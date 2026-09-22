"""Multipath-aware multitaper spin estimation for OPS rolling captures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.signal.windows import dpss


@dataclass(frozen=True)
class MultitaperEstimate:
    """Ungated spectral candidate and supporting evidence."""

    spin_hz: float
    spin_rpm: float
    peak_to_floor: float
    fade_hz: float


def repair_clipped_iq(
    i_samples: np.ndarray,
    q_samples: np.ndarray,
    *,
    margin: int = 10,
) -> tuple[np.ndarray, float]:
    """Interpolate near-rail samples using the evaluated offline treatment."""
    i_values = np.asarray(i_samples, dtype=np.float64)
    q_values = np.asarray(q_samples, dtype=np.float64)
    clipped = (
        (i_values <= margin)
        | (i_values >= 4095 - margin)
        | (q_values <= margin)
        | (q_values >= 4095 - margin)
    )
    repaired_i = i_values.copy()
    repaired_q = q_values.copy()
    valid = np.flatnonzero(~clipped)
    if valid.size >= 2 and clipped.any():
        bad = np.flatnonzero(clipped)
        repaired_i[bad] = np.interp(bad, valid, repaired_i[valid])
        repaired_q[bad] = np.interp(bad, valid, repaired_q[valid])
    repaired = (repaired_i - repaired_i.mean()) + 1j * (repaired_q - repaired_q.mean())
    return repaired, float(np.mean(clipped))


@lru_cache(maxsize=32)
def _polynomial_design(length: int, order: int = 3) -> np.ndarray:
    coordinate = np.linspace(-1.0, 1.0, length)
    design = np.column_stack([coordinate**degree for degree in range(order + 1)])
    design.setflags(write=False)
    return design


@lru_cache(maxsize=16)
def _dpss_tapers(length: int, time_bandwidth: float, taper_count: int) -> np.ndarray:
    """Cache immutable tapers reused by equal-length shot windows."""
    tapers = dpss(length, time_bandwidth, Kmax=taper_count, sym=False)
    tapers.setflags(write=False)
    return tapers


def _sine_columns(length: int, sample_rate_hz: float, frequency_hz: float) -> np.ndarray:
    time_s = np.arange(length, dtype=np.float64) / sample_rate_hz
    phase = 2.0 * np.pi * frequency_hz * time_s
    return np.column_stack((np.sin(phase), np.cos(phase)))


def _dominant_frequency(
    signal: np.ndarray,
    sample_rate_hz: float,
    band_hz: tuple[float, float],
) -> float:
    polynomial = _polynomial_design(len(signal))
    residual = signal - polynomial @ np.linalg.lstsq(polynomial, signal, rcond=None)[0]
    fft_size = max(8192, 1 << math.ceil(math.log2(len(signal) * 8)))
    spectrum = np.abs(np.fft.rfft(residual * np.hanning(len(residual)), fft_size))
    frequencies = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate_hz)
    valid_indices = np.flatnonzero((frequencies >= band_hz[0]) & (frequencies <= band_hz[1]))
    if not valid_indices.size:
        raise ValueError("fade band does not intersect the spectrum")
    return float(frequencies[valid_indices[np.argmax(spectrum[valid_indices])]])


def _remove_multipath_fade(
    envelope: np.ndarray,
    sample_rate_hz: float,
    *,
    fade_band_hz: tuple[float, float],
    search_width_hz: float = 8.0,
    grid_step_hz: float = 0.25,
) -> tuple[np.ndarray, float]:
    """Regress the dominant two-ray fade and its second harmonic."""
    polynomial = _polynomial_design(len(envelope))
    fade_hint_hz = _dominant_frequency(envelope, sample_rate_hz, fade_band_hz)
    fade_low = max(fade_band_hz[0], fade_hint_hz - search_width_hz)
    fade_high = min(fade_band_hz[1], fade_hint_hz + search_width_hz)

    best_frequency = fade_hint_hz
    best_residual: np.ndarray | None = None
    best_sse = math.inf
    for fade_hz in np.arange(fade_low, fade_high + grid_step_hz / 2.0, grid_step_hz):
        design = np.column_stack(
            (
                polynomial,
                _sine_columns(len(envelope), sample_rate_hz, fade_hz),
                _sine_columns(len(envelope), sample_rate_hz, 2.0 * fade_hz),
            )
        )
        coefficients = np.linalg.lstsq(design, envelope, rcond=None)[0]
        residual = envelope - design @ coefficients
        sse = float(residual @ residual)
        if sse < best_sse:
            best_frequency = float(fade_hz)
            best_residual = residual
            best_sse = sse

    if best_residual is None:
        raise ValueError("fade search produced no candidates")
    return best_residual, best_frequency


def estimate_multitaper_spin(
    envelope: np.ndarray,
    sample_rate_hz: float,
    *,
    spin_rpm_band: tuple[float, float] = (1500.0, 11_000.0),
    fade_band_hz: tuple[float, float] = (25.0, 90.0),
    time_bandwidth: float = 2.5,
    taper_count: int = 4,
) -> MultitaperEstimate:
    """Return the strongest bounded candidate after two-ray fade removal."""
    values = np.asarray(envelope, dtype=np.float64)
    if values.ndim != 1 or len(values) < 128:
        raise ValueError("envelope must contain at least 128 samples")

    values, fade_hz = _remove_multipath_fade(
        values,
        sample_rate_hz,
        fade_band_hz=fade_band_hz,
    )
    polynomial = _polynomial_design(len(values))
    values = values - polynomial @ np.linalg.lstsq(polynomial, values, rcond=None)[0]

    tapers = _dpss_tapers(len(values), time_bandwidth, taper_count)
    fft_size = max(8192, 1 << math.ceil(math.log2(len(values) * 8)))
    power = np.zeros(fft_size // 2 + 1, dtype=np.float64)
    for taper in tapers:
        spectrum = np.fft.rfft(values * taper, fft_size)
        power += np.abs(spectrum) ** 2
    power /= len(tapers)

    frequencies = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate_hz)
    low_hz, high_hz = (bound / 60.0 for bound in spin_rpm_band)
    valid = (frequencies >= low_hz) & (frequencies <= high_hz)
    valid_indices = np.flatnonzero(valid)
    if not valid_indices.size:
        raise ValueError("spin band does not intersect the spectrum")

    coherent_power = np.abs(np.fft.rfft(values * np.hanning(len(values)), fft_size)) ** 2
    peak_index = int(valid_indices[np.argmax(coherent_power[valid])])
    natural_resolution_hz = sample_rate_hz / len(values)
    exclusion = np.abs(frequencies - frequencies[peak_index]) <= 1.5 * natural_resolution_hz
    evidence_band = (frequencies >= max(20.0, low_hz - 4.0 * natural_resolution_hz)) & (
        frequencies <= high_hz + 4.0 * natural_resolution_hz
    )
    floor_values = power[evidence_band & ~exclusion]
    if not floor_values.size:
        floor_values = power[valid]
    floor = float(np.median(floor_values))
    peak_region = valid & (np.abs(frequencies - frequencies[peak_index]) <= natural_resolution_hz)
    ratio = float(np.max(power[peak_region]) / max(floor, np.finfo(float).tiny))
    spin_hz = float(frequencies[peak_index])
    return MultitaperEstimate(
        spin_hz=spin_hz,
        spin_rpm=spin_hz * 60.0,
        peak_to_floor=ratio,
        fade_hz=fade_hz,
    )
