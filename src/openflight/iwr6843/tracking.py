"""Ball detection + range-walk tracking on raw L3 dumps.

The chain that found the ball on every swing this weekend:

1. TDM split + range FFT + MTI (subtract each bin's mean over the loops in a
   burst) — static clutter cancels; the static argmax NEVER sees the ball.
2. Per-loop peak detections inside meter-gates that exclude the golfer blob.
3. RANSAC line fit of range vs time = the unambiguous ball speed. Doppler is
   useless here: at ~90 us loop PRI a ~50 m/s ball aliases to walking pace.

Ring slots stream in memory order; the header's ``trigger_frame`` (fw v2+)
gives the true time order and is treated as authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LOOP_PRI_S = 90e-6  # TX1-to-next-TX1 loop period for the 2TX config
RANGE_SPAN_M = 6.0  # every cfg keeps a 6 m span: bin = 6.0/n_samples
BALL_GATES_M = ((2.25, 3.75), (3.75, 5.5))
SPEED_BOUNDS_MS = (20.0, 90.0)
# fastest-credible selection: slow objects (flying tee ~25 m/s, post-impact
# clubhead ~0.7x ball) outlast the ball in the gates and win most-inliers
# RANSAC. 2026-07-14 TrackMan session: 5 driver + several SW live tracks
# stolen this way. A fast track wins if it has enough support of its own.
FAST_TRACK_MS = 26.5  # RADIAL m/s: slowest real SW ball reads ~27.5
#                               (66 mph x cos projection); flying tee ~25
FAST_SUPPORT_FRAC = 0.55  # of the most-inliers candidate
MAX_RADIAL_ACCEL = 200.0  # m/s^2 sanity for the quadratic refit


@dataclass
class Geometry:
    """Per-dump capture geometry, derived from the dump header."""

    n_frames: int
    chirps_per_frame: int
    n_tx: int
    n_rx: int
    n_samples: int
    frame_period_s: float
    trigger_frame: int
    loop_period_s: float = LOOP_PRI_S
    range_bin_start: int = 0
    range_fft_size: int | None = None
    range_bin_starts: tuple[int, ...] | None = None
    range_bin_counts: tuple[int, ...] | None = None
    frame_time_offsets_s: tuple[float, ...] | None = None

    @property
    def n_loops(self) -> int:
        """TDM loops per frame."""
        return self.chirps_per_frame // self.n_tx

    @property
    def range_res_m(self) -> float:
        """Range bin size in meters."""
        return RANGE_SPAN_M / (self.range_fft_size or self.n_samples)

    def frame_bin_start(self, frame: int | None = None) -> int:
        """Absolute first bin for a frame, or the dump-wide start."""
        if self.range_bin_starts is None:
            return self.range_bin_start
        if frame is None:
            raise ValueError("frame is required for a windowed range dump")
        return self.range_bin_starts[frame]

    def local_bin(self, absolute_bin: int, frame: int | None = None) -> int:
        """Convert full-FFT bin coordinates into this dump's payload index."""
        return absolute_bin - self.frame_bin_start(frame)

    def frame_bin_count(self, frame: int | None = None) -> int:
        """Number of valid stored bins for one frame."""
        if self.range_bin_counts is None:
            return self.n_samples
        if frame is None:
            raise ValueError("frame is required for a variable-width range dump")
        return self.range_bin_counts[frame]

    def contains_bin(self, absolute_bin: int, margin: int = 0, frame: int | None = None) -> bool:
        """True when an absolute range bin is present in this dump payload."""
        local = self.local_bin(absolute_bin, frame)
        return margin <= local < self.frame_bin_count(frame) - margin

    def loop_time(self, frame: int, loop: int) -> float:
        """Seconds from window start for (ring-slot frame, loop)."""
        slot_order = (frame - self.trigger_frame) % self.n_frames
        frame_time = (
            self.frame_time_offsets_s[slot_order]
            if self.frame_time_offsets_s is not None
            else slot_order * self.frame_period_s
        )
        return frame_time + loop * self.loop_period_s

    @property
    def capture_duration_s(self) -> float:
        """Elapsed capture time including one base frame after the final sample."""
        if self.frame_time_offsets_s:
            return self.frame_time_offsets_s[-1] + self.frame_period_s
        return self.n_frames * self.frame_period_s


