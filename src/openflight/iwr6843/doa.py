"""Direction-of-arrival on the 8-element virtual elevation array.

Snapshot chain (hardware-validated 2026-07-12/13):
1. Extract the 2TXx4RX complex values at the ball's range bin from MTI data.
2. TDM Doppler correction — the second chirp trails the first by 45 us, so a
   moving ball rotates that block by 4*pi*v*tau/lambda. v comes from the
   (unambiguous) range walk; the SIGN convention is either locked by the
   capture protocol or measured once per shot from loop-to-loop phase.
3. Restore the configured chirp order to the calibration's physical TX order,
   apply the orientation flip (RX order is reversed on this board), and apply
   corner-reflector calibration (element phases/gains incl. TX block offset).
4. Angle per snapshot via FBSS-MUSIC with the higher-peak rule (the floor
   image always sits below the direct path), cross-checked against Bartlett.

Coherent integration: consecutive loops within a burst see the ball at
essentially one position, so after motion compensation (the same Doppler
phase used for TDM, applied per loop) they can be summed coherently for
~10*log10(K) dB of SNR before estimation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.music import LAM, est_bartlett, est_music_fbss_high
from openflight.iwr6843.tracking import BallTrack, Geometry

TDM_TAU_S = 45e-6  # first -> second chirp offset inside one loop
TX2_VERTICAL_TDM_TAU_S = 2 * TDM_TAU_S  # TX1 -> TX3 offset in the 3-TX loop
# IWR6843LEVM antenna geometry (SWRU585): TX2 is displaced D=lambda/2
# from the TX1/TX3 phase center on the board's orthogonal axis. The OpenFlight
# enclosure rotates that axis into horizontal, but rotation does not change
# the electrical baseline length.
LEVM_TX2_AXIS_BASELINE_LAMBDA = 0.5
TX_ORDERS = frozenset({"normal", "reversed"})
TDM_SIGN_POLICIES = frozenset({"auto", "negative", "positive"})


def tx2_phase_to_axis_angle_rad(
    phase_rad: float | np.ndarray,
    *,
    orthogonal_angle_rad: float | np.ndarray = 0.0,
) -> float | np.ndarray:
    """Convert LEVM TX2 residual phase to its displaced-axis angle.

    The measured phase is ``2*pi*(D/lambda)*direction_cosine``. For the
    LEVM's ``D=lambda/2`` TX2 baseline this becomes ``pi*direction_cosine``.
    ``orthogonal_angle_rad`` optionally removes the other axis' projection;
    leaving it at zero returns the usual one-axis direction-cosine proxy.
    """
    phase = np.asarray(phase_rad, dtype=float)
    orthogonal = np.asarray(orthogonal_angle_rad, dtype=float)
    projection = np.cos(orthogonal)
    if np.any(np.abs(projection) < 1e-6):
        raise ValueError("orthogonal angle is too close to 90 degrees")
    axis_sine = phase / (2.0 * np.pi * LEVM_TX2_AXIS_BASELINE_LAMBDA * projection)
    angle = np.arcsin(np.clip(axis_sine, -1.0, 1.0))
    return float(angle) if angle.ndim == 0 else angle


def tx2_axis_angle_to_phase_rad(
    angle_rad: float | np.ndarray,
    *,
    orthogonal_angle_rad: float | np.ndarray = 0.0,
) -> float | np.ndarray:
    """Inverse of :func:`tx2_phase_to_axis_angle_rad` for LEVM geometry."""
    angle = np.asarray(angle_rad, dtype=float)
    orthogonal = np.asarray(orthogonal_angle_rad, dtype=float)
    phase = 2.0 * np.pi * LEVM_TX2_AXIS_BASELINE_LAMBDA * np.sin(angle) * np.cos(orthogonal)
    return float(phase) if phase.ndim == 0 else phase


def validate_tx_order(tx_order: str) -> str:
    """Validate the chirp order used to acquire a dump."""
    if tx_order not in TX_ORDERS:
        choices = ", ".join(sorted(TX_ORDERS))
        raise ValueError(f"tx_order must be one of: {choices}")
    return tx_order


def validate_tdm_sign_policy(policy: str) -> str:
    """Validate how the per-shot Doppler phase sign is selected."""
    if policy not in TDM_SIGN_POLICIES:
        choices = ", ".join(sorted(TDM_SIGN_POLICIES))
        raise ValueError(f"tdm_sign_policy must be one of: {choices}")
    return policy


@dataclass
class AnglePoint:
    """One angle measurement along the ball's flight (radar frame)."""

    t_s: float
    range_m: float  # bias-corrected slant range
    theta_rad: float  # elevation relative to boresight
    snr: float
    n_summed: int  # loops coherently combined into this point


