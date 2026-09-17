#!/usr/bin/env python3
"""Sweep spin-analysis windows against a TrackMan comparison session.

This is intentionally offline-only. It reuses the production peak picker and
quality gates, but tries alternative ball-envelope windows before we change live
spin behavior.

Usage:
    uv run --no-sync python scripts/analysis/experiment_spin_windows.py \
        --openflight session_logs/session_20260511_120001_range.jsonl \
        --comparison session_logs/comparison_test2.csv \
        --output session_logs/spin_window_experiment_test2.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
from scipy.signal import butter, sosfiltfilt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_trackman as ct  # noqa: E402  pylint: disable=wrong-import-position

from openflight.clubs import ClubType  # noqa: E402
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH  # noqa: E402
from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import IQCapture, SpinResult  # noqa: E402


@dataclass(frozen=True)
class WindowSpec:
    """A proposed analysis window in samples, relative to ball onset."""

    name: str
    start_sample: int
    end_sample: int

    @property
    def sample_count(self) -> int:
        return max(0, self.end_sample - self.start_sample)


def _club_enum(normalized_club: str) -> ClubType:
    aliases = {
        "driver": ClubType.DRIVER,
        "3-wood": ClubType.WOOD_3,
        "5-wood": ClubType.WOOD_5,
        "7-wood": ClubType.WOOD_7,
        "3-hybrid": ClubType.HYBRID_3,
        "5-hybrid": ClubType.HYBRID_5,
        "7-hybrid": ClubType.HYBRID_7,
        "9-hybrid": ClubType.HYBRID_9,
        "2-iron": ClubType.IRON_2,
        "3-iron": ClubType.IRON_3,
        "4-iron": ClubType.IRON_4,
        "5-iron": ClubType.IRON_5,
        "6-iron": ClubType.IRON_6,
        "7-iron": ClubType.IRON_7,
        "8-iron": ClubType.IRON_8,
        "9-iron": ClubType.IRON_9,
        "pw": ClubType.PW,
        "gw": ClubType.GW,
        "sw": ClubType.SW,
        "lw": ClubType.LW,
    }
    return aliases.get(normalized_club, ClubType.UNKNOWN)


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _load_session_entries(path: Path) -> tuple[list[dict], list[dict]]:
    shots = []
    captures = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("type") == "shot_detected":
                shots.append(entry)
            elif entry.get("type") == "rolling_buffer_capture":
                captures.append(entry)
    return shots, captures


def _load_trackman_by_shot(comparison_path: Path) -> dict[int, dict[str, Any]]:
    by_shot = {}
    with comparison_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            shot_number = _to_int(row.get("shot_number_of"))
            if shot_number is None:
                continue
            by_shot[shot_number] = {
                "match_quality": row.get("match_quality"),
                "spin_tm": _to_float(row.get("spin_tm")),
                "ball_speed_tm": _to_float(row.get("ball_speed_tm")),
            }
    return by_shot


def _post_onset_envelope(
    processor: RollingBufferProcessor,
    capture: IQCapture,
    ball_speed_mph: float,
    ball_timestamp_ms: float,
) -> Optional[np.ndarray]:
    i_data = np.array(capture.i_samples, dtype=np.float64)
    q_data = np.array(capture.q_samples, dtype=np.float64)
    i_data -= np.mean(i_data)
    q_data -= np.mean(q_data)
    iq = i_data + 1j * q_data

    ball_speed_mps = ball_speed_mph / processor.MPS_TO_MPH
    ball_doppler_hz = 2 * ball_speed_mps / processor.WAVELENGTH_M
    nyquist = processor.SAMPLE_RATE / 2
    low = max((ball_doppler_hz - processor.SPIN_BANDPASS_BW_HZ) / nyquist, 0.001)
    high = min((ball_doppler_hz + processor.SPIN_BANDPASS_BW_HZ) / nyquist, 0.999)
    if low >= high:
        return None

    sos = butter(processor.SPIN_BANDPASS_ORDER, [low, high], btype="band", output="sos")
    filtered = sosfiltfilt(sos, iq)
    envelope = np.abs(filtered)
    start_sample = max(0, int(ball_timestamp_ms * processor.SAMPLE_RATE / 1000))
    return envelope[start_sample:]


def _current_window(processor: RollingBufferProcessor, post_onset: np.ndarray) -> WindowSpec:
    transient_samples = int(processor.SAMPLE_RATE / processor.SPIN_BANDPASS_BW_HZ)
    if len(post_onset) > 2 * transient_samples + processor.SPIN_MIN_SAMPLES:
        return WindowSpec("current_full_trimmed", transient_samples, len(post_onset) - transient_samples)
    return WindowSpec("current_full_trimmed", 0, len(post_onset))


def _fixed_window(
    processor: RollingBufferProcessor,
    post_onset: np.ndarray,
    duration_ms: int,
) -> Optional[WindowSpec]:
    transient_samples = int(processor.SAMPLE_RATE / processor.SPIN_BANDPASS_BW_HZ)
    duration_samples = int(processor.SAMPLE_RATE * duration_ms / 1000)
    start = transient_samples
    end = min(len(post_onset), start + duration_samples)
    if end - start < processor.SPIN_MIN_SAMPLES:
        return None
    return WindowSpec(f"fixed_{duration_ms}ms", start, end)


def _sliding_energy_window(
    processor: RollingBufferProcessor,
    post_onset: np.ndarray,
    duration_ms: int,
    step_ms: int = 5,
) -> Optional[WindowSpec]:
    transient_samples = int(processor.SAMPLE_RATE / processor.SPIN_BANDPASS_BW_HZ)
    duration_samples = int(processor.SAMPLE_RATE * duration_ms / 1000)
    step_samples = int(processor.SAMPLE_RATE * step_ms / 1000)
    search_start = transient_samples
    search_end = len(post_onset) - transient_samples
    if search_end - search_start < max(duration_samples, processor.SPIN_MIN_SAMPLES):
        return None

    best_start = None
    best_energy = -1.0
    for start in range(search_start, search_end - duration_samples + 1, step_samples):
        segment = post_onset[start:start + duration_samples]
        energy = float(np.mean(segment * segment))
        if energy > best_energy:
            best_energy = energy
            best_start = start

    if best_start is None:
        return None
    return WindowSpec(
        f"energy_{duration_ms}ms",
        best_start,
        best_start + duration_samples,
    )


def _window_specs(
    processor: RollingBufferProcessor,
    post_onset: np.ndarray,
) -> list[WindowSpec]:
    specs = [_current_window(processor, post_onset)]
    for duration_ms in (25, 30, 35, 45, 60, 80):
        fixed = _fixed_window(processor, post_onset, duration_ms)
        if fixed:
            specs.append(fixed)
        energy = _sliding_energy_window(processor, post_onset, duration_ms)
        if energy:
            specs.append(energy)
    return specs


def _detect_spin_in_window(
    processor: RollingBufferProcessor,
    envelope: np.ndarray,
    spec: WindowSpec,
    expected_spin_rpm: Optional[float],
) -> SpinResult:
    ball_envelope = envelope[spec.start_sample:spec.end_sample].copy()
    if len(ball_envelope) < processor.SPIN_MIN_SAMPLES:
        return SpinResult.no_spin_detected(
            f"Ball signal too short ({len(ball_envelope)} samples, need {processor.SPIN_MIN_SAMPLES})"
        )

    weak_modulation = False
    modulation_depth: Optional[float] = None
    envelope_mean = np.mean(ball_envelope)
    envelope_std = np.std(ball_envelope)
    if envelope_mean > 0:
        modulation_depth = float(envelope_std / envelope_mean)
        if modulation_depth < 0.005:
            return SpinResult.no_spin_detected(
                f"Modulation depth too low ({modulation_depth:.4f})",
                modulation_depth=modulation_depth,
            )
        weak_modulation = modulation_depth < 0.01

    ball_envelope -= envelope_mean
    if envelope_std < 1e-6:
        return SpinResult.no_spin_detected(
            "Envelope variation too low",
            modulation_depth=modulation_depth,
        )

    windowed = ball_envelope * np.hanning(len(ball_envelope))
    fft_result = np.fft.fft(windowed, processor.SPIN_ENVELOPE_FFT_SIZE)
    freqs = np.fft.fftfreq(processor.SPIN_ENVELOPE_FFT_SIZE, d=1 / processor.SAMPLE_RATE)
    half = processor.SPIN_ENVELOPE_FFT_SIZE // 2
    magnitude = np.abs(fft_result[1:half])
    freqs = freqs[1:half]

    valid_mask = (freqs >= processor.SPIN_MIN_SEAM_HZ) & (freqs <= processor.SPIN_MAX_SEAM_HZ)
    if not np.any(valid_mask):
        return SpinResult.no_spin_detected(
            "No valid seam frequencies in range",
            modulation_depth=modulation_depth,
        )

    valid_mag = magnitude[valid_mask].copy()
    valid_freqs = freqs[valid_mask]
    n_valid = len(valid_mag)
    leakage = min(processor.SPIN_DC_LEAKAGE_BINS, max(0, n_valid - 1))
    if leakage > 0:
        valid_mag[:leakage] = 0

    peak_idx = processor._select_spin_peak(
        valid_mag,
        valid_freqs,
        leakage,
        expected_spin_rpm=expected_spin_rpm,
    )
    peak_freq = float(valid_freqs[peak_idx])
    peak_mag = float(valid_mag[peak_idx])
    at_lower_rail = peak_idx < leakage + processor.SPIN_UPPER_RAIL_BINS
    at_upper_rail = peak_idx >= n_valid - processor.SPIN_UPPER_RAIL_BINS
    noise_floor = np.median(valid_mag[valid_mag > 0]) if np.any(valid_mag > 0) else 1.0
    fft_snr = peak_mag / noise_floor if noise_floor > 0 else 0.0
    candidates = processor._build_spin_candidates(
        valid_mag,
        valid_freqs,
        leakage,
        noise_floor,
        expected_spin_rpm=expected_spin_rpm,
        selected_idx=peak_idx,
    )

    spin_rpm = peak_freq * 60
    seam_cycles = peak_freq * len(ball_envelope) / processor.SAMPLE_RATE

    if at_upper_rail and fft_snr < processor.SPIN_SNR_HIGH:
        return SpinResult.no_spin_detected(
            f"Upper-rail peak at {spin_rpm:.0f} RPM "
            f"(SNR {fft_snr:.1f} below high threshold {processor.SPIN_SNR_HIGH:.0f})",
            snr=fft_snr,
            modulation_depth=modulation_depth,
            peak_freq_hz=peak_freq,
            seam_cycles=seam_cycles,
            at_upper_rail=True,
            candidates=candidates,
        )

    if at_lower_rail and (
        modulation_depth is None or modulation_depth < 0.012
    ):
        return SpinResult.no_spin_detected(
            f"Lower-rail peak at {spin_rpm:.0f} RPM "
            f"(mod {modulation_depth or 0:.4f}, envelope-DC leakage suspected)",
            snr=fft_snr,
            modulation_depth=modulation_depth,
            peak_freq_hz=peak_freq,
            seam_cycles=seam_cycles,
            at_lower_rail=True,
            candidates=candidates,
        )

    if seam_cycles < processor.SPIN_MIN_CYCLES:
        return SpinResult.no_spin_detected(
            f"Too few seam cycles ({seam_cycles:.1f}, need {processor.SPIN_MIN_CYCLES})",
            snr=fft_snr,
            modulation_depth=modulation_depth,
            peak_freq_hz=peak_freq,
            seam_cycles=seam_cycles,
            at_lower_rail=at_lower_rail,
            at_upper_rail=at_upper_rail,
            candidates=candidates,
        )

    if fft_snr < processor.SPIN_SNR_MIN:
        return SpinResult.no_spin_detected(
            f"SNR too low ({fft_snr:.2f}, need {processor.SPIN_SNR_MIN:.1f})",
            snr=fft_snr,
            modulation_depth=modulation_depth,
            peak_freq_hz=peak_freq,
            seam_cycles=seam_cycles,
            at_lower_rail=at_lower_rail,
            at_upper_rail=at_upper_rail,
            candidates=candidates,
        )

    if fft_snr >= processor.SPIN_SNR_HIGH and seam_cycles >= 5:
        quality = "high"
        confidence = 0.9
    elif fft_snr >= processor.SPIN_SNR_HIGH and seam_cycles >= 3:
        quality = "high"
        confidence = 0.8
    elif fft_snr >= processor.SPIN_SNR_MEDIUM and seam_cycles >= 3:
        quality = "medium"
        confidence = SPIN_CONFIDENCE_HIGH
    elif fft_snr >= processor.SPIN_SNR_MEDIUM:
        quality = "low"
        confidence = 0.5
    else:
        quality = "low"
        confidence = 0.3

    if weak_modulation:
        confidence = min(confidence, 0.5)
        if quality == "high":
            quality = "medium"

    if at_lower_rail:
        confidence = min(confidence, 0.5)
        if quality in ("high", "medium"):
            quality = "low"

    return SpinResult(
        spin_rpm=round(spin_rpm),
        confidence=confidence,
        snr=round(fft_snr, 2),
        quality=quality,
        modulation_depth=modulation_depth,
        peak_freq_hz=peak_freq,
        seam_cycles=seam_cycles,
        at_lower_rail=at_lower_rail,
        at_upper_rail=at_upper_rail,
        candidates=candidates,
    )


def _result_is_reportable(spin: SpinResult) -> bool:
    return bool(
        spin.spin_rpm > 0
        and not spin.at_lower_rail
        and not spin.at_upper_rail
    )


def _rows(
    shots: list[dict],
    captures: list[dict],
    trackman_by_shot: dict[int, dict[str, Any]],
    sample_rate_hz: int,
) -> list[dict[str, Any]]:
    processor = RollingBufferProcessor(sample_rate=sample_rate_hz)
    rows = []
    for shot_entry, capture_entry in zip(shots, captures):
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = _to_int(shot_data.get("shot_number"))
        trackman = trackman_by_shot.get(shot_number or -1, {})
        if trackman.get("match_quality") != "good" or trackman.get("spin_tm") is None:
            continue

        normalized_club = ct.normalize_club(shot_data.get("club"))
        club = _club_enum(normalized_club)
        capture = IQCapture(
            sample_time=capture_entry.get("sample_time", 0),
            trigger_time=capture_entry.get("trigger_time", 0),
            i_samples=capture_entry["i_samples"],
            q_samples=capture_entry["q_samples"],
        )
        processed = processor.process_capture(
            capture,
            expected_spin_for_ball_speed=lambda ball_speed, club=club: (
                get_optimal_spin_for_ball_speed(ball_speed, club)
            ),
        )
        if not processed:
            continue

        expected_spin = get_optimal_spin_for_ball_speed(processed.ball_speed_mph, club)
        envelope = _post_onset_envelope(
            processor,
            capture,
            processed.ball_speed_mph,
            processed.ball_timestamp_ms,
        )
        if envelope is None:
            continue

        for spec in _window_specs(processor, envelope):
            spin = (
                processed.spin
                if spec.name == "current_full_trimmed"
                else _detect_spin_in_window(processor, envelope, spec, expected_spin)
            )
            if spin is None:
                continue
            top_candidate = spin.candidates[0] if spin.candidates else None
            selected_candidate = next(
                (candidate for candidate in spin.candidates if candidate.selected),
                None,
            )
            reportable = _result_is_reportable(spin)
            spin_tm = trackman["spin_tm"]
            rows.append({
                "shot_number": shot_number,
                "club": normalized_club,
                "strategy": spec.name,
                "window_start_ms": round(spec.start_sample / processor.SAMPLE_RATE * 1000, 1),
                "window_ms": round(spec.sample_count / processor.SAMPLE_RATE * 1000, 1),
                "ball_speed_of": round(processed.ball_speed_mph, 3),
                "ball_speed_tm": trackman.get("ball_speed_tm"),
                "expected_spin_rpm": round(expected_spin),
                "spin_tm": spin_tm,
                "spin_rpm": round(spin.spin_rpm) if spin.spin_rpm > 0 else None,
                "spin_error_rpm": (
                    round(spin.spin_rpm - spin_tm, 1)
                    if reportable else None
                ),
                "reportable": reportable,
                "quality": spin.quality,
                "confidence": spin.confidence,
                "snr": spin.snr,
                "modulation_depth": (
                    round(spin.modulation_depth, 4)
                    if spin.modulation_depth is not None else None
                ),
                "seam_cycles": (
                    round(spin.seam_cycles, 2)
                    if spin.seam_cycles is not None else None
                ),
                "at_lower_rail": spin.at_lower_rail,
                "at_upper_rail": spin.at_upper_rail,
                "rejection_reason": spin.rejection_reason,
                "selected_candidate_rank": (
                    selected_candidate.rank if selected_candidate else None
                ),
                "top_candidate_rpm": (
                    round(top_candidate.rpm) if top_candidate else None
                ),
                "top_candidate_snr": (
                    round(top_candidate.snr, 2) if top_candidate else None
                ),
            })
    return rows


def _write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_strategy.setdefault(row["strategy"], []).append(row)

    summary = []
    for strategy, strategy_rows in sorted(by_strategy.items()):
        reportable = [row for row in strategy_rows if row["reportable"]]
        errors = [float(row["spin_error_rpm"]) for row in reportable]
        summary.append({
            "strategy": strategy,
            "shots": len(strategy_rows),
            "reported": len(reportable),
            "bias": statistics.mean(errors) if errors else None,
            "mae": statistics.mean(abs(error) for error in errors) if errors else None,
            "rmse": math.sqrt(statistics.mean(error * error for error in errors)) if errors else None,
        })
    return summary


def _print_summary(summary: list[dict[str, Any]]) -> None:
    print(f"{'strategy':<24} {'n':>3} {'MAE':>8} {'bias':>8} {'RMSE':>8}")
    for row in sorted(
        summary,
        key=lambda item: (
            item["mae"] if item["mae"] is not None else float("inf"),
            -item["reported"],
        ),
    ):
        mae = f"{row['mae']:.1f}" if row["mae"] is not None else "-"
        bias = f"{row['bias']:.1f}" if row["bias"] is not None else "-"
        rmse = f"{row['rmse']:.1f}" if row["rmse"] is not None else "-"
        print(f"{row['strategy']:<24} {row['reported']:>3} {mae:>8} {bias:>8} {rmse:>8}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openflight", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=30000)
    args = parser.parse_args()

    shots, captures = _load_session_entries(args.openflight)
    trackman_by_shot = _load_trackman_by_shot(args.comparison)
    rows = _rows(shots, captures, trackman_by_shot, args.sample_rate)
    _write_csv(rows, args.output)
    _print_summary(_summary(rows))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