@dataclass
class BallTrack:
    """Fitted ball range-walk: range(t) = slope_bins*t + intercept_bins."""

    speed_ms: float
    slope_bins: float
    intercept_bins: float
    rms_bins: float
    n_inliers: int
    t_first: float
    t_last: float
    low_confidence: bool
    quad_bins: tuple[float, float, float] | None = None

    @property
    def speed_mph(self) -> float:
        """Ball speed in mph."""
        return self.speed_ms * 2.237

    def bin_at(self, t_s: float) -> float:
        """Predicted (fractional) range bin at time t."""
        return self.slope_bins * t_s + self.intercept_bins

    def range_at(self, t_s: float, range_res_m: float) -> float:
        """Predicted range in meters at time t."""
        return self.bin_at(t_s) * range_res_m

    def speed_ms_at(self, t_s: float, range_res_m: float) -> float:
        """LOCAL radial speed from the quadratic refit (line slope if none).

        The straight-line slope is the track-average; the ball's radial
        speed actually changes along the flight as the geometry projection
        changes. This local value is what the per-snapshot TDM Doppler
        correction needs (2026-07-15 truth-referenced finding).
        """
        if self.quad_bins is None:
            return self.speed_ms
        q2, q1, _q0 = self.quad_bins
        return (2.0 * q2 * t_s + q1) * range_res_m


