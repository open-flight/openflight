"""Live-pipeline tests for the ungated multitaper spin estimator."""

from datetime import datetime

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer import (
    IQCapture,
    ProcessedCapture,
    RollingBufferProcessor,
    SpeedTimeline,
    SpinResult,
)
from openflight.rolling_buffer.multitaper import repair_clipped_iq


def _modulated_capture(
    *,
    ball_speed_mph: float = 100.0,
    spin_rpm: float = 9000.0,
    fade_hz: float = 48.0,
    spin_depth: float = 0.015,
    fade_depth: float = 0.15,
    sample_count: int = 4096,
) -> IQCapture:
    """Build an OPS capture with strong multipath fade and weaker seam tone."""
    sample_rate_hz = 30_000
    time_s = np.arange(sample_count, dtype=np.float64) / sample_rate_hz
    speed_mps = ball_speed_mph / RollingBufferProcessor.MPS_TO_MPH
    doppler_hz = 2.0 * speed_mps / RollingBufferProcessor.WAVELENGTH_M
    spin_hz = spin_rpm / 60.0
    amplitude = 500.0 * (
        1.0
        + fade_depth * np.sin(2.0 * np.pi * fade_hz * time_s)
        + spin_depth * np.sin(2.0 * np.pi * spin_hz * time_s)
    )
    phase = 2.0 * np.pi * doppler_hz * time_s
    i_samples = np.clip(amplitude * np.cos(phase) + 2048.0, 0, 4095).astype(int)
    q_samples = np.clip(amplitude * np.sin(phase) + 2048.0, 0, 4095).astype(int)
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=i_samples.tolist(),
        q_samples=q_samples.tolist(),
    )


def test_multitaper_recovers_weak_spin_below_strong_fade():
    """Fade regression should expose a seam tone much weaker than multipath."""
    processor = RollingBufferProcessor()

    result = processor.detect_spin_multitaper(
        _modulated_capture(),
        ball_speed_mph=100.0,
        ball_timestamp_ms=5.0,
    )

    assert result.method == "multitaper_ungated"
    assert result.spin_rpm == pytest.approx(9000.0, abs=500.0)
    assert result.quality == "experimental"
    assert result.rejection_reason is None


def test_clipped_iq_is_interpolated_before_multitaper_processing():
    """Near-rail samples must receive the same repair used in offline scoring."""
    i_samples = np.array([1000.0, 1100.0, 4095.0, 1300.0, 1400.0])
    q_samples = np.array([2000.0, 2100.0, 2200.0, 2300.0, 2400.0])

    repaired, clipped_fraction = repair_clipped_iq(i_samples, q_samples)

    assert clipped_fraction == pytest.approx(0.2)
    assert repaired.real[2] == pytest.approx(0.0)
    assert repaired.imag[2] == pytest.approx(0.0)


def test_process_capture_uses_multitaper_estimator(monkeypatch):
    """The production capture path must call multitaper, not the legacy gate."""
    processor = RollingBufferProcessor()

    def legacy_detector_must_not_run(*args, **kwargs):
        raise AssertionError("legacy spin detector called")

    monkeypatch.setattr(processor, "detect_spin", legacy_detector_must_not_run)
    processed = processor.process_capture(_modulated_capture())

    assert processed is not None
    assert processed.spin is not None
    assert processed.spin.method == "multitaper_ungated"
    assert processed.spin.spin_rpm > 0


def test_process_capture_does_not_repeat_standard_fft_windows(monkeypatch):
    """Standard windows are a subset of the overlapping FFT timeline."""
    processor = RollingBufferProcessor()
    peaks_from_magnitude = processor._peaks_from_magnitude
    calls = 0

    def count_windows(*args, **kwargs):
        nonlocal calls
        calls += 1
        return peaks_from_magnitude(*args, **kwargs)

    monkeypatch.setattr(processor, "_peaks_from_magnitude", count_windows)

    assert processor.process_capture(_modulated_capture()) is not None
    expected_overlapping_windows = (
        (4096 - processor.WINDOW_SIZE) // processor.STEP_SIZE_OVERLAP
    ) + 1
    assert calls == expected_overlapping_windows


def test_multitaper_accepts_short_record_used_by_offline_scoring():
    """The live wrapper must not reintroduce the legacy 600-sample gate."""
    processor = RollingBufferProcessor()

    result = processor.detect_spin_multitaper(
        _modulated_capture(sample_count=512),
        ball_speed_mph=100.0,
        ball_timestamp_ms=0.0,
    )

    assert result.method == "multitaper_ungated"
    assert result.spin_rpm > 0
    assert result.rejection_reason is None