def measure_tdm_sign(mti: np.ndarray, track: BallTrack, geo: Geometry) -> int:
    """Resolve the Doppler phase sign by comparing measured loop-to-loop
    phase at the track bins against the range-walk prediction."""
    acc = 0j
    for frame in range(geo.n_frames):
        for loop in range(geo.n_loops - 1):
            t_s = geo.loop_time(frame, loop)
            b_0 = int(round(track.bin_at(t_s)))
            b_1 = int(round(track.bin_at(t_s + geo.loop_period_s)))
            b_0_local = geo.local_bin(b_0, frame)
            b_1_local = geo.local_bin(b_1, frame)
            if geo.contains_bin(b_0, frame=frame) and geo.contains_bin(b_1, frame=frame):
                acc += np.vdot(
                    mti[frame, 0, loop, :, b_0_local],
                    mti[frame, 0, loop + 1, :, b_1_local],
                )
    if abs(acc) == 0:
        return +1
    psi_pred = 4 * np.pi * track.speed_ms * geo.loop_period_s / LAM

    def circ_dist(a: float, b: float) -> float:
        return abs(np.angle(np.exp(1j * (a - b))))

    measured = float(np.angle(acc))
    return +1 if circ_dist(measured, psi_pred) <= circ_dist(measured, -psi_pred) else -1


def resolve_tdm_sign(policy: str, mti: np.ndarray, track: BallTrack, geo: Geometry) -> int:
    """Resolve a policy to the single sign used throughout one shot."""
    policy = validate_tdm_sign_policy(policy)
    if policy == "positive":
        return +1
    if policy == "negative":
        return -1
    return measure_tdm_sign(mti, track, geo)


def canonicalize_tx_blocks(
    early: np.ndarray,
    late: np.ndarray,
    *,
    tdm_phase: float,
    tx_order: str,
) -> np.ndarray:
    """Doppler-correct and restore chirps to the calibrated physical array order."""
    tx_order = validate_tx_order(tx_order)
    late_corrected = late * np.exp(-1j * tdm_phase)
    blocks = (early, late_corrected) if tx_order == "normal" else (late_corrected, early)
    return np.concatenate(blocks, axis=0)[::-1]


def later_physical_tx_index(tx_order: str) -> int:
    """Return the calibrated TX block that was transmitted second."""
    return 0 if validate_tx_order(tx_order) == "normal" else 1


def _snapshot(
    mti: np.ndarray,
    frame: int,
    loop: int,
    rbin: int,
    tdm_phase: float,
    cal: Calibration,
    tx_order: str,
) -> np.ndarray:
    """One calibrated 8-element snapshot in physical orientation."""
    early = mti[frame, 0, loop, :, rbin]
    late = mti[frame, 1, loop, :, rbin]
    snap = canonicalize_tx_blocks(
        early,
        late,
        tdm_phase=tdm_phase,
        tx_order=tx_order,
    )
    return cal.apply(snap)


