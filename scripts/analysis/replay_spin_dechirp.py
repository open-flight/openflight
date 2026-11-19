#!/usr/bin/env python3
"""Replay raw rolling-buffer captures through a dechirped Doppler-sideband
spin estimator and score it against TrackMan truth alongside the production
envelope detector.

Offline only — no production behavior changes. Scaffold for the "dechirped
coherent sideband spectrum" approach (TrackMan-style, single window):

1. Track the ball's Doppler carrier f_d(t) with a short-window STFT.
   The ball decelerates ~3-4 kHz/s, smearing the carrier (and every
   sideband) ~200 Hz over a 60 ms window — fatal for resolving sideband
   spacings of 42-167 Hz (2500-10000 RPM) in a plain FFT.
2. Mix the raw I/Q by the conjugate chirp so the carrier becomes a
   stationary tone at 0 Hz and spin sidebands sit at exactly +/- m*f_mod.
3. Search a harmonic comb over candidate modulation frequencies, scoring
   symmetric sideband-pair support in the dechirped spectrum.
4. Disambiguate the harmonic number: the seam's 2-fold symmetry
   modulates at 2x spin "more often than not" (FlightScope US9868044),
   while a logo/asymmetry modulates at 1x. Both interpretations are
   emitted; odd-harmonic support at 1.5x the comb fundamental indicates
   the true fundamental is half the observed spacing.

Usage:
    uv run --no-sync python scripts/analysis/replay_spin_dechirp.py \
        --openflight session_logs/session_20260511_120001_range.jsonl \
        --comparison session_logs/comparison_test2.csv \
        --output session_logs/spin_dechirp_replay_test2.csv

Full reference (requirements, pipeline, baseline results, next steps):
docs/development/spin-replay.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_trackman as ct  # noqa: E402  pylint: disable=wrong-import-position
from experiment_spin_windows import (  # noqa: E402
    _load_session_entries,
    _load_trackman_by_shot,
    _to_int,
)

from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402

SAMPLE_RATE = 30000
WAVELENGTH_M = 0.01243
MPS_TO_MPH = 2.23694

# Carrier tracking
STFT_WINDOW = 256  # ~8.5 ms — 117 Hz resolution, enough to track the carrier
STFT_STEP = 64  # ~2.1 ms hop
TRACK_TOLERANCE_HZ = 900  # search window around expected ball Doppler
TRACK_MIN_SNR = 3.0  # STFT peak vs frame median to count as "carrier present"

# Sideband search (after dechirp, carrier at 0 Hz)
SIDEBAND_FFT_SIZE = 1 << 17  # zero-padded for fine comb sampling
MOD_MIN_HZ = 33.0  # 2000 RPM at 1x
MOD_MAX_HZ = 400.0  # 12000 RPM at 2x seam modulation
COMB_STEP_HZ = 0.5
COMB_HARMONICS = (1.0, 2.0, 3.0)
COMB_HARMONIC_WEIGHTS = (1.0, 0.6, 0.35)
ODD_HARMONIC_RATIO = 0.35  # support at 1.5x fundamental => spacing is really f/2
MIN_USABLE_MS = 25.0
SPIN_PLAUSIBLE_RPM = (1500.0, 13000.0)


@dataclass
class CarrierTrack:
    """Quadratic fit of the ball Doppler carrier over its usable window."""

    poly: np.ndarray  # np.polyfit coeffs of f_d(t) in Hz vs seconds
    start_sample: int
    end_sample: int

    @property
    def usable_ms(self) -> float:
        return (self.end_sample - self.start_sample) / SAMPLE_RATE * 1000


def track_carrier(
    iq: np.ndarray,
    ball_speed_mph: float,
    onset_sample: int,
) -> Optional[CarrierTrack]:
    """Fit f_d(t) from STFT peaks near the expected ball Doppler.

    Also determines the usable window: the contiguous run of STFT frames
    (starting at ball onset) whose carrier peak stays above TRACK_MIN_SNR.
    """
    expected_hz = 2 * (ball_speed_mph / MPS_TO_MPH) / WAVELENGTH_M
    window = np.hanning(STFT_WINDOW)
    freqs = np.fft.fftfreq(STFT_WINDOW, d=1 / SAMPLE_RATE)
    band = (np.abs(freqs - expected_hz) <= TRACK_TOLERANCE_HZ) & (freqs > 0)
    if not band.any():
        return None

    times, peaks, mags = [], [], []
    end_of_track = onset_sample
    for start in range(onset_sample, len(iq) - STFT_WINDOW, STFT_STEP):
        seg = iq[start : start + STFT_WINDOW] * window
        spec = np.abs(np.fft.fft(seg))
        floor = float(np.median(spec[spec > 0])) or 1.0
        in_band = spec[band]
        peak_idx = int(np.argmax(in_band))
        snr = float(in_band[peak_idx]) / floor
        if snr < TRACK_MIN_SNR:
            # Carrier lost (net impact / out of range): stop at the first
            # loss after we have accumulated some track.
            if len(times) >= 5:
                break
            continue
        times.append((start + STFT_WINDOW / 2) / SAMPLE_RATE)
        peaks.append(float(freqs[band][peak_idx]))
        mags.append(float(in_band[peak_idx]))
        end_of_track = start + STFT_WINDOW

    if len(times) < 5:
        return None
    t = np.array(times)
    f = np.array(peaks)
    w = np.sqrt(np.array(mags))
    order = 2 if len(times) >= 8 else 1
    poly = np.polyfit(t, f, order, w=w)
    return CarrierTrack(poly=poly, start_sample=onset_sample, end_sample=end_of_track)


def dechirp(iq: np.ndarray, track: CarrierTrack) -> np.ndarray:
    """Mix the usable window by the conjugate carrier chirp (carrier -> 0 Hz)."""
    segment = iq[track.start_sample : track.end_sample]
    t = (track.start_sample + np.arange(len(segment))) / SAMPLE_RATE
    phase = 2 * np.pi * np.polyval(np.polyint(track.poly), t)
    return segment * np.exp(-1j * phase)


@dataclass
class SidebandEstimate:
    mod_freq_hz: float  # fundamental sideband spacing found by the comb
    comb_score: float  # harmonic-comb support (vs noise floor)
    odd_support: float  # support at 1.5x fundamental (harmonic-number evidence)
    spin_rpm_1x: float  # spin if the modulating feature is 1x (logo)
    spin_rpm_2x: float  # spin if the modulation is the 2x seam
    best_rpm: float  # disambiguated pick
    harmonic_choice: str  # "1x" | "2x"


def sideband_spin(baseband: np.ndarray) -> Optional[SidebandEstimate]:
    """Find the spin modulation frequency as symmetric sidebands around 0 Hz."""
    windowed = baseband * np.hanning(len(baseband))
    spectrum = np.abs(np.fft.fft(windowed, SIDEBAND_FFT_SIZE))

    df = SAMPLE_RATE / SIDEBAND_FFT_SIZE
    half_cell_bins = max(1, int(round(SAMPLE_RATE / len(baseband) / 2 / df)))
    # Local floor window: wide enough to estimate the carrier-skirt level
    # at that offset, excluding the sideband cell itself.
    floor_bins = 8 * half_cell_bins

    def side_snr(spectrum_half: np.ndarray, k: int) -> float:
        cell = spectrum_half[k - half_cell_bins : k + half_cell_bins + 1]
        lo = max(1, k - floor_bins)
        hi = min(len(spectrum_half) - 1, k + floor_bins)
        ring = np.concatenate(
            [
                spectrum_half[lo : k - half_cell_bins],
                spectrum_half[k + half_cell_bins + 1 : hi],
            ]
        )
        ring = ring[ring > 0]
        if cell.size == 0 or ring.size < 4:
            return 0.0
        # The dechirped carrier's skirt slopes steeply: normalize each
        # sideband by its own neighborhood, not a global floor, or the
        # lowest candidate frequency always wins.
        return float(cell.max()) / float(np.median(ring))

    upper_half = spectrum[: SIDEBAND_FFT_SIZE // 2]
    lower_half = spectrum[SIDEBAND_FFT_SIZE // 2 :][::-1]  # mirrored negative freqs

    def mag_at(freq_hz: float) -> tuple[float, float]:
        # local-floor SNR within half a natural-resolution cell of +/-freq_hz
        k = int(round(freq_hz / df))
        if k - floor_bins < 1 or k + floor_bins >= SIDEBAND_FFT_SIZE // 2:
            return 0.0, 0.0
        # lower_half[j] = spectrum[N-1-j] = bin at -(j+1)*df, so -k*df is j=k-1
        return side_snr(upper_half, k), side_snr(lower_half, k - 1)

    candidates = np.arange(MOD_MIN_HZ, MOD_MAX_HZ, COMB_STEP_HZ)
    scores = np.zeros(len(candidates))
    for i, f0 in enumerate(candidates):
        score = 0.0
        for harmonic, weight in zip(COMB_HARMONICS, COMB_HARMONIC_WEIGHTS):
            upper, lower = mag_at(f0 * harmonic)
            # symmetric-pair support: both sidebands must be present
            score += weight * min(upper, lower)
        scores[i] = score

    best = int(np.argmax(scores))
    f_mod = float(candidates[best])
    comb_score = float(scores[best])
    if comb_score <= 0:
        return None

    # Harmonic-number disambiguation: if there is sideband support at
    # 1.5x f_mod (an odd multiple of f_mod/2), the true fundamental is
    # f_mod/2 and f_mod was its second harmonic.
    upper, lower = mag_at(1.5 * f_mod)
    odd_support = min(upper, lower)

    spin_1x = f_mod * 60.0
    spin_2x = f_mod / 2.0 * 60.0
    if odd_support >= ODD_HARMONIC_RATIO * comb_score:
        choice, best_rpm = "2x", spin_2x
    else:
        # Default to 1x when the comb fundamental itself is plausible
        # spin; prefer 2x when 1x would be implausibly high.
        if spin_1x > SPIN_PLAUSIBLE_RPM[1] and SPIN_PLAUSIBLE_RPM[0] <= spin_2x:
            choice, best_rpm = "2x", spin_2x
        else:
            choice, best_rpm = "1x", spin_1x

    return SidebandEstimate(
        mod_freq_hz=f_mod,
        comb_score=comb_score,
        odd_support=float(odd_support),
        spin_rpm_1x=spin_1x,
        spin_rpm_2x=spin_2x,
        best_rpm=best_rpm,
        harmonic_choice=choice,
    )


def replay_shot(
    processor: RollingBufferProcessor,
    capture: IQCapture,
) -> tuple[Optional[Any], Optional[SidebandEstimate], dict[str, Any]]:
    """Run production processing and the dechirped estimator on one capture."""
    processed = processor.process_capture(capture)
    diag: dict[str, Any] = {}
    if not processed:
        return None, None, diag

    i_data = np.array(capture.i_samples, dtype=np.float64)
    q_data = np.array(capture.q_samples, dtype=np.float64)
    iq = (i_data - i_data.mean()) + 1j * (q_data - q_data.mean())

    onset = max(0, int(processed.ball_timestamp_ms * SAMPLE_RATE / 1000))
    track = track_carrier(iq, processed.ball_speed_mph, onset)
    if track is None:
        diag["dechirp_skip"] = "no_carrier_track"
        return processed, None, diag

    diag["usable_ms"] = round(track.usable_ms, 1)
    diag["chirp_hz_per_s"] = round(float(track.poly[-2]) if len(track.poly) >= 2 else 0.0)
    if track.usable_ms < MIN_USABLE_MS:
        diag["dechirp_skip"] = f"usable_window_{track.usable_ms:.0f}ms"
        return processed, None, diag

    estimate = sideband_spin(dechirp(iq, track))
    return processed, estimate, diag


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--openflight", type=Path, required=True, help="Session JSONL")
    parser.add_argument("--comparison", type=Path, required=True, help="TrackMan CSV")
    parser.add_argument("--output", type=Path, required=True, help="Per-shot output CSV")
    args = parser.parse_args()

    shots, captures = _load_session_entries(args.openflight)
    trackman_by_shot = _load_trackman_by_shot(args.comparison)
    processor = RollingBufferProcessor(sample_rate=SAMPLE_RATE)

    rows: list[dict[str, Any]] = []
    for shot_entry, capture_entry in zip(shots, captures):
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = _to_int(shot_data.get("shot_number"))
        truth = trackman_by_shot.get(shot_number or -1, {})
        if truth.get("match_quality") != "good" or truth.get("spin_tm") is None:
            continue

        capture = IQCapture(
            sample_time=capture_entry.get("sample_time", 0),
            trigger_time=capture_entry.get("trigger_time", 0),
            i_samples=capture_entry["i_samples"],
            q_samples=capture_entry["q_samples"],
        )
        processed, estimate, diag = replay_shot(processor, capture)
        if not processed:
            continue

        spin_tm = truth["spin_tm"]
        baseline = processed.spin
        row: dict[str, Any] = {
            "shot_number": shot_number,
            "club": ct.normalize_club(shot_data.get("club")),
            "spin_tm": spin_tm,
            "ball_speed_of": round(processed.ball_speed_mph, 1),
            "baseline_rpm": baseline.spin_rpm if baseline and baseline.spin_rpm else None,
            "baseline_quality": baseline.quality if baseline else None,
            "baseline_snr": baseline.snr if baseline else None,
            "usable_ms": diag.get("usable_ms"),
            "chirp_hz_per_s": diag.get("chirp_hz_per_s"),
            "dechirp_skip": diag.get("dechirp_skip"),
        }
        if estimate:
            row.update(
                {
                    "dechirp_rpm": round(estimate.best_rpm),
                    "dechirp_choice": estimate.harmonic_choice,
                    "dechirp_rpm_1x": round(estimate.spin_rpm_1x),
                    "dechirp_rpm_2x": round(estimate.spin_rpm_2x),
                    "dechirp_score": round(estimate.comb_score, 2),
                    "dechirp_odd_support": round(estimate.odd_support, 2),
                }
            )
        rows.append(row)

    if not rows:
        print("No paired shots with TrackMan spin truth found.")
        return

    fieldnames = sorted({key for row in rows for key in row})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    _print_summary(rows)
    print(f"\nPer-shot rows written to {args.output}")


def _summarize(label: str, pairs: list[tuple[float, float]], total: int) -> None:
    if not pairs:
        print(f"{label:28s} coverage 0/{total}")
        return
    errors = [abs(measured - truth) for measured, truth in pairs]
    pct = [abs(m - t) / t * 100 for m, t in pairs if t > 0]
    within10 = sum(1 for p in pct if p <= 10)
    print(
        f"{label:28s} coverage {len(pairs)}/{total}  "
        f"MAE {statistics.mean(errors):6.0f} rpm  "
        f"median {statistics.median(errors):6.0f} rpm  "
        f"within10% {within10}/{len(pairs)}"
    )


def _print_summary(rows: list[dict[str, Any]]) -> None:
    total = len(rows)
    baseline_pairs = [
        (row["baseline_rpm"], row["spin_tm"]) for row in rows if row.get("baseline_rpm")
    ]
    dechirp_pairs = [(row["dechirp_rpm"], row["spin_tm"]) for row in rows if row.get("dechirp_rpm")]
    # Oracle: the better of the 1x/2x interpretations per shot — the
    # ceiling a perfect harmonic-disambiguation rule could reach.
    oracle_pairs = []
    for row in rows:
        if row.get("dechirp_rpm_1x"):
            truth = row["spin_tm"]
            best = min(
                (row["dechirp_rpm_1x"], row["dechirp_rpm_2x"]),
                key=lambda rpm: abs(rpm - truth),
            )
            oracle_pairs.append((best, truth))

    print(f"\n=== Spin replay summary ({total} TrackMan-paired shots) ===")
    _summarize("production envelope", baseline_pairs, total)
    _summarize("dechirped sidebands", dechirp_pairs, total)
    _summarize("dechirp + harmonic oracle", oracle_pairs, total)


if __name__ == "__main__":
    main()