def mti_filter(
    cube: np.ndarray,
    scope: str = "burst",
    *,
    range_domain: bool = False,
    geometry: Geometry | None = None,
) -> np.ndarray:
    """Raw cube [nf, cpf, nrx, ns] -> complex MTI [nf, 2(tx), loops, nrx, ns].

    Range FFT then static removal. ``scope="burst"`` subtracts each bin's
    mean over the loops of one burst — the standard filter, but it NOTCHES
    balls whose loop-to-loop phase is ~0 mod 2pi (radial speed near
    n x 26.93 m/s): the ball looks static within a burst and is removed,
    shattering the range walk (the 2026-07-14 5-iron failures).
    ``scope="window"`` subtracts the mean over ALL bursts: statics are
    constant across the full 72 ms window and still cancel, while a
    notch-speed ball moves range bins across the window and survives.
    """
    n_frames, cpf, n_rx, n_samples = cube.shape
    tdm = cube.reshape(n_frames, cpf // 2, 2, n_rx, n_samples)
    tdm = tdm.transpose(0, 2, 1, 3, 4)
    rfft = tdm if range_domain else np.fft.fft(tdm, axis=-1)
    if scope == "window" and geometry is not None and geometry.range_bin_starts is not None:
        fft_size = geometry.range_fft_size or max(
            start + geometry.frame_bin_count(frame)
            for frame, start in enumerate(geometry.range_bin_starts)
        )
        totals = np.zeros((tdm.shape[1], n_rx, fft_size), dtype=complex)
        counts = np.zeros(fft_size, dtype=float)
        for frame, start in enumerate(geometry.range_bin_starts):
            count = geometry.frame_bin_count(frame)
            stop = start + count
            totals[:, :, start:stop] += rfft[frame, ..., :count].sum(axis=1)
            counts[start:stop] += rfft.shape[2]
        counts[counts == 0] = 1.0
        means = totals / counts[None, None, :]
        out = np.zeros_like(rfft)
        for frame, start in enumerate(geometry.range_bin_starts):
            count = geometry.frame_bin_count(frame)
            stop = start + count
            out[frame, ..., :count] = rfft[frame, ..., :count] - means[:, None, :, start:stop]
        return out
    if scope == "window":
        return rfft - rfft.mean(axis=(0, 2), keepdims=True)
    return rfft - rfft.mean(axis=2, keepdims=True)


def loop_power(mti: np.ndarray) -> np.ndarray:
    """MTI residual power per loop: [nf*loops, n_samples]."""
    n_frames = mti.shape[0]
    n_loops = mti.shape[2]
    power = (np.abs(mti) ** 2).sum(axis=(1, 3))
    return power.reshape(n_frames * n_loops, mti.shape[-1])


def _detections(
    power: np.ndarray,
    geo: Geometry,
    snr_min: float = 4.0,
    max_range_m: float | None = None,
    gates_m: tuple[tuple[float, float], ...] = BALL_GATES_M,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-loop sub-bin peaks inside ``gates_m``, SNR-gated.

    ``max_range_m`` clamps the gates — set it just short of the NET so
    ball-riding-up-the-net motion never enters the track or angle fits.
    """
    n_samples = power.shape[1]
    res = geo.range_res_m
    loops_idx: list[int] = []
    bins: list[float] = []
    if geo.range_bin_starts is not None:
        n_loops = geo.n_loops
        for row_index, row in enumerate(power):
            frame = row_index // n_loops
            frame_start = geo.frame_bin_start(frame)
            frame_count = geo.frame_bin_count(frame)
            for lo_m, hi_m in gates_m:
                if max_range_m is not None:
                    hi_m = min(hi_m, max_range_m)
                if hi_m <= lo_m:
                    continue
                abs_lo = int(lo_m / res)
                abs_hi = int(hi_m / res)
                g_lo = max(abs_lo - frame_start, 0)
                g_hi = min(abs_hi - frame_start, frame_count - 2)
                if g_hi - g_lo < 3:
                    continue
                gate = row[g_lo:g_hi]
                base = np.median(gate) + 1e-12
                idx = int(np.argmax(gate))
                if gate[idx] / base <= snr_min:
                    continue
                peak_local = float(g_lo + idx)
                j = int(peak_local)
                if g_lo < j < g_hi - 1:
                    y_0, y_1, y_2 = row[j - 1], row[j], row[j + 1]
                    den = y_0 - 2 * y_1 + y_2
                    if den < 0 and abs((y_0 - y_2) / (2 * den)) < 1:
                        peak_local += (y_0 - y_2) / (2 * den)
                loops_idx.append(row_index)
                bins.append(frame_start + peak_local)
        return np.asarray(loops_idx), np.asarray(bins)

    for lo_m, hi_m in gates_m:
        if max_range_m is not None:
            hi_m = min(hi_m, max_range_m)
        if hi_m <= lo_m:
            continue
        abs_lo = int(lo_m / res)
        abs_hi = int(hi_m / res)
        g_lo = max(abs_lo - geo.range_bin_start, 0)
        g_hi = min(abs_hi - geo.range_bin_start, n_samples - 2)
        if g_hi - g_lo < 3:
            continue
        gate = power[:, g_lo:g_hi]
        base = np.median(gate, axis=1) + 1e-12
        idx = np.argmax(gate, axis=1)
        snr = gate[np.arange(len(power)), idx] / base
        for i in np.nonzero(snr > snr_min)[0]:
            peak_local = float(g_lo + idx[i])
            j = int(peak_local)
            if g_lo < j < g_hi - 1:
                y_0, y_1, y_2 = power[i, j - 1], power[i, j], power[i, j + 1]
                den = y_0 - 2 * y_1 + y_2
                if den < 0 and abs((y_0 - y_2) / (2 * den)) < 1:
                    peak_local += (y_0 - y_2) / (2 * den)
            loops_idx.append(i)
            bins.append(geo.range_bin_start + peak_local)
    return np.asarray(loops_idx), np.asarray(bins)


def find_ball(
    mti: np.ndarray,
    geo: Geometry,
    *,
    iterations: int = 2500,
    seed: int = 1,
    max_range_m: float | None = None,
    min_ball_ms: float = FAST_TRACK_MS,
    gates_m: tuple[tuple[float, float], ...] = BALL_GATES_M,
    speed_bounds_ms: tuple[float, float] = SPEED_BOUNDS_MS,
    time_window_s: tuple[float, float] | None = None,
) -> BallTrack | None:
    """RANSAC a range walk inside ``gates_m``/``speed_bounds_ms``; None when
    no plausible streak exists.

    Selection is FASTEST-CREDIBLE, not most-inliers: the best fast
    (>= ``min_ball_ms``) candidate wins whenever it has at least
    FAST_SUPPORT_FRAC of the overall best candidate's inliers.
    ``min_ball_ms`` is the slowest RADIAL speed a real ball can read for
    the club in play (see shot.CLUB_POLICY) — it must sit above the
    class's post-impact club/tee speeds or they steal the track.

    Defaults reproduce the ball search. The club estimator passes its own
    (closer, slower) ``gates_m``/``speed_bounds_ms``; because the club's
    speed band overlaps the ball's, it must also pass ``time_window_s`` to
    restrict the search to pre-impact frames — otherwise this fitter will
    happily lock onto the ball instead of the club.
    """
    power = loop_power(mti)
    loops_idx, bins = _detections(power, geo, max_range_m=max_range_m, gates_m=gates_m)
    if loops_idx.size < 8:
        return None
    res = geo.range_res_m
    n_loops = geo.n_loops
    times = np.array([geo.loop_time(i // n_loops, i % n_loops) for i in loops_idx])
    if time_window_s is not None:
        keep = (times >= time_window_s[0]) & (times <= time_window_s[1])
        if keep.sum() < 8:
            return None
        times, bins, loops_idx = times[keep], bins[keep], loops_idx[keep]
    tol = 1.2 if geo.n_samples >= 128 else 0.8
    rng = np.random.default_rng(seed)
    best: tuple[int, float, float, float, float, float] | None = None  # most inliers at any speed
    best_fast: tuple[int, float, float, float, float, float] | None = (
        None  # most inliers among fast candidates
    )
    for _ in range(iterations):
        i, j = rng.choice(times.size, 2, replace=False)
        d_t = times[i] - times[j]
        if abs(d_t) < 3e-3:
            continue
        slope = (bins[i] - bins[j]) / d_t
        if not speed_bounds_ms[0] <= slope * res <= speed_bounds_ms[1]:
            continue
        icpt = bins[i] - slope * times[i]
        inliers = np.abs(bins - (slope * times + icpt)) < tol
        n_new = int(inliers.sum())
        if n_new < 8:
            continue
        beats_best = best is None or n_new > best[0]  # pylint: disable=unsubscriptable-object
        beats_fast = slope * res >= min_ball_ms and (
            best_fast is None or n_new > best_fast[0]  # pylint: disable=unsubscriptable-object
        )
        if not (beats_best or beats_fast):
            continue
        design = np.vstack([times[inliers], np.ones(n_new)]).T
        (sl2, ic2), *_ = np.linalg.lstsq(design, bins[inliers], rcond=None)
        if not speed_bounds_ms[0] <= sl2 * res <= speed_bounds_ms[1]:
            continue
        resid = bins[inliers] - (sl2 * times[inliers] + ic2)
        rms = float(np.sqrt((resid**2).mean()))
        cand = (n_new, sl2, ic2, rms, float(times[inliers].min()), float(times[inliers].max()))
        if beats_best:
            best = cand
        if sl2 * res >= min_ball_ms and (
            best_fast is None or n_new > best_fast[0]  # pylint: disable=unsubscriptable-object
        ):
            best_fast = cand
    if best is None:
        return None
    pick = best
    if (
        best_fast is not None
        and best[1] * res < min_ball_ms
        and best_fast[0] >= FAST_SUPPORT_FRAC * best[0]
    ):
        pick = best_fast
    n_inl, slope, icpt, rms, t_first, t_last = pick
    # quadratic refit of the picked track's inliers: local radial speed
    inl = np.abs(bins - (slope * times + icpt)) < tol
    quad = None
    if inl.sum() >= 10:
        q2, q1, q0 = np.polyfit(times[inl], bins[inl], 2)
        if abs(2.0 * q2 * res) < MAX_RADIAL_ACCEL:
            quad = (float(q2), float(q1), float(q0))
    span_s = t_last - t_first
    return BallTrack(
        speed_ms=slope * res,
        slope_bins=slope,
        intercept_bins=icpt,
        rms_bins=rms,
        n_inliers=n_inl,
        t_first=t_first,
        t_last=t_last,
        low_confidence=bool(rms >= 0.45 or span_s < 0.012),
        quad_bins=quad,
    )