def snapshot_series(
    mti: np.ndarray,
    track: BallTrack,
    geo: Geometry,
    cal: Calibration,
    *,
    coherent_loops: int = 1,
    snr_min: float = 8.0,
    tx_order: str = "normal",
    tdm_sign: int | None = None,
    tdm_tau_s: float = TDM_TAU_S,
) -> list[tuple[float, float, np.ndarray, float]]:
    """Calibrated snapshots along the fitted track.

    Returns [(t_mid_s, range_m_bias_corrected, 8-el snapshot, snr), ...].
    ``coherent_loops=K`` motion-compensates and sums K consecutive loops
    (~10*log10(K) dB gain); K=1 is the raw per-loop series.
    """
    tx_order = validate_tx_order(tx_order)
    if tdm_sign is None:
        tdm_sign = measure_tdm_sign(mti, track, geo)
    if tdm_sign not in (-1, +1):
        raise ValueError("tdm_sign must be -1 or +1")
    noise = max(float(np.median(np.abs(mti) ** 2)), 1e-12)
    k = max(1, int(coherent_loops))
    out: list[tuple[float, float, np.ndarray, float]] = []
    for frame in range(geo.n_frames):
        for start in range(0, geo.n_loops - k + 1, k):
            t_mid = geo.loop_time(frame, start + (k - 1) / 2.0)
            if not track.t_first - 2e-3 <= t_mid <= track.t_last + 2e-3:
                continue
            # LOCAL radial speed (quadratic track): the ball's radial speed
            # changes along the flight; using the track-average leaves a
            # TX-block phase residual that grows toward the track ends
            # (TrackMan-truth finding, 2026-07-15)
            v_r = track.speed_ms_at(t_mid, geo.range_res_m)
            tdm_phase = tdm_sign * 4 * np.pi * v_r * tdm_tau_s / LAM
            loop_phase = tdm_sign * 4 * np.pi * v_r * geo.loop_period_s / LAM
            acc = np.zeros(8, dtype=complex)
            for off in range(k):
                loop = start + off
                t_s = geo.loop_time(frame, loop)
                rbin = int(round(track.bin_at(t_s)))
                local_bin = geo.local_bin(rbin, frame)
                if not geo.contains_bin(rbin, margin=1, frame=frame):
                    break
                snap = _snapshot(mti, frame, loop, local_bin, tdm_phase, cal, tx_order)
                acc += snap * np.exp(-1j * loop_phase * off)
            else:
                snr = float((np.abs(acc) ** 2).mean() / (noise * k))
                # STRICT full-gain gate, kept deliberately: on 2026-07-13
                # real shots the two-ray solver worked best on few
                # ultra-clean snapshots (7/8 fits at +/-2.1 deg); relaxing
                # to sqrt(k) admitted clutter that failed the two-source
                # model and starved the fits instead of feeding them
                if snr < snr_min * k:
                    continue
                rng_m = cal.true_range(track.range_at(t_mid, geo.range_res_m))
                out.append((t_mid, rng_m, acc, snr))
    return out


def angle_points(
    mti: np.ndarray,
    track: BallTrack,
    geo: Geometry,
    cal: Calibration,
    *,
    coherent_loops: int = 1,
    snr_min: float = 8.0,
    tx_order: str = "normal",
    tdm_sign: int | None = None,
    tdm_tau_s: float = TDM_TAU_S,
    agreement_max_rad: float = np.radians(8.0),
) -> list[AnglePoint]:
    """Per-point elevation estimates along the fitted ball track.

    MUSIC (higher-peak rule) per snapshot, gated on SNR and MUSIC/Bartlett
    agreement (a multipath-corruption tell).
    """
    series = snapshot_series(
        mti,
        track,
        geo,
        cal,
        coherent_loops=coherent_loops,
        snr_min=snr_min,
        tx_order=tx_order,
        tdm_sign=tdm_sign,
        tdm_tau_s=tdm_tau_s,
    )
    noise = float(np.median(np.abs(mti) ** 2))
    k = max(1, int(coherent_loops))
    points: list[AnglePoint] = []
    for t_mid, rng_m, snap, snr in series:
        theta = est_music_fbss_high(snap, noise * k)
        if abs(theta - est_bartlett(snap)) > agreement_max_rad:
            continue
        points.append(
            AnglePoint(t_s=t_mid, range_m=rng_m, theta_rad=float(theta), snr=snr, n_summed=k)
        )
    return points


