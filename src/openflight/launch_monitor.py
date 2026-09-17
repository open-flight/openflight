"""
Golf Launch Monitor data types and carry estimation.

Provides Shot and carry distance estimation used by
RollingBufferMonitor and the Flask server.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .clubs import ClubType
from .clubs.physics import get_club_physics
from .ops243 import SpeedReading

# Spin confidence threshold for "high" quality — used across modules.
# Measured spin is trusted for physics simulation only above this level.
SPIN_CONFIDENCE_HIGH = 0.7


def estimate_carry_distance(ball_speed_mph: float, club: ClubType = ClubType.DRIVER) -> float:
    """
    Estimate carry distance from ball speed using TrackMan-derived data.

    This uses interpolation from real-world data assuming optimal launch
    conditions (10-14° launch angle, appropriate spin for ball speed).

    Data sources:
    - TrackMan PGA Tour averages
    - pitchmarks.com ball speed to distance tables

    Args:
        ball_speed_mph: Ball speed in mph
        club: Club type (affects the model used)

    Returns:
        Estimated carry distance in yards

    Note:
        Without launch angle and spin rate, this is an approximation.
        Actual distance can vary ±10-15% based on:
        - Launch angle (optimal: 10-14° for driver)
        - Spin rate (optimal: 2000-3000 rpm for driver)
        - Weather conditions
        - Altitude
    """
    # Driver ball speed to carry distance lookup table
    # Based on TrackMan data assuming optimal launch conditions
    # Format: (ball_speed_mph, carry_yards_low, carry_yards_high)
    DRIVER_TABLE = [
        (100, 130, 142),
        (110, 157, 170),
        (120, 183, 197),
        (130, 207, 223),
        (140, 231, 249),
        (150, 254, 275),
        (160, 276, 301),
        (167, 275, 285),  # PGA Tour average
        (170, 298, 325),
        (180, 320, 349),
        (190, 342, 372),
        (200, 360, 389),
        (210, 383, 408),
    ]
    # Some source rows are mixed (e.g. PGA average vs optimal carry) and can
    # create local dips in the midpoint curve. Clamp midpoints to a
    # non-decreasing sequence so carry does not decrease as ball speed rises.
    monotonic_driver_curve = []
    max_carry_so_far = 0.0
    for speed, carry_min, carry_max in DRIVER_TABLE:
        midpoint = (carry_min + carry_max) / 2
        max_carry_so_far = max(max_carry_so_far, midpoint)
        monotonic_driver_curve.append((speed, max_carry_so_far))

    # No club factor applied — ball speed already reflects the club's smash
    # factor. Club-specific carry differences come from launch angle and spin
    # adjustments applied downstream.

    # Interpolate from driver table
    if ball_speed_mph <= monotonic_driver_curve[0][0]:
        # Below minimum - extrapolate linearly
        ratio = ball_speed_mph / monotonic_driver_curve[0][0]
        base_carry = monotonic_driver_curve[0][1]
        carry = base_carry * ratio
    elif ball_speed_mph >= monotonic_driver_curve[-1][0]:
        # Above maximum - extrapolate conservatively
        # Use ~1.8 yards per mph above 210 mph
        base_carry = monotonic_driver_curve[-1][1]
        carry = base_carry + (ball_speed_mph - monotonic_driver_curve[-1][0]) * 1.8
    else:
        # Interpolate between table entries
        for i in range(len(monotonic_driver_curve) - 1):
            if monotonic_driver_curve[i][0] <= ball_speed_mph < monotonic_driver_curve[i + 1][0]:
                # Linear interpolation
                speed_low, carry_low = monotonic_driver_curve[i]
                speed_high, carry_high = monotonic_driver_curve[i + 1]

                # Interpolate
                t = (ball_speed_mph - speed_low) / (speed_high - speed_low)
                carry = carry_low + t * (carry_high - carry_low)
                break
        else:
            # Fallback (shouldn't reach here)
            carry = ball_speed_mph * 1.65

    return carry


def adjust_carry_for_launch_angle(
    base_carry: float,
    launch_angle: float,
    club: ClubType = ClubType.DRIVER,
    confidence: float = 1.0,
) -> float:
    """
    Adjust carry distance based on launch angle deviation from optimal.

    Deviation from optimal costs carry:
    - Too low: -2.0 yards per degree (ball doesn't get enough height)
    - Too high: -1.5 yards per degree (ball balloons, less severe)
    - Penalty is scaled by confidence and capped at 10% of base carry.

    Args:
        base_carry: Base carry distance in yards (from estimate_carry_distance)
        launch_angle: Measured vertical launch angle in degrees
        club: Club type (determines optimal launch angle)
        confidence: Confidence in the launch angle measurement (0-1)

    Returns:
        Adjusted carry distance in yards
    """
    optimal = get_club_physics(club).optimal_launch_deg
    angle_delta = launch_angle - optimal

    if angle_delta < 0:
        raw_penalty = abs(angle_delta) * 2.0
    else:
        raw_penalty = angle_delta * 1.5

    penalty = raw_penalty * confidence
    max_penalty = base_carry * 0.10
    penalty = min(penalty, max_penalty)

    return base_carry - penalty


@dataclass
class Shot:
    """
    Represents a detected golf shot with ball and club data.

    Attributes:
        ball_speed_mph: Peak ball speed detected (mph)
        club_speed_mph: Peak club head speed detected (mph), if available
        smash_factor: Ratio of ball speed to club speed (typically 1.4-1.5 for driver)
        timestamp: When the shot was detected
        shot_number: Stable session sequence assigned when the server receives the shot
        impact_timestamp: Epoch timestamp aligned to impact/OPS trigger time
        impact_timestamp_kld7: Ball-contact instant used by the K-LD7
            geometry launch-angle estimator. This currently mirrors the
            trusted hardware sound-trigger timestamp; OPS ball_timestamp_ms is
            a radar-return position inside the capture and should not move
            geometry t=0.
        peak_magnitude: Signal strength of strongest reading
        readings: All raw speed readings for this shot
        club: Club type for distance estimation
        launch_angle_vertical: Vertical launch angle in degrees (from camera)
        launch_angle_horizontal: Horizontal launch angle in degrees (from camera)
        launch_angle_confidence: Backward-compatible primary launch angle confidence (0-1)
        launch_angle_vertical_confidence: Confidence in vertical launch angle measurement
        launch_angle_horizontal_confidence: Confidence in horizontal launch angle measurement
        launch_angle_vertical_source: Source for vertical launch angle
        launch_angle_horizontal_source: Source for horizontal launch angle
        spin_rpm: Spin rate in RPM (from rolling buffer mode)
        spin_confidence: Confidence in spin measurement (0-1)
        spin_method: Estimator that produced the spin candidate
        spin_result_quality: Processor quality label for the spin detection
        spin_multipath_fade_hz: Fitted two-ray fade frequency, when available
        spin_snr: Signal-to-noise ratio of the spin envelope peak
        spin_modulation_depth: Envelope std/mean inside the spin window
        spin_peak_freq_hz: Frequency of the detected spin candidate
        spin_seam_cycles: Number of spin cycles in the analysis window
        spin_at_lower_rail: Whether the spin candidate hit the low search boundary
        spin_at_upper_rail: Whether the spin candidate hit the high search boundary
        spin_candidates: Ranked spin candidates for offline analysis
        spin_phase_method: Phase confirmation method, if attempted
        spin_phase_rpm: Phase-derived spin candidate, if available
        spin_phase_snr: Phase-derived candidate SNR
        spin_phase_agreement_pct: Envelope/phase agreement percentage
        spin_phase_confirmed: Whether phase recovered a low-SNR spin
        spin_rejection_reason: Why spin was withheld, if it was rejected
        carry_spin_adjusted: Carry distance adjusted for spin (yards)
        mode: Shot source — "streaming", "rolling-buffer", or "mock"
        readings_data: Serialized readings for session logging
        camera_replay: Public replay metadata for a matched high-speed capture
    """

    ball_speed_mph: float
    timestamp: datetime
    shot_number: Optional[int] = None
    impact_timestamp: Optional[float] = None
    impact_timestamp_kld7: Optional[float] = None
    club_speed_mph: Optional[float] = None
    # Raw OPS radial ball speed, kept when the cosine correction rewrites
    # ball_speed_mph (radar bin anchoring must keep using the radial value)
    ball_speed_raw_mph: Optional[float] = None
    peak_magnitude: Optional[float] = None
    readings: List[SpeedReading] = field(default_factory=list)
    club: ClubType = ClubType.DRIVER
    launch_angle_vertical: Optional[float] = None
    launch_angle_horizontal: Optional[float] = None
    launch_angle_confidence: Optional[float] = None
    launch_angle_vertical_confidence: Optional[float] = None
    launch_angle_horizontal_confidence: Optional[float] = None
    launch_angle_vertical_source: Optional[str] = None
    launch_angle_horizontal_source: Optional[str] = None
    spin_rpm: Optional[float] = None
    spin_confidence: Optional[float] = None
    # Raw radar-measured spin, kept when --calculated-spin rewrites
    # spin_rpm with the kinematic estimate (for offline scoring)
    spin_rpm_measured: Optional[float] = None
    spin_source: Optional[str] = None  # "measured", "calculated", or None
    spin_method: Optional[str] = None
    spin_result_quality: Optional[str] = None
    spin_multipath_fade_hz: Optional[float] = None
    spin_snr: Optional[float] = None
    spin_modulation_depth: Optional[float] = None
    spin_peak_freq_hz: Optional[float] = None
    spin_seam_cycles: Optional[float] = None
    spin_at_lower_rail: Optional[bool] = None
    spin_at_upper_rail: Optional[bool] = None
    spin_candidates: Optional[list] = None
    spin_phase_method: Optional[str] = None
    spin_phase_rpm: Optional[float] = None
    spin_phase_snr: Optional[float] = None
    spin_phase_agreement_pct: Optional[float] = None
    spin_phase_confirmed: bool = False
    spin_rejection_reason: Optional[str] = None
    carry_spin_adjusted: Optional[float] = None
    mode: str = "rolling-buffer"
    profile_id: str = ""
    profile_name: str = ""
    readings_data: Optional[list] = None
    camera_replay: Optional[dict] = None
    angle_source: Optional[str] = None  # "radar", "camera", "estimated", or None
    club_angle_deg: Optional[float] = None  # Club angle of attack from K-LD7 (vertical)
    club_path_deg: Optional[float] = None  # Club path: IWR6843, or K-LD7 (deprecated, horizontal)
    experimental_attack_angle_deg: Optional[float] = None
    experimental_attack_angle_status: Optional[str] = None
    experimental_club_path_deg: Optional[float] = None
    experimental_club_path_status: Optional[str] = None
    # Camera-fused club delivery: radar AoA candidate + per-club calibration
    # offset, club path derived from the camera delivery-plane trace. Same
    # experimental estimator level as the fields above.
    experimental_fused_attack_angle_deg: Optional[float] = None
    experimental_fused_club_path_deg: Optional[float] = None
    experimental_fused_status: Optional[str] = None
    experimental_fused_attack_angle_confidence: Optional[str] = None
    experimental_fused_club_path_confidence: Optional[str] = None
    experimental_camera_trace_deg: Optional[float] = None
    experimental_aoa_offset_source: Optional[str] = None
    # Independent horizontal ball-flight evidence. The camera-assisted value
    # may become the displayed experimental result while the original IWR
    # value remains available for replay and comparison.
    iwr6843_horizontal_deg: Optional[float] = None
    iwr6843_horizontal_confidence: Optional[float] = None
    experimental_camera_horizontal_deg: Optional[float] = None
    experimental_camera_horizontal_confidence: Optional[float] = None
    experimental_camera_horizontal_status: Optional[str] = None
    experimental_camera_iwr_delta_deg: Optional[float] = None
    # Transient fitted IWR range trajectory consumed by live camera fusion.
    # Deliberately excluded from websocket/session serializers.
    iwr6843_club_range_evidence: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    iwr6843_ball_range_evidence: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    spin_axis_deg: Optional[float] = None  # Spin axis tilt: 0=backspin, +right(fade), -left(draw)
    inclinometer: Optional[dict] = None  # Stable enclosure orientation used for this shot

    @property
    def ball_speed_ms(self) -> float:
        """Ball speed in meters per second."""
        return self.ball_speed_mph * 0.44704

    @property
    def club_speed_ms(self) -> Optional[float]:
        """Club speed in meters per second."""
        if self.club_speed_mph is None:
            return None
        return self.club_speed_mph * 0.44704

    @property
    def smash_factor(self) -> Optional[float]:
        """
        Smash factor: ratio of ball speed to club speed.

        Indicates quality of contact:
        - Driver: 1.44-1.50 (optimal)
        - Irons: 1.30-1.38
        - Wedges: 1.20-1.25

        Returns:
            Smash factor or None if club speed not available
        """
        if self.club_speed_mph is None or self.club_speed_mph == 0:
            return None
        return self.ball_speed_mph / self.club_speed_mph

    @property
    def estimated_carry_yards(self) -> float:
        """Estimated carry distance based on ball speed, club type, and launch angle."""
        base = estimate_carry_distance(self.ball_speed_mph, self.club)
        if self.launch_angle_vertical is not None:
            return adjust_carry_for_launch_angle(
                base,
                self.launch_angle_vertical,
                self.club,
                self.launch_angle_confidence or 0.2,
            )
        return base

    @property
    def estimated_carry_range(self) -> tuple:
        """
        Return (min, max) carry distance estimate to show uncertainty.

        Returns:
            Tuple of (low_estimate, high_estimate) in yards
        """
        base = self.estimated_carry_yards
        # ±10% uncertainty without launch angle/spin data
        # Reduce to ±5% if we have launch angle data
        if self.has_launch_angle:
            return (base * 0.95, base * 1.05)
        return (base * 0.90, base * 1.10)

    @property
    def has_launch_angle(self) -> bool:
        """Check if launch angle data is available for this shot."""
        return self.launch_angle_vertical is not None

    @property
    def has_spin(self) -> bool:
        """Check if spin data is available for this shot."""
        return self.spin_rpm is not None

    @property
    def spin_quality(self) -> Optional[str]:
        """
        Get spin measurement quality as a string.

        Returns:
            "high", "medium", "low", or None if no spin data
        """
        if self.spin_rpm is not None and self.spin_result_quality:
            return self.spin_result_quality
        if self.spin_confidence is None:
            return None
        if self.spin_confidence >= SPIN_CONFIDENCE_HIGH:
            return "high"
        if self.spin_confidence >= 0.4:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        """Return the canonical, unrounded representation of this shot."""
        return {
            "shot_number": self.shot_number,
            "ball_speed_mph": self.ball_speed_mph,
            "ball_speed_raw_mph": self.ball_speed_raw_mph,
            "club_speed_mph": self.club_speed_mph,
            "smash_factor": self.smash_factor,
            "estimated_carry_yards": self.estimated_carry_yards,
            "carry_range": list(self.estimated_carry_range),
            "club": self.club.value,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "timestamp": self.timestamp.isoformat(),
            "impact_timestamp": self.impact_timestamp,
            "peak_magnitude": self.peak_magnitude,
            "readings_count": len(self.readings),
            "readings": self.readings_data,
            "mode": self.mode,
            "launch_angle_vertical": self.launch_angle_vertical,
            "launch_angle_horizontal": self.launch_angle_horizontal,
            "launch_angle_confidence": self.launch_angle_confidence,
            "launch_angle_vertical_confidence": self.launch_angle_vertical_confidence,
            "launch_angle_horizontal_confidence": self.launch_angle_horizontal_confidence,
            "launch_angle_vertical_source": self.launch_angle_vertical_source,
            "launch_angle_horizontal_source": self.launch_angle_horizontal_source,
            "angle_source": self.angle_source,
            "club_angle_deg": self.club_angle_deg,
            "club_path_deg": self.club_path_deg,
            "experimental_attack_angle_deg": self.experimental_attack_angle_deg,
            "experimental_attack_angle_status": self.experimental_attack_angle_status,
            "experimental_club_path_deg": self.experimental_club_path_deg,
            "experimental_club_path_status": self.experimental_club_path_status,
            "experimental_fused_attack_angle_deg": self.experimental_fused_attack_angle_deg,
            "experimental_fused_club_path_deg": self.experimental_fused_club_path_deg,
            "experimental_fused_status": self.experimental_fused_status,
            "experimental_fused_attack_angle_confidence": (
                self.experimental_fused_attack_angle_confidence
            ),
            "experimental_fused_club_path_confidence": (
                self.experimental_fused_club_path_confidence
            ),
            "experimental_camera_trace_deg": self.experimental_camera_trace_deg,
            "experimental_aoa_offset_source": self.experimental_aoa_offset_source,
            "iwr6843_horizontal_deg": self.iwr6843_horizontal_deg,
            "iwr6843_horizontal_confidence": self.iwr6843_horizontal_confidence,
            "experimental_camera_horizontal_deg": self.experimental_camera_horizontal_deg,
            "experimental_camera_horizontal_confidence": (
                self.experimental_camera_horizontal_confidence
            ),
            "experimental_camera_horizontal_status": self.experimental_camera_horizontal_status,
            "experimental_camera_iwr_delta_deg": self.experimental_camera_iwr_delta_deg,
            "camera_replay": dict(self.camera_replay) if self.camera_replay else None,
            "spin_axis_deg": self.spin_axis_deg,
            "inclinometer": self.inclinometer,
            "spin_rpm": self.spin_rpm,
            "spin_rpm_measured": self.spin_rpm_measured,
            "spin_source": self.spin_source,
            "spin_method": self.spin_method,
            "spin_confidence": self.spin_confidence,
            "spin_quality": self.spin_quality,
            "spin_multipath_fade_hz": self.spin_multipath_fade_hz,
            "spin_snr": self.spin_snr,
            "spin_modulation_depth": self.spin_modulation_depth,
            "spin_peak_freq_hz": self.spin_peak_freq_hz,
            "spin_candidate_rpm": (
                self.spin_peak_freq_hz * 60 if self.spin_peak_freq_hz is not None else None
            ),
            "spin_seam_cycles": self.spin_seam_cycles,
            "spin_at_lower_rail": self.spin_at_lower_rail,
            "spin_at_upper_rail": self.spin_at_upper_rail,
            "spin_candidates": self.spin_candidates,
            "spin_phase_method": self.spin_phase_method,
            "spin_phase_rpm": self.spin_phase_rpm,
            "spin_phase_snr": self.spin_phase_snr,
            "spin_phase_agreement_pct": self.spin_phase_agreement_pct,
            "spin_phase_confirmed": self.spin_phase_confirmed,
            "spin_rejection_reason": self.spin_rejection_reason,
            "carry_spin_adjusted": self.carry_spin_adjusted,
        }


def summarize_shots(shots: List[Shot], mode: str) -> dict:
    """Calculate the shared session summary for a list of shots."""
    summary = {
        "shot_count": len(shots),
        "avg_ball_speed": 0,
        "max_ball_speed": 0,
        "min_ball_speed": 0,
        "avg_club_speed": None,
        "avg_smash_factor": None,
        "avg_carry_est": 0,
        "avg_spin_rpm": None,
        "spin_detection_rate": 0,
        "mode": mode,
    }
    if not shots:
        return summary

    ball_speeds = [shot.ball_speed_mph for shot in shots]
    club_speeds = [shot.club_speed_mph for shot in shots if shot.club_speed_mph]
    smash_factors = [shot.smash_factor for shot in shots if shot.smash_factor]
    spin_rpms = [shot.spin_rpm for shot in shots if shot.spin_rpm is not None]
    summary.update(
        {
            "avg_ball_speed": statistics.mean(ball_speeds),
            "max_ball_speed": max(ball_speeds),
            "min_ball_speed": min(ball_speeds),
            "std_dev": statistics.stdev(ball_speeds) if len(ball_speeds) > 1 else 0,
            "avg_club_speed": statistics.mean(club_speeds) if club_speeds else None,
            "avg_smash_factor": statistics.mean(smash_factors) if smash_factors else None,
            "avg_carry_est": statistics.mean([shot.estimated_carry_yards for shot in shots]),
            "avg_spin_rpm": statistics.mean(spin_rpms) if spin_rpms else None,
            "spin_detection_rate": len(spin_rpms) / len(shots),
        }
    )
    return summary
