"""
Data types for rolling buffer mode.

These types represent the raw I/Q data captured from the radar,
the processed speed timeline, and spin detection results.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..launch_monitor import SPIN_CONFIDENCE_RELIABLE, compute_smash_factor


@dataclass
class IQCapture:
    """
    Raw I/Q data captured from S! command in rolling buffer mode.

    The radar returns 4096 samples each for I (in-phase) and Q (quadrature)
    components of the Doppler signal. At 30ksps, this represents ~136ms of data.

    Attributes:
        sample_time: Radar timestamp when sampling started (seconds since power-on)
        trigger_time: Radar timestamp when trigger fired
        i_samples: 4096 in-phase samples (raw ADC values, 0-4095)
        q_samples: 4096 quadrature samples (raw ADC values, 0-4095)
        timestamp: Python timestamp when capture was received
        first_byte_timestamp: Host epoch timestamp when the first byte of a
            hardware-triggered capture arrived from the radar.
        trigger_timestamp: Host epoch timestamp when the hardware trigger fired,
            derived from first_byte_timestamp and the post-trigger buffer span.
    """

    sample_time: float
    trigger_time: float
    i_samples: List[int]
    q_samples: List[int]
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    first_byte_timestamp: Optional[float] = None
    trigger_timestamp: Optional[float] = None
    trigger_timestamp_source: Optional[str] = None
    clock_sync_offset_s: Optional[float] = None

    def __post_init__(self) -> None:
        """Infer the hardware trigger epoch when first-byte timing is available."""
        if self.trigger_timestamp is None:
            self.apply_trigger_timestamp_from_first_byte()

    @property
    def num_samples(self) -> int:
        """Total number of I/Q sample pairs."""
        return len(self.i_samples)

    @property
    def duration_ms(self) -> float:
        """Duration of capture in milliseconds (at 30ksps)."""
        return (self.num_samples / 30000) * 1000

    @property
    def trigger_offset_ms(self) -> float:
        """Time offset of trigger from start of buffer in milliseconds."""
        return (self.trigger_time - self.sample_time) * 1000

    @property
    def post_trigger_duration_ms(self) -> float:
        """Duration of capture sampled after the hardware trigger."""
        return min(
            max(self.duration_ms - self.trigger_offset_ms, 0.0),
            self.duration_ms,
        )

    def infer_trigger_timestamp_from_first_byte(self) -> Optional[float]:
        """Return hardware trigger epoch inferred from first response byte time."""
        if self.first_byte_timestamp is None:
            return None
        return self.first_byte_timestamp - self.post_trigger_duration_ms / 1000.0

    def apply_trigger_timestamp_from_first_byte(self) -> Optional[float]:
        """Set trigger_timestamp from first_byte_timestamp when possible."""
        inferred = self.infer_trigger_timestamp_from_first_byte()
        if inferred is not None:
            self.trigger_timestamp = inferred
            self.trigger_timestamp_source = "first_byte"
        return inferred

    def infer_trigger_timestamp_from_clock_sync(self, clock_offset_s: float) -> float:
        """Return hardware trigger epoch from OPS radar clock and host offset."""
        return self.trigger_time + clock_offset_s

    def apply_trigger_timestamp_from_clock_sync(self, clock_offset_s: float) -> float:
        """Set trigger_timestamp using OPS trigger_time plus host-clock offset."""
        inferred = self.infer_trigger_timestamp_from_clock_sync(clock_offset_s)
        self.trigger_timestamp = inferred
        self.trigger_timestamp_source = "ops_clock_sync"
        self.clock_sync_offset_s = clock_offset_s
        return inferred


@dataclass
class SpeedReading:
    """
    A single speed reading extracted from FFT processing.

    This matches the format used in streaming mode for compatibility.
    """

    speed_mph: float
    magnitude: float
    timestamp_ms: float  # Relative to capture start
    direction: str = "outbound"  # "inbound" or "outbound"

    @property
    def is_outbound(self) -> bool:
        return self.direction == "outbound"


@dataclass
class SpeedTimeline:
    """
    High-resolution speed timeline from overlapping FFT processing.

    With 32-sample stepping (vs 128), we get ~937 Hz temporal resolution
    instead of ~56 Hz from streaming mode.

    Attributes:
        readings: List of speed readings in chronological order
        sample_rate_hz: Effective sample rate (~937 Hz with 32-step overlap)
        capture: Reference to the original I/Q capture
    """

    readings: List[SpeedReading]
    sample_rate_hz: float
    capture: Optional[IQCapture] = None

    @property
    def duration_ms(self) -> float:
        """Duration of timeline in milliseconds."""
        if not self.readings:
            return 0
        return self.readings[-1].timestamp_ms - self.readings[0].timestamp_ms

    @property
    def peak_speed(self) -> Optional[SpeedReading]:
        """Reading with highest speed."""
        if not self.readings:
            return None
        return max(self.readings, key=lambda r: r.speed_mph)

    @property
    def speeds(self) -> List[float]:
        """List of just the speed values."""
        return [r.speed_mph for r in self.readings]

    @property
    def timestamps(self) -> List[float]:
        """List of just the timestamp values."""
        return [r.timestamp_ms for r in self.readings]

    def get_readings_after(self, timestamp_ms: float) -> List[SpeedReading]:
        """Get readings after a given timestamp."""
        return [r for r in self.readings if r.timestamp_ms > timestamp_ms]

    def get_readings_before(self, timestamp_ms: float) -> List[SpeedReading]:
        """Get readings before a given timestamp."""
        return [r for r in self.readings if r.timestamp_ms < timestamp_ms]


@dataclass
class ImpactEstimate:
    """
    Capture-relative estimate of when ball strike occurred.

    The OPS hardware trigger remains the fallback. When the speed timeline has a
    clear club-to-ball transition, the midpoint between the last club-like frame
    and first ball-like frame is a better impact instant for K-LD7 correlation.
    """

    timestamp_ms: Optional[float]
    source: str
    reason: Optional[str] = None
    speed_delta_mph: Optional[float] = None
    transition_gap_ms: Optional[float] = None
    last_club_speed_mph: Optional[float] = None
    last_club_timestamp_ms: Optional[float] = None
    last_club_center_ms: Optional[float] = None
    first_ball_speed_mph: Optional[float] = None
    first_ball_timestamp_ms: Optional[float] = None
    first_ball_center_ms: Optional[float] = None
    min_transition_delta_mph: float = 15.0


@dataclass
class SpinCandidate:
    """Diagnostic envelope-FFT spin candidate."""

    rank: int
    rpm: float
    freq_hz: float
    relative_magnitude: float
    snr: float
    at_lower_rail: bool = False
    at_upper_rail: bool = False
    expected_spin_error_pct: Optional[float] = None
    selected: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation."""
        return {
            "rank": self.rank,
            "rpm": round(self.rpm),
            "freq_hz": round(self.freq_hz, 3),
            "relative_magnitude": round(self.relative_magnitude, 3),
            "snr": round(self.snr, 2),
            "at_lower_rail": bool(self.at_lower_rail),
            "at_upper_rail": bool(self.at_upper_rail),
            "expected_spin_error_pct": (
                round(self.expected_spin_error_pct, 1)
                if self.expected_spin_error_pct is not None
                else None
            ),
            "selected": bool(self.selected),
        }