def circular_median(values: list[float]) -> float:
    """Median of angles, wrapping correctly across +/-pi."""
    array = np.asarray(values, dtype=float)
    # Score every observed angle as the candidate in one small matrix. The
    # horizontal estimators call this hundreds of times per shot (usually for
    # four RX phases); constructing a separate NumPy pipeline per candidate
    # made this otherwise tiny robust statistic a measurable hot path.
    wrapped_distances = np.abs(np.angle(np.exp(1j * (array[:, np.newaxis] - array[np.newaxis, :]))))
    scores = np.median(wrapped_distances, axis=0)
    return float(array[int(np.argmin(scores))])


def tx2_phase_at(
    tdm: np.ndarray,
    frame: int,
    loop: int,
    local_bin: int,
    *,
    velocity_ms: float,
    tdm_sign: int,
    n_rx: int,
) -> tuple[float, float] | None:
    """TX2-vs-(TX1,TX3) phase at one (frame, loop, bin), motion corrected.

    ``tdm`` is the TDM-split cube [frames, loops, n_tx, n_rx, bins]. Returns
    (phase_rad, weight) or None when the bin carries no usable amplitude.
    Weight is the mean power of the reference and TX2 vectors, for weighted
    circular averaging by the caller.
    """
    tx1 = tdm[frame, loop, 0, :, local_bin]
    tx2 = tdm[frame, loop, 1, :, local_bin] * np.exp(
        -1j * tdm_sign * 4.0 * np.pi * velocity_ms * TDM_TAU_S / LAM
    )
    tx3 = tdm[frame, loop, 2, :, local_bin] * np.exp(
        -1j * tdm_sign * 4.0 * np.pi * velocity_ms * TX2_VERTICAL_TDM_TAU_S / LAM
    )
    reference = 0.5 * (tx1 + tx3)
    weight = float(np.mean(np.abs(reference) ** 2 + np.abs(tx2) ** 2))
    if weight <= 0:
        return None
    phases = [
        float(np.angle(np.conj(reference[rx]) * tx2[rx]))
        for rx in range(n_rx)
        if abs(reference[rx]) * abs(tx2[rx]) > 0
    ]
    if not phases:
        return None
    return circular_median(phases), weight


def tx2_reference_phases_at(
    tdm: np.ndarray,
    frame: int,
    loop: int,
    local_bin: int,
    *,
    velocity_ms: float,
    tdm_sign: int,
    n_rx: int,
) -> tuple[float, float, float] | None:
    """Separate TX2-vs-TX1 and TX2-vs-TX3 phases for temporal fusion.

    Unlike :func:`tx2_phase_at`, this does not average the two vertical
    reference voltages. Their unequal gains and elevation-dependent phase can
    cancel in voltage space. Keeping both normalized phase differences lets
    the experimental club-path estimator unwrap each through time before
    averaging their motion, avoiding the 180-degree midpoint branch flips
    seen on real V6 captures.
    """
    tx1 = tdm[frame, loop, 0, :, local_bin]
    tx2 = tdm[frame, loop, 1, :, local_bin] * np.exp(
        -1j * tdm_sign * 4.0 * np.pi * velocity_ms * TDM_TAU_S / LAM
    )
    tx3 = tdm[frame, loop, 2, :, local_bin] * np.exp(
        -1j * tdm_sign * 4.0 * np.pi * velocity_ms * TX2_VERTICAL_TDM_TAU_S / LAM
    )
    phase_tx1 = [
        float(np.angle(np.conj(tx1[rx]) * tx2[rx]))
        for rx in range(n_rx)
        if abs(tx1[rx]) * abs(tx2[rx]) > 0
    ]
    phase_tx3 = [
        float(np.angle(np.conj(tx3[rx]) * tx2[rx]))
        for rx in range(n_rx)
        if abs(tx3[rx]) * abs(tx2[rx]) > 0
    ]
    if not phase_tx1 or not phase_tx3:
        return None
    weight = float(np.mean(np.abs(tx2) * np.sqrt(np.maximum(np.abs(tx1) * np.abs(tx3), 0.0))))
    return circular_median(phase_tx1), circular_median(phase_tx3), weight