def test_multitaper_candidate_is_reported_without_confidence_or_rail_gate():
    """Experimental candidates stay visible but remain excluded from carry."""
    from openflight.rolling_buffer import RollingBufferMonitor

    capture = _modulated_capture()
    processed = ProcessedCapture(
        timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
        ball_speed_mph=100.0,
        ball_timestamp_ms=60.0,
        club_speed_mph=75.0,
        spin=SpinResult(
            spin_rpm=10_950.0,
            confidence=0.1,
            snr=1.2,
            quality="experimental",
            method="multitaper_ungated",
            multipath_fade_hz=48.2,
            peak_freq_hz=182.5,
            at_upper_rail=True,
        ),
        capture=capture,
    )
    monitor = RollingBufferMonitor(port=None, trigger_type="sound")
    monitor.set_club(ClubType.PW)

    shot = monitor._create_shot(processed)

    assert shot is not None
    assert shot.spin_rpm == pytest.approx(10_950.0)
    assert shot.spin_method == "multitaper_ungated"
    assert shot.spin_confidence == pytest.approx(0.1)
    assert shot.spin_quality == "experimental"
    assert shot.spin_rejection_reason is None
    assert shot.carry_spin_adjusted is None

    from openflight.server import shot_to_dict

    payload = shot_to_dict(shot)
    assert payload["spin_rpm"] == 10_950
    assert payload["spin_method"] == "multitaper_ungated"
    assert payload["spin_quality"] == "experimental"
    assert payload["spin_multipath_fade_hz"] == pytest.approx(48.2)


def test_shot_method_defaults_to_none():
    """Non-rolling and legacy shots remain backward compatible."""
    from openflight.launch_monitor import Shot

    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

    assert shot.spin_method is None


def _reference_remove_multipath_fade(
    envelope, sample_rate_hz, *, fade_band_hz, search_width_hz=8.0, grid_step_hz=0.25
):
    """Per-candidate fade search that the broadcast version replaced."""
    # pylint: disable=protected-access
    from openflight.rolling_buffer import multitaper

    def sine_columns(length, frequency_hz):
        phase = 2.0 * np.pi * frequency_hz * (np.arange(length, dtype=np.float64) / sample_rate_hz)
        return np.column_stack((np.sin(phase), np.cos(phase)))

    polynomial = multitaper._polynomial_design(len(envelope))
    hint = multitaper._dominant_frequency(envelope, sample_rate_hz, fade_band_hz)
    low = max(fade_band_hz[0], hint - search_width_hz)
    high = min(fade_band_hz[1], hint + search_width_hz)
    best_frequency, best_residual, best_sse = hint, None, float("inf")
    for fade_hz in np.arange(low, high + grid_step_hz / 2.0, grid_step_hz):
        design = np.column_stack(
            (
                polynomial,
                sine_columns(len(envelope), fade_hz),
                sine_columns(len(envelope), 2.0 * fade_hz),
            )
        )
        coefficients = np.linalg.lstsq(design, envelope, rcond=None)[0]
        residual = envelope - design @ coefficients
        sse = float(residual @ residual)
        if sse < best_sse:
            best_frequency, best_residual, best_sse = float(fade_hz), residual, sse
    return best_residual, best_frequency


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("fade_hz", [30.0, 48.3, 71.7])
def test_fade_removal_matches_per_candidate_reference_exactly(seed, fade_hz):
    from openflight.rolling_buffer.multitaper import _remove_multipath_fade  # pylint: disable=protected-access

    rng = np.random.default_rng(seed)
    sample_rate_hz = 30_000.0 / 32
    t = np.arange(400) / sample_rate_hz
    envelope = (
        1.0
        + 0.15 * np.sin(2 * np.pi * fade_hz * t + seed)
        + 0.015 * np.sin(2 * np.pi * 120.0 * t)
        + rng.normal(0, 0.01, t.size)
    )
    band = (25.0, 90.0)
    expected = _reference_remove_multipath_fade(envelope, sample_rate_hz, fade_band_hz=band)
    actual = _remove_multipath_fade(envelope, sample_rate_hz, fade_band_hz=band)
    assert actual[1] == expected[1]
    assert np.array_equal(actual[0], expected[0])
