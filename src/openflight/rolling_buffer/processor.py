"""
Rolling buffer signal processor.

Handles FFT processing of raw I/Q data to extract speed and spin information.
Based on OmniPreSense AN-027 Rolling Buffer application note.
"""

import json
import logging
from collections.abc import Iterator
from typing import Callable, List, Optional, Tuple

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

from ..clubs import ClubType
from ..launch_monitor import SPIN_CONFIDENCE_HIGH
from .multitaper import estimate_multitaper_spin, repair_clipped_iq
from .types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedReading,
    SpeedTimeline,
    SpinCandidate,
    SpinResult,
)

logger = logging.getLogger("openflight.rolling_buffer.processor")


class RollingBufferProcessor:
    """
    Processes raw I/Q data from rolling buffer mode into speed and spin data.

    The processor implements:
    1. Standard FFT processing (128-sample blocks, ~234 Hz equivalent)
    2. Overlapping FFT processing (32-sample steps, ~937 Hz)
    3. Secondary FFT for spin detection from speed oscillations

    Based on OmniPreSense documentation:
    - AN-027-A Rolling Buffer
    - Sports Ball Detection presentation
    """

    # Processing constants
    WINDOW_SIZE = 128  # Samples per FFT window
    FFT_SIZE = 4096  # Zero-padded FFT size
    STEP_SIZE_STANDARD = 128  # Non-overlapping step
    STEP_SIZE_OVERLAP = 32  # Overlapping step for high resolution
    SAMPLE_RATE = 30000  # 30 ksps

    # Speed conversion
    # Speed = bin_index * wavelength * sample_rate / (2 * fft_size)
    # For 24.125 GHz radar: wavelength = c / f = 0.01243 m
    # Simplified: bin * 0.0063 * (sample_rate / fft_size) gives m/s
    WAVELENGTH_M = 0.01243  # meters (24.125 GHz)
    MPS_TO_MPH = 2.23694

    # Signal processing
    ADC_RANGE = 4096  # 12-bit ADC
    VOLTAGE_REF = 3.3  # Reference voltage

    # Magnitude threshold for valid peaks. Low threshold lets weak signals
    # through; they get filtered later by the 15 mph speed check.
    MAGNITUDE_THRESHOLD = 3

    # Multi-peak extraction
    MIN_PEAK_SEPARATION_BINS = 50  # ~5 mph; rejects sidelobe duplicates
    MAX_PEAKS_PER_DIRECTION = 3

    # DC mask: skip first N bins in peak search to reject DC leakage,
    # body movement, and environmental noise. At 30kHz/4096-pt FFT,
    # each bin ≈ 0.1 mph, so 150 bins ≈ 15 mph exclusion zone.
    # Matches the streaming processor's dc_mask and the trigger's
    # 15 mph acceptance threshold — no useful signal lives below 15 mph.
    DC_MASK_BINS = 150

    # Club-speed extraction. Each overlapping FFT spans 4.27 ms, so the
    # windows immediately before the first ball reading contain mixed club and
    # ball energy. Track the fastest pre-impact Doppler branch, then summarize
    # its plateau before that mixed region instead of selecting the strongest
    # reflector (which is frequently the shaft or an earlier swing return).
    CLUB_BRANCH_HISTORY_MS = 30.0
    CLUB_BALL_ONSET_SEARCH_MS = 10.0
    CLUB_BALL_MATCH_MIN_MPH = 4.0
    CLUB_BALL_MATCH_FRACTION = 0.06
    CLUB_PLATEAU_LOOKBACK_MS = 18.0
    CLUB_PLATEAU_GUARD_MS = 4.0
    CLUB_PLATEAU_QUANTILE = 0.70
    CLUB_BALL_CONTAMINATION_RATIO = 0.95
    CLUB_TERMINAL_START_MS = -2.5
    CLUB_TERMINAL_END_MS = 1.0
    CLUB_MAX_PLAUSIBLE_SPEED_MPH = 150.0
    CLUB_SMOOTH_MERGE_TYPES = frozenset({ClubType.SW, ClubType.LW})

    # Spin detection via amplitude envelope demodulation.
    # The ball seam modulates the radar return at 1x spin rate.
    SPIN_BANDPASS_BW_HZ = 700  # ±700 Hz around ball Doppler (must cover max seam freq)
    SPIN_BANDPASS_ORDER = 4  # Butterworth filter order
    SPIN_ENVELOPE_FFT_SIZE = 8192  # Zero-padded FFT for envelope
    SPIN_MIN_SEAM_HZ = 33.0  # ~2000 RPM min (seam = 1x spin)
    SPIN_MAX_SEAM_HZ = 200.0  # 12000 RPM max
    SPIN_MIN_SAMPLES = 600  # ~20ms minimum ball signal
    SPIN_SNR_HIGH = 8.0  # High confidence threshold
    SPIN_SNR_MEDIUM = 5.0  # Medium confidence threshold
    SPIN_SNR_MIN = 2.5  # Minimum to report
    SPIN_AUTOCORR_MIN = 0.3  # Minimum normalized correlation
    SPIN_MIN_CYCLES = 2  # Minimum seam cycles to report
    # Rail-rejection guards. The envelope FFT has two pathological
    # regions where the peak picker hunts for noise rather than a real
    # seam tone:
    #   - Slow envelope drift (range falloff, residual DC) leaks into
    #     the lowest bins of the seam range. The polynomial detrend
    #     removes the drift itself; a 1-bin guard absorbs what little
    #     leakage remains. (This guard was previously 5 bins, which
    #     zeroed 33-51.3 Hz = 1980-3080 RPM — typical driver backspin —
    #     and produced a rail-artifact pile-up at ~3296 RPM instead.)
    #   - The highest 1-2 bins are the bandpass shoulder of the
    #     prefilter. Even a moderate noise spike there reads as
    #     ~12000 RPM. Reject the pick when the peak lands there
    #     and SNR isn't strong enough to override.
    SPIN_DC_LEAKAGE_BINS = 1  # Zero this many low bins of valid range
    # Picks at or below this RPM are reported but capped to low quality:
    # red envelope noise produces sustained narrowband mimics in this
    # band that a single ~136 ms capture cannot distinguish from a real
    # seam tone (see the low-band cap in detect_spin).
    SPIN_LOW_BAND_SUSPECT_MAX_RPM = 3100.0
    # Polynomial order for envelope detrending before the spin FFT.
    # Order 3 follows the smooth range-falloff decay across the window
    # but cannot track a >=33 Hz seam tone (>=2 cycles in any window
    # long enough to pass SPIN_MIN_SAMPLES), so real spin is preserved.
    SPIN_DETREND_POLY_ORDER = 3
    # Ball-signal-end detection: the analysis window must stop when the
    # ball signal dies (net impact indoors), not at the end of the
    # capture — the amplitude cliff plus dead air corrupts the envelope
    # FFT. Signal is "lost" when the smoothed envelope stays below
    # SPIN_SIGNAL_LOSS_THRESHOLD x (early-window median) for
    # SPIN_SIGNAL_LOSS_HOLD_SAMPLES.
    SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES = 90  # ~3 ms moving average
    SPIN_SIGNAL_LOSS_REF_SAMPLES = 450  # first ~15 ms sets reference level
    SPIN_SIGNAL_LOSS_THRESHOLD = 0.15  # fraction of reference level
    SPIN_SIGNAL_LOSS_HOLD_SAMPLES = 150  # ~5 ms sustained loss
    SPIN_UPPER_RAIL_BINS = 2  # Top N bins of valid range = "upper rail"
    SPIN_PRIOR_MIN_RELATIVE_MAG = 0.40  # Candidate must be this strong to displace argmax
    SPIN_PRIOR_MAX_RELATIVE_ERROR = 0.55  # Candidate must be within this fraction of expected
    SPIN_PRIOR_STRONGEST_FAR_ERROR = 0.45  # Strongest peak is "far" above this error
    SPIN_HIGH_PRIOR_MIN_RPM = 6000.0  # Prior high enough to identify iron/wedge spin
    SPIN_IMPLAUSIBLE_LOWER_RAIL_FRACTION = 0.60  # Rail below this fraction is artifact-like
    SPIN_LOWER_RAIL_RECOVERY_MIN_RELATIVE_MAG = 0.20
    SPIN_LOWER_RAIL_RECOVERY_MAX_RELATIVE_ERROR = 0.35
    SPIN_CANDIDATE_COUNT = 5  # Ranked diagnostic peaks to persist in logs
    SPIN_PHASE_ENV_SNR_MIN = 1.75  # Envelope floor for phase-confirmed recovery
    SPIN_PHASE_SNR_MIN = 2.5  # Phase witness floor
    SPIN_PHASE_AGREEMENT_PCT = 10.0  # Max envelope/phase candidate difference
    MULTITAPER_SPIN_RPM_BAND = (1500.0, 11_000.0)
    MULTITAPER_RAIL_MARGIN_RPM = 250.0
    MULTITAPER_MIN_SAMPLES = 128

    BALL_SPEED_MATCH_TOLERANCE_MPH = 3.0
    IMPACT_TRANSITION_MIN_DELTA_MPH = 15.0
    IMPACT_TRANSITION_MAX_GAP_MS = 25.0

    def __init__(self, sample_rate: int = 30000):
        """Initialize processor with pre-computed window function.

        Args:
            sample_rate: Sample rate in Hz (default 30000). Lower rates
                extend the buffer duration at the cost of max detectable speed.
        """
        self.SAMPLE_RATE = sample_rate
        self.hanning_window = np.hanning(self.WINDOW_SIZE)

    def parse_capture(
        self,
        response: str,
        first_byte_timestamp: Optional[float] = None,
    ) -> Optional[IQCapture]:
        """
        Parse S! command response into IQCapture object.

        The response consists of multiple JSON lines:
        {"sample_time": "964.003"}
        {"trigger_time": "964.105"}
        {"I": [4096 integers...]}
        {"Q": [4096 integers...]}

        Args:
            response: Raw response string from S! command
            first_byte_timestamp: Host epoch timestamp when the first byte
                arrived from a hardware-triggered rolling-buffer dump.

        Returns:
            IQCapture object or None if parsing fails
        """
        try:
            sample_time = None
            trigger_time = None
            i_samples = None
            q_samples = None

            for line in response.strip().split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue

                try:
                    data = json.loads(line)

                    if "sample_time" in data:
                        sample_time = float(data["sample_time"])
                    elif "trigger_time" in data:
                        trigger_time = float(data["trigger_time"])
                    elif "I" in data:
                        i_samples = data["I"]
                    elif "Q" in data:
                        q_samples = data["Q"]

                except json.JSONDecodeError:
                    continue

            if all(v is not None for v in [sample_time, trigger_time, i_samples, q_samples]):
                # Both arrays must be the same length: the FFT pipeline pairs
                # them index-wise. Over USB CDC a short array was impossible,
                # but the J3 UART has no flow control, so a stalled reader can
                # drop bytes mid-dump. Truncation usually breaks the JSON and
                # is caught below; a mismatch that still parses must not reach
                # the processing stage as silently bad data.
                if len(i_samples) != len(q_samples) or not i_samples:
                    logger.warning(
                        "[PROCESSOR] I/Q length mismatch (I=%d, Q=%d) in a %d-byte "
                        "response — dropped bytes on the wire?",
                        len(i_samples),
                        len(q_samples),
                        len(response),
                    )
                    return None

                return IQCapture(
                    sample_time=sample_time,
                    trigger_time=trigger_time,
                    i_samples=i_samples,
                    q_samples=q_samples,
                    first_byte_timestamp=first_byte_timestamp,
                )

            # Log what's missing
            missing = []
            if sample_time is None:
                missing.append("sample_time")
            if trigger_time is None:
                missing.append("trigger_time")
            if i_samples is None:
                missing.append("I")
            if q_samples is None:
                missing.append("Q")

            # Include response preview in warning for debugging
            if len(response) < 500:
                response_preview = repr(response)
            else:
                response_preview = repr(response[:500]) + "..."
            logger.warning(
                "[PROCESSOR] Incomplete capture (missing: %s). Response (%d bytes): %s",
                ", ".join(missing),
                len(response),
                response_preview,
            )
            return None

        except Exception as e:
            logger.error("[PROCESSOR] Failed to parse capture: %s", e, exc_info=True)
            return None

    def _find_peaks(
        self,
        magnitude: np.ndarray,
        start: int,
        end: int,
    ) -> List[Tuple[int, float]]:
        """
        Find local maxima in a magnitude spectrum region.

        Uses numpy-only local maxima detection with greedy separation
        filtering to reject sidelobe duplicates.

        Args:
            magnitude: Full FFT magnitude array
            start: First bin to search (inclusive)
            end: Last bin to search (exclusive)

        Returns:
            List of (bin_index, magnitude) sorted by magnitude descending,
            capped at MAX_PEAKS_PER_DIRECTION.
        """
        if start >= end or end - start < 3:
            return []

        region = magnitude[start:end]

        # Local maxima: bins where value > both neighbors
        local_max = (region[1:-1] > region[:-2]) & (region[1:-1] > region[2:])
        # Convert to absolute bin indices
        peak_indices = np.where(local_max)[0] + start + 1

        # Filter by magnitude threshold
        candidates = [
            (int(idx), float(magnitude[idx]))
            for idx in peak_indices
            if magnitude[idx] >= self.MAGNITUDE_THRESHOLD
        ]

        # Sort by magnitude descending
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Greedy selection with minimum separation
        selected: List[Tuple[int, float]] = []
        for bin_idx, mag in candidates:
            if len(selected) >= self.MAX_PEAKS_PER_DIRECTION:
                break
            too_close = any(
                abs(bin_idx - sel_bin) < self.MIN_PEAK_SEPARATION_BINS for sel_bin, _ in selected
            )
            if not too_close:
                selected.append((bin_idx, mag))

        return selected

    def _process_block(
        self,
        i_block: np.ndarray,
        q_block: np.ndarray,
    ) -> List[Tuple[float, float, str]]:
        """
        Process a single 128-sample block through FFT.

        Steps:
        1. Remove DC offset (subtract mean)
        2. Scale to voltage (multiply by 3.3/4096)
        3. Apply Hanning window
        4. Create complex signal (I + jQ)
        5. FFT with zero-padding
        6. Find peak in outbound and inbound independently
        7. Return all peaks exceeding MAGNITUDE_THRESHOLD

        Args:
            i_block: 128 I samples
            q_block: 128 Q samples

        Returns:
            List of (speed_mph, magnitude, direction) tuples for each
            peak exceeding MAGNITUDE_THRESHOLD. May contain 0, 1, or 2 entries.
        """
        # Remove DC offset
        i_centered = i_block - np.mean(i_block)
        q_centered = q_block - np.mean(q_block)

        # Scale to voltage
        i_scaled = i_centered * (self.VOLTAGE_REF / self.ADC_RANGE)
        q_scaled = q_centered * (self.VOLTAGE_REF / self.ADC_RANGE)

        # Apply Hanning window
        i_windowed = i_scaled * self.hanning_window
        q_windowed = q_scaled * self.hanning_window

        # Create complex signal (standard I + jQ)
        complex_signal = i_windowed + 1j * q_windowed

        # FFT
        fft_result = np.fft.fft(complex_signal, self.FFT_SIZE)
        magnitude = np.abs(fft_result)

        half = self.FFT_SIZE // 2
        dc_mask = self.DC_MASK_BINS

        results: List[Tuple[float, float, str]] = []

        # OPS243 I/Q convention (empirically determined from diagnostic data):
        # - Positive frequencies (bins 1 to half-1) = OUTBOUND (away from radar)
        # - Negative frequencies (bins half+1 to end) = INBOUND (toward radar)

        # Outbound peaks: search positive frequencies, skipping DC mask bins
        if dc_mask < half:
            for peak_bin, peak_mag in self._find_peaks(magnitude, dc_mask, half):
                freq_hz = peak_bin * self.SAMPLE_RATE / self.FFT_SIZE
                speed_mps = freq_hz * self.WAVELENGTH_M / 2
                speed_mph = speed_mps * self.MPS_TO_MPH
                results.append((speed_mph, float(peak_mag), "outbound"))

        # Inbound peaks: search negative frequencies, skipping DC mask bins
        # Negative frequencies are in bins [half+1, FFT_SIZE-1].
        # FFT layout: bin FFT_SIZE-1 is freq -1 (nearest DC),
        #             bin half+1 is freq -(half-1) (nearest Nyquist).
        # DC leakage lives at the END of the array (bins near FFT_SIZE-1),
        # so we exclude bins [FFT_SIZE - dc_mask, FFT_SIZE-1].
        neg_start = half + 1
        neg_end = self.FFT_SIZE - dc_mask
        if neg_start < neg_end:
            for neg_peak_bin, neg_peak_mag in self._find_peaks(magnitude, neg_start, neg_end):
                abs_bin = self.FFT_SIZE - neg_peak_bin
                freq_hz = abs_bin * self.SAMPLE_RATE / self.FFT_SIZE
                speed_mps = freq_hz * self.WAVELENGTH_M / 2
                speed_mph = speed_mps * self.MPS_TO_MPH
                results.append((speed_mph, float(neg_peak_mag), "inbound"))

        return results

    def _iter_capture_windows(
        self,
        capture: IQCapture,
        step_size: int,
    ) -> Iterator[tuple[int, list[SpeedReading]]]:
        """Yield each FFT window once with its extracted readings.

        The production path needs both the 128-sample standard timeline and
        the 32-sample overlapping timeline. Standard windows are an exact
        subset of the overlapping windows, so exposing window boundaries lets
        ``process_capture`` build both views without repeating FFT work.

        Args:
            capture: Raw I/Q capture from radar
            step_size: Samples between FFT windows (128=standard, 32=overlapping)
        """
        i_data = np.asarray(capture.i_samples)
        q_data = np.asarray(capture.q_samples)

        start = 0
        while start + self.WINDOW_SIZE <= len(i_data):
            i_block = i_data[start : start + self.WINDOW_SIZE]
            q_block = q_data[start : start + self.WINDOW_SIZE]

            peaks = self._process_block(i_block, q_block)
            timestamp_ms = (start / self.SAMPLE_RATE) * 1000
            yield (
                start,
                [
                    SpeedReading(
                        speed_mph=speed_mph,
                        magnitude=magnitude,
                        timestamp_ms=timestamp_ms,
                        direction=direction,
                    )
                    for speed_mph, magnitude, direction in peaks
                ],
            )
            start += step_size

    def _process_capture(self, capture: IQCapture, step_size: int) -> SpeedTimeline:
        """Process a capture at one FFT window stride."""
        readings = [
            reading
            for _start, window_readings in self._iter_capture_windows(capture, step_size)
            for reading in window_readings
        ]
        return SpeedTimeline(
            readings=readings,
            sample_rate_hz=self.SAMPLE_RATE / step_size,
            capture=capture,
        )

    def _process_overlapping_with_standard(
        self,
        capture: IQCapture,
    ) -> tuple[SpeedTimeline, SpeedTimeline]:
        """Build standard and overlapping timelines from one FFT pass."""
        standard_readings = []
        overlapping_readings = []
        for start, window_readings in self._iter_capture_windows(
            capture,
            self.STEP_SIZE_OVERLAP,
        ):
            overlapping_readings.extend(window_readings)
            if start % self.STEP_SIZE_STANDARD == 0:
                standard_readings.extend(window_readings)

        standard = SpeedTimeline(
            readings=standard_readings,
            sample_rate_hz=self.SAMPLE_RATE / self.STEP_SIZE_STANDARD,
            capture=capture,
        )
        overlapping = SpeedTimeline(
            readings=overlapping_readings,
            sample_rate_hz=self.SAMPLE_RATE / self.STEP_SIZE_OVERLAP,
            capture=capture,
        )
        return standard, overlapping

    def process_standard(self, capture: IQCapture) -> SpeedTimeline:
        """
        Process capture with standard non-overlapping blocks (~234 Hz).

        Args:
            capture: Raw I/Q capture from radar

        Returns:
            SpeedTimeline with ~32 readings (one per 128-sample block)
        """
        return self._process_capture(capture, self.STEP_SIZE_STANDARD)

    def process_overlapping(self, capture: IQCapture) -> SpeedTimeline:
        """
        Process capture with overlapping blocks for high resolution (~937 Hz).

        This provides 4x the temporal resolution of standard processing,
        which is required for spin detection.

        Args:
            capture: Raw I/Q capture from radar

        Returns:
            SpeedTimeline with ~124 readings (32-sample stepping)
        """
        return self._process_capture(capture, self.STEP_SIZE_OVERLAP)

    def _local_noise_floor(
        self, valid_mag: np.ndarray, peak_idx: int, window_samples: int
    ) -> float:
        """Noise level around the peak, excluding the peak's main lobe.

        Takes the *max* of the two sides' medians: a genuine narrow tone
        has both sides at the true noise floor, while a peak riding the
        top of a red-noise slope has at least one side nearly as high as
        the peak itself. A pooled median would let the rolled-off side
        mask the slope.

        The exclusion zone scales with the Hann main-lobe width in
        zero-padded bins (2 x FFT_SIZE / window_samples per side), so a
        short analysis window's wider tone skirt is not counted as noise.
        """
        # A real Hann tone reaches its first null 2 x FFT/N padded bins
        # from the peak; beyond that only -31 dB sidelobes remain. The
        # neighborhood is the immediate shoulder just past the null — a
        # red-noise bump's tell-tale is that this shoulder stays near
        # peak level, and a wider window would dilute it with far bins
        # that have already rolled off.
        exclude = int(np.ceil(2 * self.SPIN_ENVELOPE_FFT_SIZE / max(window_samples, 1)))
        half_width = 3 * exclude
        lo = max(0, peak_idx - half_width)
        hi = min(len(valid_mag), peak_idx + half_width + 1)
        side_floors = []
        for side in (
            valid_mag[lo : max(lo, peak_idx - exclude)],
            valid_mag[min(hi, peak_idx + exclude + 1) : hi],
        ):
            live = side[side > 0]
            # A side needs a few live bins to give a meaningful median
            # (the leakage guard zeroes bins near the low band edge).
            if live.size >= 3:
                side_floors.append(float(np.median(live)))
        return max(side_floors) if side_floors else 0.0

    def _spin_peak_is_persistent(self, ball_envelope: np.ndarray, peak_freq_hz: float) -> bool:
        """Whether the picked seam tone is present in both halves of the window.

        A real seam tone persists for the whole ball flight; an envelope-noise
        fluctuation concentrates its energy in part of the window. Demoting
        non-persistent picks is the single-window analogue of tracking
        sideband traces across successive spectra.
        """
        half = len(ball_envelope) // 2
        if half < self.SPIN_MIN_SAMPLES // 2:
            return True  # window too short to split meaningfully
        for segment in (ball_envelope[:half], ball_envelope[half:]):
            seg = segment - np.mean(segment)
            windowed = seg * np.hanning(len(seg))
            magnitude = np.abs(np.fft.fft(windowed, self.SPIN_ENVELOPE_FFT_SIZE))
            freqs = np.fft.fftfreq(self.SPIN_ENVELOPE_FFT_SIZE, d=1 / self.SAMPLE_RATE)
            half_fft = self.SPIN_ENVELOPE_FFT_SIZE // 2
            magnitude, freqs = magnitude[1:half_fft], freqs[1:half_fft]
            valid = (freqs >= self.SPIN_MIN_SEAM_HZ) & (freqs <= self.SPIN_MAX_SEAM_HZ)
            valid_mag, valid_freqs = magnitude[valid], freqs[valid]
            if not np.any(valid_mag > 0):
                return False
            floor = float(np.median(valid_mag[valid_mag > 0]))
            # Tolerance: the half-window's natural resolution (±2 bins)
            tol_hz = 2.0 * self.SAMPLE_RATE / len(seg)
            near = np.abs(valid_freqs - peak_freq_hz) <= tol_hz
            if not near.any() or floor <= 0:
                return False
            near_max = float(valid_mag[near].max())
            # The pick must be above the floor AND (near-)dominant in
            # this half. A broad noise bump keeps energy near the pick
            # but its dominant frequency wanders across the bump; a real
            # tone stays pinned at the same frequency in every half.
            if near_max < 2.5 * floor or near_max < 0.7 * float(valid_mag.max()):
                return False
        return True

    def _ball_signal_end_sample(self, envelope: np.ndarray, start_sample: int) -> int:
        """Return the absolute sample index where the ball signal ends.

        Detects sustained loss of the bandpassed ball tone (net impact
        indoors) by comparing the smoothed envelope against the level at
        the start of the ball window. Returns len(envelope) when no loss
        is found, so outdoor shots keep the full capture.
        """
        tail = envelope[start_sample:]
        if len(tail) < self.SPIN_SIGNAL_LOSS_REF_SAMPLES:
            return len(envelope)

        kernel = np.ones(self.SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES)
        kernel /= self.SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES
        smoothed = np.convolve(tail, kernel, mode="same")
        reference = float(np.median(smoothed[: self.SPIN_SIGNAL_LOSS_REF_SAMPLES]))
        if reference <= 0:
            return len(envelope)

        below = smoothed < reference * self.SPIN_SIGNAL_LOSS_THRESHOLD
        hold = self.SPIN_SIGNAL_LOSS_HOLD_SAMPLES
        if len(below) < hold:
            return len(envelope)
        sustained = np.convolve(below.astype(float), np.ones(hold), mode="valid") >= hold
        if not sustained.any():
            return len(envelope)
        loss_offset = int(np.argmax(sustained))
        logger.info(
            "[PROCESSOR] Ball signal lost at %.1fms (window trimmed from %.1fms)",
            (start_sample + loss_offset) / self.SAMPLE_RATE * 1000,
            len(envelope) / self.SAMPLE_RATE * 1000,
        )
        return start_sample + loss_offset

    def detect_spin_multitaper(
        self,
        capture: IQCapture,
        ball_speed_mph: float,
        ball_timestamp_ms: float,
        expected_spin_rpm: Optional[float] = None,
    ) -> SpinResult:
        """Return an ungated multipath-aware multitaper spin candidate.

        This is the live version of the best-performing TrackMan-scored
        offline track. Evidence is logged as confidence, but never used to
        suppress a bounded candidate. The result remains ``experimental`` so
        it cannot drive spin-adjusted carry.
        """
        del expected_spin_rpm  # The evaluated estimator intentionally has no spin prior.
        iq, clipped_fraction = repair_clipped_iq(
            np.asarray(capture.i_samples),
            np.asarray(capture.q_samples),
        )
        if clipped_fraction > 0:
            logger.info(
                "[PROCESSOR] Multitaper repaired %.2f%% clipped I/Q samples",
                clipped_fraction * 100.0,
            )

        ball_speed_mps = ball_speed_mph / self.MPS_TO_MPH
        ball_doppler_hz = 2 * ball_speed_mps / self.WAVELENGTH_M
        nyquist = self.SAMPLE_RATE / 2
        low = max((ball_doppler_hz - self.SPIN_BANDPASS_BW_HZ) / nyquist, 0.001)
        high = min((ball_doppler_hz + self.SPIN_BANDPASS_BW_HZ) / nyquist, 0.999)
        if low >= high:
            return SpinResult.no_spin_detected(
                "Ball Doppler outside filter range",
                method="multitaper_ungated",
            )

        try:
            sos = butter(self.SPIN_BANDPASS_ORDER, [low, high], btype="band", output="sos")
            envelope = np.abs(sosfiltfilt(sos, iq))
        except Exception as exc:
            return SpinResult.no_spin_detected(
                f"Bandpass filter failed: {exc}",
                method="multitaper_ungated",
            )

        start_sample = max(0, int(ball_timestamp_ms * self.SAMPLE_RATE / 1000))
        end_sample = self._ball_signal_end_sample(envelope, start_sample)
        ball_envelope = envelope[start_sample:end_sample]
        transient_samples = int(self.SAMPLE_RATE / self.SPIN_BANDPASS_BW_HZ)
        if len(ball_envelope) > 2 * transient_samples + self.SPIN_MIN_SAMPLES:
            ball_envelope = ball_envelope[transient_samples:-transient_samples]
        if len(ball_envelope) < self.MULTITAPER_MIN_SAMPLES:
            return SpinResult.no_spin_detected(
                f"Ball signal too short ({len(ball_envelope)} samples, "
                f"need {self.MULTITAPER_MIN_SAMPLES})",
                method="multitaper_ungated",
            )

        envelope_mean = float(np.mean(ball_envelope))
        modulation_depth = (
            float(np.std(ball_envelope) / envelope_mean) if envelope_mean > 0 else None
        )
        try:
            estimate = estimate_multitaper_spin(
                ball_envelope,
                self.SAMPLE_RATE,
                spin_rpm_band=self.MULTITAPER_SPIN_RPM_BAND,
            )
        except (ValueError, np.linalg.LinAlgError) as exc:
            return SpinResult.no_spin_detected(
                f"Multitaper estimation failed: {exc}",
                modulation_depth=modulation_depth,
                method="multitaper_ungated",
            )

        min_rpm, max_rpm = self.MULTITAPER_SPIN_RPM_BAND
        at_lower_rail = estimate.spin_rpm <= min_rpm + self.MULTITAPER_RAIL_MARGIN_RPM
        at_upper_rail = estimate.spin_rpm >= max_rpm - self.MULTITAPER_RAIL_MARGIN_RPM
        window_seconds = len(ball_envelope) / self.SAMPLE_RATE
        confidence = min(0.59, estimate.peak_to_floor / (estimate.peak_to_floor + 5.0))
        candidate = SpinCandidate(
            rank=1,
            rpm=estimate.spin_rpm,
            freq_hz=estimate.spin_hz,
            relative_magnitude=1.0,
            snr=estimate.peak_to_floor,
            at_lower_rail=at_lower_rail,
            at_upper_rail=at_upper_rail,
            selected=True,
        )
        logger.info(
            "[PROCESSOR] Ungated multitaper spin: %.0f RPM, evidence=%.2f, "
            "fade=%.2f Hz, window=%.1fms",
            estimate.spin_rpm,
            estimate.peak_to_floor,
            estimate.fade_hz,
            window_seconds * 1000.0,
        )
        return SpinResult(
            spin_rpm=estimate.spin_rpm,
            confidence=confidence,
            snr=estimate.peak_to_floor,
            quality="experimental",
            method="multitaper_ungated",
            multipath_fade_hz=estimate.fade_hz,
            modulation_depth=modulation_depth,
            peak_freq_hz=estimate.spin_hz,
            seam_cycles=estimate.spin_hz * window_seconds,
            at_lower_rail=at_lower_rail,
            at_upper_rail=at_upper_rail,
            candidates=[candidate],
        )

    def detect_spin(
        self,
        capture: IQCapture,
        ball_speed_mph: float,
        ball_timestamp_ms: float,
        expected_spin_rpm: Optional[float] = None,
    ) -> SpinResult:
        """
        Detect spin rate from amplitude envelope of the ball's Doppler signal.

        The golf ball seam creates amplitude modulation at 1x spin rate as it
        crosses the radar beam once per revolution. We isolate the ball's
        Doppler signal with a bandpass filter, extract the amplitude envelope,
        then find the modulation frequency.

        Primary: FFT on the envelope. Autocorrelation is used only to
        confirm marginal FFT picks, not to override a disagreeing FFT peak.
        """
        i_data = np.array(capture.i_samples, dtype=np.float64)
        q_data = np.array(capture.q_samples, dtype=np.float64)

        # Remove DC offset
        i_data -= np.mean(i_data)
        q_data -= np.mean(q_data)

        # Complex I/Q signal
        iq = i_data + 1j * q_data

        # Ball Doppler frequency
        ball_speed_mps = ball_speed_mph / self.MPS_TO_MPH
        ball_doppler_hz = 2 * ball_speed_mps / self.WAVELENGTH_M

        # Bandpass filter around ball Doppler frequency
        nyquist = self.SAMPLE_RATE / 2
        low = (ball_doppler_hz - self.SPIN_BANDPASS_BW_HZ) / nyquist
        high = (ball_doppler_hz + self.SPIN_BANDPASS_BW_HZ) / nyquist

        # Clamp to valid range
        low = max(low, 0.001)
        high = min(high, 0.999)
        if low >= high:
            return SpinResult.no_spin_detected("Ball Doppler outside filter range")

        try:
            sos = butter(self.SPIN_BANDPASS_ORDER, [low, high], btype="band", output="sos")
            filtered = sosfiltfilt(sos, iq)
        except Exception as e:
            return SpinResult.no_spin_detected(f"Bandpass filter failed: {e}")

        # Amplitude envelope
        envelope = np.abs(filtered)

        # Trim to ball-present window: from ball onset to where the ball
        # signal dies (net impact indoors), falling back to capture end.
        start_sample = max(0, int(ball_timestamp_ms * self.SAMPLE_RATE / 1000))
        spin_window_start_sample = start_sample
        spin_window_end_sample = self._ball_signal_end_sample(envelope, start_sample)
        ball_envelope = envelope[spin_window_start_sample:spin_window_end_sample]

        # Trim filter transients from both ends. sosfiltfilt's internal
        # padding doesn't fully eliminate edge ripple in the envelope.
        # Trim 1/(bandwidth) seconds from each end as a conservative estimate.
        transient_samples = int(self.SAMPLE_RATE / self.SPIN_BANDPASS_BW_HZ)
        if len(ball_envelope) > 2 * transient_samples + self.SPIN_MIN_SAMPLES:
            spin_window_start_sample += transient_samples
            spin_window_end_sample -= transient_samples
            ball_envelope = ball_envelope[transient_samples:-transient_samples]

        if len(ball_envelope) < self.SPIN_MIN_SAMPLES:
            return SpinResult.no_spin_detected(
                f"Ball signal too short ({len(ball_envelope)} samples, "
                f"need {self.SPIN_MIN_SAMPLES})"
            )

        # Check modulation depth before proceeding. Real seam modulation
        # creates 1-5% amplitude variation; quantization noise creates <0.5%.
        weak_modulation = False
        modulation_depth: Optional[float] = None
        envelope_mean = np.mean(ball_envelope)
        envelope_std = np.std(ball_envelope)
        if envelope_mean > 0:
            modulation_depth = float(envelope_std / envelope_mean)
            if modulation_depth < 0.005:
                logger.info(
                    "[PROCESSOR] Spin rejected: modulation depth %.4f below 0.005",
                    modulation_depth,
                )
                return SpinResult.no_spin_detected(
                    f"Modulation depth too low ({modulation_depth:.4f})",
                    modulation_depth=modulation_depth,
                )

            # Flag weak modulation — above the noise floor (0.5%) but below
            # the level where we trust the envelope FFT peak (1%). Caps
            # confidence later to prevent marginal signals scoring 0.7+.
            weak_modulation = modulation_depth < 0.01

        # Remove DC and apply Hann window
        ball_envelope = ball_envelope - envelope_mean
        if envelope_std < 1e-6:
            return SpinResult.no_spin_detected(
                "Envelope variation too low",
                modulation_depth=modulation_depth,
            )
        # Detrend slow envelope drift (range falloff) so it cannot leak
        # into the low end of the seam band and shadow real driver spin.
        x = np.arange(len(ball_envelope), dtype=np.float64)
        trend = np.polyval(np.polyfit(x, ball_envelope, self.SPIN_DETREND_POLY_ORDER), x)
        ball_envelope = ball_envelope - trend
        windowed = ball_envelope * np.hanning(len(ball_envelope))

        # --- Primary: FFT on envelope ---
        fft_result = np.fft.fft(windowed, self.SPIN_ENVELOPE_FFT_SIZE)
        freqs = np.fft.fftfreq(self.SPIN_ENVELOPE_FFT_SIZE, d=1 / self.SAMPLE_RATE)
        half = self.SPIN_ENVELOPE_FFT_SIZE // 2
        magnitude = np.abs(fft_result[1:half])
        freqs = freqs[1:half]

        # Restrict to seam frequency range
        valid_mask = (freqs >= self.SPIN_MIN_SEAM_HZ) & (freqs <= self.SPIN_MAX_SEAM_HZ)
        if not np.any(valid_mask):
            return SpinResult.no_spin_detected(
                "No valid seam frequencies in range",
                modulation_depth=modulation_depth,
            )

        valid_mag = magnitude[valid_mask]
        valid_freqs = freqs[valid_mask]
        n_valid = len(valid_mag)

        # Zero the lowest N bins of the valid range. The Hann main lobe
        # is ~2 bins wide but envelope-DC leakage shoulders extend
        # several bins further. With FFT size 8192 and SR 30 kHz, each
        # bin is 3.66 Hz, so 5 bins ≈ 18 Hz of DC-dominated spectrum at
        # the bottom of the seam search range. This kills the rail-low
        # 2637/2856/3076 RPM pile-up observed on real driver captures.
        leakage = min(self.SPIN_DC_LEAKAGE_BINS, max(0, n_valid - 1))
        valid_mag = valid_mag.copy()
        if leakage > 0:
            valid_mag[:leakage] = 0

        peak_idx = self._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage,
            expected_spin_rpm=expected_spin_rpm,
        )
        peak_freq = float(valid_freqs[peak_idx])
        peak_mag = float(valid_mag[peak_idx])

        # Rail flags record where the envelope FFT peak landed.
        at_lower_rail = peak_idx < leakage + self.SPIN_UPPER_RAIL_BINS
        at_upper_rail = peak_idx >= n_valid - self.SPIN_UPPER_RAIL_BINS

        # SNR: peak vs the stricter of two noise floors. The global
        # median catches white noise; the local median catches red
        # envelope noise, which forms broad low-frequency bumps whose
        # peaks tower over the global median while sitting barely above
        # their own neighborhood. A real seam tone is a narrow line
        # (Hann main lobe) above its local floor; a noise-bump peak is
        # not. Without the local floor, toneless red noise reads as
        # high-confidence ~2000-3300 RPM spin.
        noise_floor = np.median(valid_mag[valid_mag > 0]) if np.any(valid_mag > 0) else 1.0
        local_floor = self._local_noise_floor(valid_mag, peak_idx, len(windowed))
        noise_floor = max(noise_floor, local_floor)
        fft_snr = peak_mag / noise_floor if noise_floor > 0 else 0
        spin_candidates = self._build_spin_candidates(
            valid_mag,
            valid_freqs,
            leakage,
            noise_floor,
            expected_spin_rpm=expected_spin_rpm,
            selected_idx=peak_idx,
        )

        # Seam frequency to spin RPM (seam = 1x spin, one seam crossing per revolution)
        spin_rpm = peak_freq * 60

        # Hard ceiling — reject anything above physical maximum.
        # The FFT mask should enforce this; belt-and-suspenders.
        max_rpm = self.SPIN_MAX_SEAM_HZ * 60
        if spin_rpm > max_rpm:
            return SpinResult.no_spin_detected(
                f"Spin {spin_rpm:.0f} RPM exceeds physical maximum ({max_rpm:.0f})",
                snr=fft_snr,
                modulation_depth=modulation_depth,
                peak_freq_hz=peak_freq,
                at_upper_rail=at_upper_rail,
                candidates=spin_candidates,
            )

        # Check minimum cycles in window
        window_seconds = len(ball_envelope) / self.SAMPLE_RATE
        seam_cycles = peak_freq * window_seconds

        logger.info(
            "[PROCESSOR] Spin envelope: peak=%.1f Hz (%.0f RPM), SNR=%.1f, "
            "cycles=%.1f, mod=%.4f, window=%.0fms, samples=%d, "
            "rail_lo=%s, rail_hi=%s",
            peak_freq,
            spin_rpm,
            fft_snr,
            seam_cycles,
            modulation_depth if modulation_depth is not None else float("nan"),
            window_seconds * 1000,
            len(ball_envelope),
            at_lower_rail,
            at_upper_rail,
        )

        # Reject upper-rail picks unless SNR is genuinely high. A peak
        # at the very top of the bandpass-shoulder region almost always
        # indicates filter-edge noise rather than a real seam tone.
        # Allow strong-SNR exceptions because legitimate ~11000 RPM
        # short-iron spin can land near the cap.
        if at_upper_rail and fft_snr < self.SPIN_SNR_HIGH:
            logger.warning(
                "[PROCESSOR] Spin rejected: upper-rail peak at %.0f RPM "
                "(SNR %.1f < %.1f, bandpass-shoulder noise)",
                spin_rpm,
                fft_snr,
                self.SPIN_SNR_HIGH,
            )
            return SpinResult.no_spin_detected(
                f"Upper-rail peak at {spin_rpm:.0f} RPM "
                f"(SNR {fft_snr:.1f} below high threshold {self.SPIN_SNR_HIGH:.0f})",
                snr=fft_snr,
                modulation_depth=modulation_depth,
                peak_freq_hz=peak_freq,
                seam_cycles=seam_cycles,
                at_upper_rail=True,
                candidates=spin_candidates,
            )

        # Lower-rail picks sit where residual envelope drift leaks past
        # the detrend. Treat them as suspect: require modulation depth
        # to clearly exceed the weak-modulation threshold AND medium
        # SNR (a real low-spin driver tone is strong; noise picks in
        # this zone hover just above the report floor).
        if at_lower_rail and (
            modulation_depth is None or modulation_depth < 0.012 or fft_snr < self.SPIN_SNR_MEDIUM
        ):
            logger.warning(
                "[PROCESSOR] Spin rejected: lower-rail peak at %.0f RPM "
                "(mod %.4f, SNR %.1f, envelope-drift leakage suspected)",
                spin_rpm,
                modulation_depth if modulation_depth is not None else float("nan"),
                fft_snr,
            )
            return SpinResult.no_spin_detected(
                f"Lower-rail peak at {spin_rpm:.0f} RPM "
                f"(mod {modulation_depth or 0:.4f}, SNR {fft_snr:.1f}, "
                f"envelope-drift leakage suspected)",
                snr=fft_snr,
                modulation_depth=modulation_depth,
                peak_freq_hz=peak_freq,
                seam_cycles=seam_cycles,
                at_lower_rail=True,
                candidates=spin_candidates,
            )

        # --- Fallback: Autocorrelation for marginal FFT ---
        autocorr_confirmed = False
        if fft_snr < self.SPIN_SNR_MEDIUM and fft_snr >= self.SPIN_SNR_MIN:
            norm = np.correlate(windowed, windowed, mode="full")
            norm = norm[len(norm) // 2 :]  # positive lags only
            if norm[0] > 0:
                norm = norm / norm[0]

            # Match the FFT search range exactly, including the
            # DC-leakage guard. Without this, the autocorrelation can
            # bypass the rail rejection by recommending a lag that
            # corresponds to a freq inside the leakage zone or at the
            # upper rail.
            leakage_floor_hz = (
                self.SPIN_MIN_SEAM_HZ
                + self.SPIN_DC_LEAKAGE_BINS * self.SAMPLE_RATE / self.SPIN_ENVELOPE_FFT_SIZE
            )
            min_lag = int(self.SAMPLE_RATE / self.SPIN_MAX_SEAM_HZ)
            max_lag = int(self.SAMPLE_RATE / leakage_floor_hz)
            max_lag = min(max_lag, len(norm) - 1)

            if min_lag < max_lag:
                search_region = norm[min_lag:max_lag]
                if len(search_region) > 0:
                    acorr_peak_idx = np.argmax(search_region)
                    acorr_peak_val = search_region[acorr_peak_idx]
                    acorr_lag = min_lag + acorr_peak_idx

                    if acorr_peak_val >= self.SPIN_AUTOCORR_MIN and acorr_lag > 0:
                        acorr_freq = self.SAMPLE_RATE / acorr_lag
                        acorr_rpm = acorr_freq * 60

                        if abs(acorr_rpm - spin_rpm) / max(spin_rpm, 1) < 0.10:
                            autocorr_confirmed = True
                            logger.info(
                                "[PROCESSOR] Spin autocorrelation confirms: %.0f RPM (corr=%.2f)",
                                acorr_rpm,
                                acorr_peak_val,
                            )
                        else:
                            # Autocorr peak disagrees with FFT peak. The
                            # autocorr peak at minimum lag is almost
                            # always the upper-rail rate (~12000 RPM),
                            # which previously overrode legitimate
                            # mid-range seam tones. Log the disagreement
                            # but keep the FFT pick.
                            logger.info(
                                "[PROCESSOR] Spin autocorrelation disagrees: "
                                "FFT=%.0f RPM, autocorr=%.0f RPM (corr=%.2f); "
                                "keeping FFT pick",
                                spin_rpm,
                                acorr_rpm,
                                acorr_peak_val,
                            )

        # --- Quality assessment ---
        if seam_cycles < self.SPIN_MIN_CYCLES:
            return SpinResult.no_spin_detected(
                f"Too few seam cycles ({seam_cycles:.1f}, need {self.SPIN_MIN_CYCLES})",
                snr=fft_snr,
                modulation_depth=modulation_depth,
                peak_freq_hz=peak_freq,
                seam_cycles=seam_cycles,
                at_lower_rail=at_lower_rail,
                at_upper_rail=at_upper_rail,
                candidates=spin_candidates,
            )

        if fft_snr < self.SPIN_SNR_MIN and not autocorr_confirmed:
            phase_confirmation = None
            if fft_snr >= self.SPIN_PHASE_ENV_SNR_MIN and not at_lower_rail and not at_upper_rail:
                phase_confirmation = self._phase_spin_confirmation(
                    filtered[spin_window_start_sample:spin_window_end_sample],
                    envelope_spin_rpm=spin_rpm,
                    expected_spin_rpm=expected_spin_rpm,
                )
                if phase_confirmation and phase_confirmation["confirmed"]:
                    logger.info(
                        "[PROCESSOR] Spin phase-confirmed low-SNR envelope: "
                        "envelope=%.0f RPM (SNR %.2f), phase=%.0f RPM "
                        "(SNR %.2f, agreement %.1f%%, method=%s)",
                        spin_rpm,
                        fft_snr,
                        phase_confirmation["rpm"],
                        phase_confirmation["snr"],
                        phase_confirmation["agreement_pct"],
                        phase_confirmation["method"],
                    )
                    return SpinResult(
                        spin_rpm=round(spin_rpm),
                        confidence=0.3,
                        snr=round(fft_snr, 2),
                        quality="low",
                        modulation_depth=modulation_depth,
                        peak_freq_hz=peak_freq,
                        seam_cycles=seam_cycles,
                        at_lower_rail=at_lower_rail,
                        at_upper_rail=at_upper_rail,
                        candidates=spin_candidates,
                        phase_method=phase_confirmation["method"],
                        phase_rpm=round(phase_confirmation["rpm"]),
                        phase_snr=round(phase_confirmation["snr"], 2),
                        phase_agreement_pct=round(phase_confirmation["agreement_pct"], 1),
                        phase_confirmed=True,
                    )

            logger.info(
                "[PROCESSOR] Spin rejected: SNR %.2f below %.1f "
                "(peak=%.0f RPM, cycles=%.1f, rail_lo=%s, rail_hi=%s)",
                fft_snr,
                self.SPIN_SNR_MIN,
                spin_rpm,
                seam_cycles,
                at_lower_rail,
                at_upper_rail,
            )
            return SpinResult.no_spin_detected(
                f"SNR too low ({fft_snr:.2f}, need {self.SPIN_SNR_MIN:.1f})",
                snr=fft_snr,
                modulation_depth=modulation_depth,
                peak_freq_hz=peak_freq,
                seam_cycles=seam_cycles,
                at_lower_rail=at_lower_rail,
                at_upper_rail=at_upper_rail,
                candidates=spin_candidates,
                phase_method=(phase_confirmation["method"] if phase_confirmation else None),
                phase_rpm=(
                    round(phase_confirmation["rpm"])
                    if phase_confirmation and phase_confirmation["rpm"]
                    else None
                ),
                phase_snr=(round(phase_confirmation["snr"], 2) if phase_confirmation else None),
                phase_agreement_pct=(
                    round(phase_confirmation["agreement_pct"], 1)
                    if phase_confirmation and phase_confirmation["agreement_pct"] is not None
                    else None
                ),
                phase_confirmed=False,
            )

        if fft_snr >= self.SPIN_SNR_HIGH and seam_cycles >= 5:
            quality = "high"
            confidence = 0.9
        elif fft_snr >= self.SPIN_SNR_HIGH and seam_cycles >= 3:
            quality = "high"
            confidence = 0.8
        elif fft_snr >= self.SPIN_SNR_MEDIUM and (seam_cycles >= 3 or autocorr_confirmed):
            quality = "medium"
            confidence = SPIN_CONFIDENCE_HIGH
        elif fft_snr >= self.SPIN_SNR_MEDIUM or autocorr_confirmed:
            quality = "low"
            confidence = 0.5
        elif fft_snr >= self.SPIN_SNR_MIN:
            quality = "low"
            confidence = 0.3
        else:
            quality = "low"
            confidence = 0.3

        # A seam tone must persist across the whole window — a pick whose
        # energy lives in only half the window is an envelope-noise
        # fluctuation, however sharp its spectral peak looks.
        if not self._spin_peak_is_persistent(ball_envelope, peak_freq):
            logger.info(
                "[PROCESSOR] Spin demoted: %.0f RPM peak not persistent across both window halves",
                spin_rpm,
            )
            confidence = min(confidence, 0.3)
            quality = "low"

        # Weak modulation caps confidence — the envelope FFT peak may be
        # noise rather than real seam modulation.
        if weak_modulation:
            confidence = min(confidence, 0.5)
            if quality == "high":
                quality = "medium"

        # Low-band picks (<= ~3100 RPM / ~52 Hz) can be real driver spin,
        # and the detrended FFT now reports their correct value — but at
        # a ~136 ms window this band has an irreducible noise-mimic
        # problem: lowpassed envelope noise produces sustained narrowband
        # components here that are physically indistinguishable from a
        # seam tone within a single capture (and real TrackMan sessions
        # showed exactly such artifacts on irons/wedges). Report the
        # value, but never as reliable. Lifting this cap requires an
        # estimator with more signal energy (dechirped Doppler sidebands)
        # or a longer observation window.
        if at_lower_rail or spin_rpm <= self.SPIN_LOW_BAND_SUSPECT_MAX_RPM:
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
            candidates=spin_candidates,
        )

    def _build_spin_candidates(
        self,
        valid_mag: np.ndarray,
        valid_freqs: np.ndarray,
        leakage_bins: int,
        noise_floor: float,
        expected_spin_rpm: Optional[float] = None,
        selected_idx: Optional[int] = None,
    ) -> List[SpinCandidate]:
        """Return ranked local envelope-FFT peaks for diagnostics."""
        if len(valid_mag) == 0 or not np.any(valid_mag > 0):
            return []

        strongest_idx = int(np.argmax(valid_mag))
        peak_indices = set(find_peaks(valid_mag, distance=2)[0])
        peak_indices.add(strongest_idx)
        if selected_idx is not None:
            peak_indices.add(int(selected_idx))

        strongest_mag = float(valid_mag[strongest_idx])
        lower_rail_limit = leakage_bins + self.SPIN_UPPER_RAIL_BINS
        upper_rail_start = len(valid_mag) - self.SPIN_UPPER_RAIL_BINS
        sorted_indices = sorted(
            peak_indices,
            key=lambda idx: float(valid_mag[idx]),
            reverse=True,
        )

        # Keep the top-N by magnitude, but always include the selected
        # candidate so rejected shots explain what the algorithm actually chose.
        kept = sorted_indices[: self.SPIN_CANDIDATE_COUNT]
        if selected_idx is not None and selected_idx not in kept:
            kept.append(int(selected_idx))

        candidates = []
        for rank, idx in enumerate(kept, start=1):
            freq_hz = float(valid_freqs[idx])
            rpm = freq_hz * 60
            expected_error_pct = None
            if expected_spin_rpm is not None and expected_spin_rpm > 0:
                expected_error_pct = abs(rpm - expected_spin_rpm) / expected_spin_rpm * 100
            candidates.append(
                SpinCandidate(
                    rank=rank,
                    rpm=rpm,
                    freq_hz=freq_hz,
                    relative_magnitude=float(valid_mag[idx] / strongest_mag),
                    snr=float(valid_mag[idx] / noise_floor) if noise_floor > 0 else 0.0,
                    at_lower_rail=bool(idx < lower_rail_limit),
                    at_upper_rail=bool(idx >= upper_rail_start),
                    expected_spin_error_pct=expected_error_pct,
                    selected=bool(selected_idx is not None and idx == selected_idx),
                )
            )

        return candidates

    def _phase_spin_confirmation(
        self,
        filtered_iq_window: np.ndarray,
        envelope_spin_rpm: float,
        expected_spin_rpm: Optional[float] = None,
    ) -> Optional[dict]:
        """Return a phase witness when it tightly confirms envelope spin."""
        if len(filtered_iq_window) < self.SPIN_MIN_SAMPLES:
            return None

        phase = np.unwrap(np.angle(filtered_iq_window))
        x = np.arange(len(phase), dtype=np.float64)
        slope, intercept = np.polyfit(x, phase, 1)
        phase_residual = phase - (slope * x + intercept)

        witnesses = [
            self._phase_spin_candidate(
                phase_residual,
                sample_rate_hz=self.SAMPLE_RATE,
                method="phase_residual",
                expected_spin_rpm=expected_spin_rpm,
                envelope_spin_rpm=envelope_spin_rpm,
            )
        ]

        instant_frequency = np.diff(phase) * self.SAMPLE_RATE / (2 * np.pi)
        if len(instant_frequency) >= self.SPIN_MIN_SAMPLES:
            x_freq = np.arange(len(instant_frequency), dtype=np.float64)
            slope_freq, intercept_freq = np.polyfit(x_freq, instant_frequency, 1)
            frequency_residual = instant_frequency - (slope_freq * x_freq + intercept_freq)
            witnesses.append(
                self._phase_spin_candidate(
                    frequency_residual,
                    sample_rate_hz=self.SAMPLE_RATE,
                    method="instant_frequency",
                    expected_spin_rpm=expected_spin_rpm,
                    envelope_spin_rpm=envelope_spin_rpm,
                )
            )

        valid = [witness for witness in witnesses if witness is not None]
        if not valid:
            return None

        valid.sort(
            key=lambda witness: (
                not witness["confirmed"],
                witness["agreement_pct"],
                -witness["snr"],
            )
        )
        return valid[0]

    def _phase_spin_candidate(
        self,
        signal: np.ndarray,
        sample_rate_hz: float,
        method: str,
        expected_spin_rpm: Optional[float],
        envelope_spin_rpm: float,
    ) -> Optional[dict]:
        """Extract one phase-derived spin candidate from a residual signal."""
        if len(signal) < self.SPIN_MIN_SAMPLES:
            return None

        centered = signal - np.mean(signal)
        if np.std(centered) < 1e-9:
            return None

        windowed = centered * np.hanning(len(centered))
        fft_result = np.fft.fft(windowed, self.SPIN_ENVELOPE_FFT_SIZE)
        freqs = np.fft.fftfreq(self.SPIN_ENVELOPE_FFT_SIZE, d=1 / sample_rate_hz)
        half = self.SPIN_ENVELOPE_FFT_SIZE // 2
        magnitude = np.abs(fft_result[1:half])
        freqs = freqs[1:half]
        valid_mask = (freqs >= self.SPIN_MIN_SEAM_HZ) & (freqs <= self.SPIN_MAX_SEAM_HZ)
        if not np.any(valid_mask):
            return None

        valid_mag = magnitude[valid_mask].copy()
        valid_freqs = freqs[valid_mask]
        if not np.any(valid_mag > 0):
            return None

        n_valid = len(valid_mag)
        leakage = min(self.SPIN_DC_LEAKAGE_BINS, max(0, n_valid - 1))
        if leakage > 0:
            valid_mag[:leakage] = 0
        if not np.any(valid_mag > 0):
            return None

        peak_idx = self._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage,
            expected_spin_rpm=expected_spin_rpm,
        )
        peak_freq = float(valid_freqs[peak_idx])
        rpm = peak_freq * 60
        noise_floor = np.median(valid_mag[valid_mag > 0])
        snr = float(valid_mag[peak_idx] / noise_floor) if noise_floor > 0 else 0.0
        at_lower_rail = peak_idx < leakage + self.SPIN_UPPER_RAIL_BINS
        at_upper_rail = peak_idx >= n_valid - self.SPIN_UPPER_RAIL_BINS
        agreement_pct = abs(rpm - envelope_spin_rpm) / max(envelope_spin_rpm, 1) * 100
        confirmed = bool(
            snr >= self.SPIN_PHASE_SNR_MIN
            and not at_lower_rail
            and not at_upper_rail
            and agreement_pct <= self.SPIN_PHASE_AGREEMENT_PCT
        )
        return {
            "method": method,
            "rpm": rpm,
            "snr": snr,
            "agreement_pct": agreement_pct,
            "at_lower_rail": at_lower_rail,
            "at_upper_rail": at_upper_rail,
            "confirmed": confirmed,
        }

    def _select_spin_peak(
        self,
        valid_mag: np.ndarray,
        valid_freqs: np.ndarray,
        leakage_bins: int,
        expected_spin_rpm: Optional[float] = None,
    ) -> int:
        """Pick an envelope-FFT peak, optionally using expected spin as a prior.

        The largest envelope peak often lands at the low edge of the search
        range on real captures. When a club/ball-speed spin expectation is
        available, use it only as a tie-breaker between visible spectral peaks:
        prefer a non-rail local peak near the expected range if it has enough
        relative strength. Later SNR/cycle gates still decide whether the
        selected candidate is reportable.
        """
        strongest_idx = int(np.argmax(valid_mag))
        if (
            expected_spin_rpm is None
            or expected_spin_rpm <= 0
            or len(valid_mag) == 0
            or valid_mag[strongest_idx] <= 0
        ):
            return strongest_idx

        peak_indices = set(find_peaks(valid_mag, distance=2)[0])
        peak_indices.add(strongest_idx)
        if not peak_indices:
            return strongest_idx

        lower_rail_limit = leakage_bins + self.SPIN_UPPER_RAIL_BINS
        upper_rail_start = len(valid_mag) - self.SPIN_UPPER_RAIL_BINS
        strongest_rpm = float(valid_freqs[strongest_idx] * 60)
        strongest_rel_error = abs(strongest_rpm - expected_spin_rpm) / expected_spin_rpm
        strongest_is_lower_rail = strongest_idx < lower_rail_limit
        strongest_is_implausible_lower_rail = (
            strongest_is_lower_rail
            and expected_spin_rpm >= self.SPIN_HIGH_PRIOR_MIN_RPM
            and strongest_rpm < expected_spin_rpm * self.SPIN_IMPLAUSIBLE_LOWER_RAIL_FRACTION
        )

        candidates = []
        lower_rail_recovery_candidates = []
        for idx in peak_indices:
            if idx < lower_rail_limit or idx >= upper_rail_start:
                continue
            rel_mag = float(valid_mag[idx] / valid_mag[strongest_idx])
            rpm = float(valid_freqs[idx] * 60)
            rel_error = abs(rpm - expected_spin_rpm) / expected_spin_rpm
            if (
                rel_mag >= self.SPIN_PRIOR_MIN_RELATIVE_MAG
                and rel_error <= self.SPIN_PRIOR_MAX_RELATIVE_ERROR
            ):
                candidates.append((rel_error, -rel_mag, int(idx)))
            if (
                strongest_is_implausible_lower_rail
                and rel_mag >= self.SPIN_LOWER_RAIL_RECOVERY_MIN_RELATIVE_MAG
                and rel_error <= self.SPIN_LOWER_RAIL_RECOVERY_MAX_RELATIVE_ERROR
            ):
                lower_rail_recovery_candidates.append((-rel_mag, rel_error, int(idx)))

        if candidates:
            best_error, _, best_idx = min(candidates)
            strongest_is_far = strongest_rel_error > self.SPIN_PRIOR_STRONGEST_FAR_ERROR
        elif lower_rail_recovery_candidates:
            _, best_error, best_idx = min(lower_rail_recovery_candidates)
            logger.info(
                "[PROCESSOR] Spin high-prior recovery selected %.0f RPM over "
                "implausible lower rail %.0f RPM (expected %.0f RPM)",
                valid_freqs[best_idx] * 60,
                strongest_rpm,
                expected_spin_rpm,
            )
            return best_idx
        else:
            return strongest_idx

        if strongest_is_lower_rail or (strongest_is_far and best_error < strongest_rel_error):
            logger.info(
                "[PROCESSOR] Spin prior selected %.0f RPM over strongest %.0f RPM "
                "(expected %.0f RPM)",
                valid_freqs[best_idx] * 60,
                strongest_rpm,
                expected_spin_rpm,
            )
            return best_idx

        return strongest_idx

    def find_club_speed(
        self,
        timeline: SpeedTimeline,
        ball_speed_mph: float,
        ball_timestamp_ms: float,
        max_window_ms: float = 100,
        club_type: ClubType = ClubType.UNKNOWN,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Find club-head speed from the upper Doppler branch before impact.

        The club head normally forms the fastest accelerating branch before
        the stable ball-speed branch appears. Its return can be weaker than the
        shaft, so magnitude alone is not a reliable identity selector. The
        final FFT windows also mix club and ball energy; a robust percentile of
        the preceding plateau avoids that contamination.

        High-loft wedge club and ball speeds can overlap, producing a smooth
        merge rather than a distinct transition. For that case, use the terminal
        upper branch instead of imposing an iron/driver smash-factor bracket.

        Args:
            timeline: Speed timeline
            ball_speed_mph: Detected ball speed
            ball_timestamp_ms: When ball was detected
            max_window_ms: Maximum time before ball to search
            club_type: Selected club, used for smooth wedge transitions

        Returns:
            Tuple of (club_speed_mph, club_timestamp_ms) or (None, None)
        """
        legacy_speed, legacy_timestamp = self._find_club_speed_by_magnitude(
            timeline,
            ball_speed_mph,
            ball_timestamp_ms,
            max_window_ms,
        )

        history_ms = min(max_window_ms, self.CLUB_BRANCH_HISTORY_MS)
        upper_by_timestamp: dict[float, SpeedReading] = {}
        for reading in timeline.readings:
            relative_ms = reading.timestamp_ms - ball_timestamp_ms
            if not reading.is_outbound:
                continue
            if not -history_ms <= relative_ms <= self.CLUB_TERMINAL_END_MS:
                continue
            if not 0 < reading.speed_mph <= self.CLUB_MAX_PLAUSIBLE_SPEED_MPH:
                continue
            current = upper_by_timestamp.get(reading.timestamp_ms)
            if current is None or reading.speed_mph > current.speed_mph:
                upper_by_timestamp[reading.timestamp_ms] = reading

        upper_branch = sorted(
            upper_by_timestamp.values(),
            key=lambda reading: reading.timestamp_ms,
        )
        if len(upper_branch) < 3:
            return legacy_speed, legacy_timestamp

        if club_type in self.CLUB_SMOOTH_MERGE_TYPES:
            terminal = [
                reading
                for reading in upper_branch
                if self.CLUB_TERMINAL_START_MS
                <= reading.timestamp_ms - ball_timestamp_ms
                <= self.CLUB_TERMINAL_END_MS
            ]
            terminal_pick = self._quantile_reading(terminal, 0.50)
            if terminal_pick is not None:
                return terminal_pick.speed_mph, terminal_pick.timestamp_ms

        ball_tolerance_mph = max(
            self.CLUB_BALL_MATCH_MIN_MPH,
            ball_speed_mph * self.CLUB_BALL_MATCH_FRACTION,
        )
        onset_timestamp_ms: Optional[float] = None
        for first, second in zip(upper_branch, upper_branch[1:]):
            first_relative_ms = first.timestamp_ms - ball_timestamp_ms
            if first_relative_ms < -self.CLUB_BALL_ONSET_SEARCH_MS:
                continue
            if (
                abs(first.speed_mph - ball_speed_mph) <= ball_tolerance_mph
                and abs(second.speed_mph - ball_speed_mph) <= ball_tolerance_mph
            ):
                onset_timestamp_ms = first.timestamp_ms
                break

        if onset_timestamp_ms is None:
            # One overlapping FFT step before the mixed club/ball guard.
            onset_timestamp_ms = ball_timestamp_ms - 3.2

        plateau_start_ms = onset_timestamp_ms - self.CLUB_PLATEAU_LOOKBACK_MS
        plateau_end_ms = onset_timestamp_ms - self.CLUB_PLATEAU_GUARD_MS
        plateau = [
            reading
            for reading in upper_branch
            if plateau_start_ms <= reading.timestamp_ms <= plateau_end_ms
        ]
        plateau_pick = self._quantile_reading(plateau, self.CLUB_PLATEAU_QUANTILE)
        if plateau_pick is None:
            return legacy_speed, legacy_timestamp

        if plateau_pick.speed_mph > ball_speed_mph * self.CLUB_BALL_CONTAMINATION_RATIO:
            return legacy_speed, legacy_timestamp

        return plateau_pick.speed_mph, plateau_pick.timestamp_ms

    @staticmethod
    def _quantile_reading(readings: list[SpeedReading], quantile: float) -> Optional[SpeedReading]:
        """Return the observed reading nearest a speed quantile."""
        if not readings:
            return None
        target_speed = float(np.quantile([reading.speed_mph for reading in readings], quantile))
        return min(
            readings,
            key=lambda reading: (
                abs(reading.speed_mph - target_speed),
                -reading.timestamp_ms,
            ),
        )

    @staticmethod
    def _find_club_speed_by_magnitude(
        timeline: SpeedTimeline,
        ball_speed_mph: float,
        ball_timestamp_ms: float,
        max_window_ms: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Provide a conservative fallback when no usable branch exists."""
        min_club = ball_speed_mph * 0.67
        max_club = ball_speed_mph * 0.85

        # Get readings at or before ball timestamp (covers software trigger
        # latency where club and ball appear in the same FFT block)
        pre_ball = [r for r in timeline.readings if r.timestamp_ms <= ball_timestamp_ms]

        # Filter to valid club candidates, excluding the ball reading itself
        candidates = [
            r
            for r in pre_ball
            if r.is_outbound
            and min_club <= r.speed_mph <= max_club
            and ball_timestamp_ms - r.timestamp_ms <= max_window_ms
            and abs(r.speed_mph - ball_speed_mph) > 1.0  # exclude ball
        ]

        if not candidates:
            return None, None

        club_reading = max(candidates, key=lambda r: r.magnitude)

        return club_reading.speed_mph, club_reading.timestamp_ms

    def _reading_center_ms(self, reading: SpeedReading) -> float:
        """Return the center time of the FFT window for a reading."""
        return reading.timestamp_ms + (self.WINDOW_SIZE / self.SAMPLE_RATE) * 500.0

    def estimate_impact(
        self,
        timeline: SpeedTimeline,
        ball_speed_mph: float,
        club_speed_mph: Optional[float] = None,
        capture: Optional[IQCapture] = None,
    ) -> ImpactEstimate:
        """
        Estimate strike time from the OPS club-to-ball speed transition.

        If the timeline has a clean jump from a club-like outbound speed to the
        first ball-like outbound speed, use the midpoint of those frame centers.
        If that jump is under the configured delta threshold, or either side of
        the transition is missing, fall back to the hardware sound trigger.
        """
        sound_trigger_ms = capture.trigger_offset_ms if capture is not None else None

        def fallback(
            reason: str,
            *,
            first_ball: Optional[SpeedReading] = None,
            last_club: Optional[SpeedReading] = None,
            speed_delta_mph: Optional[float] = None,
            transition_gap_ms: Optional[float] = None,
        ) -> ImpactEstimate:
            first_ball_center_ms = (
                self._reading_center_ms(first_ball) if first_ball is not None else None
            )
            last_club_center_ms = (
                self._reading_center_ms(last_club) if last_club is not None else None
            )
            return ImpactEstimate(
                timestamp_ms=sound_trigger_ms,
                source=("sound_trigger" if sound_trigger_ms is not None else "unavailable"),
                reason=reason,
                speed_delta_mph=speed_delta_mph,
                transition_gap_ms=transition_gap_ms,
                last_club_speed_mph=(last_club.speed_mph if last_club is not None else None),
                last_club_timestamp_ms=(last_club.timestamp_ms if last_club is not None else None),
                last_club_center_ms=last_club_center_ms,
                first_ball_speed_mph=(first_ball.speed_mph if first_ball is not None else None),
                first_ball_timestamp_ms=(
                    first_ball.timestamp_ms if first_ball is not None else None
                ),
                first_ball_center_ms=first_ball_center_ms,
                min_transition_delta_mph=self.IMPACT_TRANSITION_MIN_DELTA_MPH,
            )

        outbound = sorted(
            (r for r in timeline.readings if r.is_outbound),
            key=lambda r: (r.timestamp_ms, -r.magnitude),
        )
        ball_candidates = [
            r
            for r in outbound
            if abs(r.speed_mph - ball_speed_mph) <= self.BALL_SPEED_MATCH_TOLERANCE_MPH
        ]
        if not ball_candidates:
            return fallback("no_ball_candidate")

        first_ball = ball_candidates[0]
        first_ball_center_ms = self._reading_center_ms(first_ball)
        min_club_speed = ball_speed_mph * 0.67
        max_club_speed = ball_speed_mph * 0.85

        transition_candidates = []
        for reading in outbound:
            if reading is first_ball:
                continue
            reading_center_ms = self._reading_center_ms(reading)
            transition_gap_ms = first_ball_center_ms - reading_center_ms
            if transition_gap_ms < 0:
                continue
            if transition_gap_ms > self.IMPACT_TRANSITION_MAX_GAP_MS:
                continue
            if not min_club_speed <= reading.speed_mph <= max_club_speed:
                continue
            if reading.speed_mph >= first_ball.speed_mph - 1.0:
                continue
            transition_candidates.append(reading)

        if club_speed_mph is not None:
            club_nearby = [
                r for r in transition_candidates if abs(r.speed_mph - club_speed_mph) <= 5.0
            ]
            if club_nearby:
                transition_candidates = club_nearby

        if not transition_candidates:
            return fallback("no_club_transition_candidate", first_ball=first_ball)

        last_club = max(
            transition_candidates,
            key=lambda r: (self._reading_center_ms(r), r.magnitude),
        )
        last_club_center_ms = self._reading_center_ms(last_club)
        transition_gap_ms = first_ball_center_ms - last_club_center_ms
        speed_delta_mph = first_ball.speed_mph - last_club.speed_mph

        if speed_delta_mph < self.IMPACT_TRANSITION_MIN_DELTA_MPH:
            return fallback(
                "speed_delta_below_threshold",
                first_ball=first_ball,
                last_club=last_club,
                speed_delta_mph=speed_delta_mph,
                transition_gap_ms=transition_gap_ms,
            )

        return ImpactEstimate(
            timestamp_ms=(last_club_center_ms + first_ball_center_ms) / 2.0,
            source="ops_transition",
            speed_delta_mph=speed_delta_mph,
            transition_gap_ms=transition_gap_ms,
            last_club_speed_mph=last_club.speed_mph,
            last_club_timestamp_ms=last_club.timestamp_ms,
            last_club_center_ms=last_club_center_ms,
            first_ball_speed_mph=first_ball.speed_mph,
            first_ball_timestamp_ms=first_ball.timestamp_ms,
            first_ball_center_ms=first_ball_center_ms,
            min_transition_delta_mph=self.IMPACT_TRANSITION_MIN_DELTA_MPH,
        )

    @staticmethod
    def _find_consistent_ball_speed(outbound_readings: list) -> float:
        """Find the ball speed that appears most consistently across FFT windows.

        Bins outbound readings to 1-mph buckets and returns the peak of the
        densest cluster. This is robust against single-window outliers (noise
        spikes, harmonics) that would fool a raw max().

        The ball produces a consistent Doppler return across many windows,
        while noise spikes appear in only 1-2 windows.
        """
        if not outbound_readings:
            return 0.0

        speeds = [r.speed_mph for r in outbound_readings]

        # Bin to 1-mph buckets, find the mode
        from collections import Counter

        binned = Counter(round(s) for s in speeds)

        # The ball is the highest-speed cluster with significant repetition.
        # Sort bins by count descending, then by speed descending to break ties.
        # Require at least 2 occurrences to be considered a real signal.
        frequent = [(spd, cnt) for spd, cnt in binned.items() if cnt >= 2]
        if not frequent:
            # No repeated speeds — fall back to max
            return max(speeds)

        # Among bins with meaningful repetition, pick the fastest.
        # The ball is always the fastest real signal; club is slower.
        frequent.sort(key=lambda x: x[0], reverse=True)
        ball_bin = frequent[0][0]

        # Log if max speed differs significantly from mode (outlier rejected)
        max_speed = max(speeds)
        if max_speed > ball_bin + 10:
            logger.info(
                "[PROCESSOR] Ball speed outlier rejected: max=%.1f, mode=%.1f mph (%d occurrences)",
                max_speed,
                float(ball_bin),
                frequent[0][1],
            )

        # Return the actual max speed within ±2 mph of the mode bin
        # for sub-mph precision
        nearby = [s for s in speeds if abs(s - ball_bin) <= 2.0]
        return max(nearby) if nearby else float(ball_bin)

    def process_capture(
        self,
        capture: IQCapture,
        expected_spin_rpm: Optional[float] = None,
        expected_spin_for_ball_speed: Optional[Callable[[float], float]] = None,
        club_type: ClubType = ClubType.UNKNOWN,
    ) -> Optional[ProcessedCapture]:
        """
        Full processing pipeline: I/Q -> speeds -> spin -> shot data.

        Args:
            capture: Raw I/Q capture from radar
            expected_spin_rpm: Optional club/ball-speed spin prior used
                only to choose among visible envelope peaks.
            expected_spin_for_ball_speed: Optional callback for deriving a
                spin prior after ball speed has been detected.
            club_type: Selected club for club-speed transition interpretation.

        Returns:
            ProcessedCapture with all extracted data, or None if processing fails
        """
        # Use non-overlapping (standard) processing to find ball speed.
        # Ball speed = the most-repeated speed across independent windows,
        # NOT the maximum. A single FFT window with a noise spike at 200 mph
        # would poison max(), but mode-based detection ignores it because
        # the real ball signal appears consistently in many windows.
        standard, timeline = self._process_overlapping_with_standard(capture)
        std_outbound = [r for r in standard.readings if r.is_outbound]
        if not std_outbound:
            logger.warning("[PROCESSOR] No outbound readings found")
            return None

        ball_speed_mph = self._find_consistent_ball_speed(std_outbound)
        logger.info(
            "[PROCESSOR] Ball speed: %.1f mph (mode-based, %d outbound readings)",
            ball_speed_mph,
            len(std_outbound),
        )

        if not timeline.readings:
            logger.warning("[PROCESSOR] No valid readings extracted from capture")
            return None

        # Find the ball in the overlapping timeline at the standard-detected speed
        # (within tolerance) to get the precise timestamp for spin analysis
        outbound = [r for r in timeline.readings if r.is_outbound]
        ball_candidates = [
            r
            for r in outbound
            if abs(r.speed_mph - ball_speed_mph) <= self.BALL_SPEED_MATCH_TOLERANCE_MPH
        ]
        if ball_candidates:
            ball_reading = max(ball_candidates, key=lambda r: r.magnitude)
        elif outbound:
            # Fallback: closest speed to standard result
            ball_reading = min(outbound, key=lambda r: abs(r.speed_mph - ball_speed_mph))
        else:
            # No outbound readings in overlapping timeline at all —
            # use midpoint of capture as best-guess timestamp
            ball_reading = None
            logger.warning("[PROCESSOR] No outbound readings in overlapping timeline")

        ball_timestamp_ms = ball_reading.timestamp_ms if ball_reading else 68.0

        # Find club speed
        club_speed_mph, club_timestamp_ms = self.find_club_speed(
            timeline,
            ball_speed_mph,
            ball_timestamp_ms,
            club_type=club_type,
        )
        if club_speed_mph is not None:
            logger.info(
                "[PROCESSOR] Club speed: %.1f mph at %.1fms before ball",
                club_speed_mph,
                ball_timestamp_ms - club_timestamp_ms,
            )
        else:
            logger.debug("[PROCESSOR] No club speed found (ball=%.1f mph)", ball_speed_mph)

        impact = self.estimate_impact(
            timeline,
            ball_speed_mph,
            club_speed_mph=club_speed_mph,
            capture=capture,
        )
        if impact.source == "ops_transition":
            logger.info(
                "[PROCESSOR] Impact estimate: OPS transition %.1fms "
                "(club %.1f mph @ %.1fms -> ball %.1f mph @ %.1fms, "
                "delta=%.1f mph)",
                impact.timestamp_ms,
                impact.last_club_speed_mph,
                impact.last_club_center_ms,
                impact.first_ball_speed_mph,
                impact.first_ball_center_ms,
                impact.speed_delta_mph,
            )
        else:
            logger.info(
                "[PROCESSOR] Impact estimate: %s %.1fms (%s)",
                impact.source,
                impact.timestamp_ms if impact.timestamp_ms is not None else float("nan"),
                impact.reason,
            )

        if expected_spin_rpm is None and expected_spin_for_ball_speed is not None:
            expected_spin_rpm = expected_spin_for_ball_speed(ball_speed_mph)

        # Spin detection via amplitude envelope demodulation on raw I/Q
        spin = self.detect_spin_multitaper(
            capture,
            ball_speed_mph,
            ball_timestamp_ms,
            expected_spin_rpm=expected_spin_rpm,
        )

        logger.info(
            "[PROCESSOR] Spin result: %.0f RPM, SNR=%.2f, quality=%s",
            spin.spin_rpm,
            spin.snr,
            spin.quality,
        )

        return ProcessedCapture(
            timeline=timeline,
            ball_speed_mph=ball_speed_mph,
            ball_timestamp_ms=ball_timestamp_ms,
            club_speed_mph=club_speed_mph,
            club_timestamp_ms=club_timestamp_ms,
            spin=spin,
            capture=capture,
            impact=impact,
        )