@dataclass
class SpinResult:
    """
    Result of spin rate detection from secondary FFT.

    Spin is detected by analyzing micro-variations in ball speed caused
    by the dimpled surface. Success rate is ~50-60% per OmniPreSense.

    Attributes:
        spin_rpm: Detected spin rate in revolutions per minute
        confidence: Quality score from 0-1 (high SNR, valid range = high confidence)
        snr: Signal-to-noise ratio of the spin peak
        quality: Human-readable quality assessment
        method: Estimator that produced the result.
        multipath_fade_hz: Fitted two-ray fade frequency removed before
            estimating spin, when applicable.
        modulation_depth: Envelope std/mean inside the ball window. <0.005
            usually means quantization noise; <0.01 means the FFT peak
            may not be a real seam tone.
        peak_freq_hz: Frequency of the picked envelope-FFT peak (Hz).
        seam_cycles: Number of seam cycles in the analysis window
            (peak_freq_hz × window_seconds).
        at_lower_rail: True when the picked peak sits at or near the
            bottom of the seam search range. Such picks are dominated
            by envelope-DC leakage and should be treated with suspicion.
        at_upper_rail: True when the picked peak sits at or near the
            top of the seam search range. Such picks are typically
            bandpass-shoulder noise rather than a real seam tone.
        candidates: Ranked local envelope-FFT peaks kept for offline
            TrackMan comparison and detector tuning.
        phase_method: Phase-derived confirmation method, if attempted.
        phase_rpm: Phase-derived spin candidate, if available.
        phase_snr: Phase-derived candidate SNR.
        phase_agreement_pct: Difference between envelope and phase candidates.
        phase_confirmed: True when phase recovered a low-SNR envelope candidate.
        rejection_reason: Human-readable reason if the detection was
            rejected (rail-hit, low SNR, etc.). None on a clean accept.
    """

    spin_rpm: float
    confidence: float
    snr: float
    quality: str  # "high", "medium", "low", "experimental", or rejection reason
    method: str = "envelope_fft"
    multipath_fade_hz: Optional[float] = None
    modulation_depth: Optional[float] = None
    peak_freq_hz: Optional[float] = None
    seam_cycles: Optional[float] = None
    at_lower_rail: bool = False
    at_upper_rail: bool = False
    candidates: List[SpinCandidate] = field(default_factory=list)
    phase_method: Optional[str] = None
    phase_rpm: Optional[float] = None
    phase_snr: Optional[float] = None
    phase_agreement_pct: Optional[float] = None
    phase_confirmed: bool = False
    rejection_reason: Optional[str] = None

    @property
    def is_reliable(self) -> bool:
        """Whether spin detection is considered reliable."""
        return self.confidence >= SPIN_CONFIDENCE_RELIABLE and self.quality in ("high", "medium")

    @classmethod
    def no_spin_detected(
        cls,
        reason: str = "No clear spin signal",
        snr: float = 0.0,
        modulation_depth: Optional[float] = None,
        peak_freq_hz: Optional[float] = None,
        seam_cycles: Optional[float] = None,
        at_lower_rail: bool = False,
        at_upper_rail: bool = False,
        candidates: Optional[List[SpinCandidate]] = None,
        phase_method: Optional[str] = None,
        phase_rpm: Optional[float] = None,
        phase_snr: Optional[float] = None,
        phase_agreement_pct: Optional[float] = None,
        phase_confirmed: bool = False,
        method: str = "envelope_fft",
        multipath_fade_hz: Optional[float] = None,
    ) -> "SpinResult":
        """Factory for when spin detection fails. Diagnostic fields are
        carried through so we can see *why* it failed in the JSONL.
        """
        return cls(
            spin_rpm=0,
            confidence=0,
            snr=round(snr, 2),
            quality=reason,
            method=method,
            multipath_fade_hz=multipath_fade_hz,
            modulation_depth=modulation_depth,
            peak_freq_hz=peak_freq_hz,
            seam_cycles=seam_cycles,
            at_lower_rail=at_lower_rail,
            at_upper_rail=at_upper_rail,
            candidates=candidates or [],
            phase_method=phase_method,
            phase_rpm=phase_rpm,
            phase_snr=phase_snr,
            phase_agreement_pct=phase_agreement_pct,
            phase_confirmed=phase_confirmed,
            rejection_reason=reason,
        )


@dataclass
class ProcessedCapture:
    """
    Fully processed capture with speed timeline and optional spin.

    This is the final output from RollingBufferProcessor, containing
    all extracted data ready for shot detection.

    Attributes:
        timeline: High-resolution speed timeline
        ball_speed_mph: Peak ball speed detected
        ball_timestamp_ms: When ball was detected in timeline
        club_speed_mph: Club speed if detected (before ball)
        club_timestamp_ms: When club was detected
        spin: Spin detection result (may indicate failure)
        capture: Original raw I/Q data
        impact: Best capture-relative impact estimate for K-LD7 correlation
    """

    timeline: SpeedTimeline
    ball_speed_mph: float
    ball_timestamp_ms: float
    club_speed_mph: Optional[float] = None
    club_timestamp_ms: Optional[float] = None
    spin: Optional[SpinResult] = None
    capture: Optional[IQCapture] = None
    impact: Optional[ImpactEstimate] = None

    @property
    def impact_timestamp_ms(self) -> Optional[float]:
        """Best capture-relative impact timestamp, if available."""
        return self.impact.timestamp_ms if self.impact is not None else None

    @property
    def impact_source(self) -> Optional[str]:
        """Source used for the impact timestamp."""
        return self.impact.source if self.impact is not None else None

    @property
    def smash_factor(self) -> Optional[float]:
        """Ball speed / club speed ratio; None if above MAX_SMASH_FACTOR."""
        return compute_smash_factor(self.ball_speed_mph, self.club_speed_mph)

    @property
    def has_spin(self) -> bool:
        """Whether reliable spin data is available."""
        return self.spin is not None and self.spin.is_reliable
