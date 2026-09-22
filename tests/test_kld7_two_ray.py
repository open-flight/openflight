"""Tests for the two-ray multipath demodulation vertical estimator.

The synthetic payloads are physically exact: each sample carries the
two-way path phase of the ball AND its floor image computed from the
trajectory geometry, so Doppler aliasing, the multipath fringe, the
intra-frame phase drift, and the F1B range phase all emerge from the
physics rather than being injected as separate tones.
"""

import math

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.kld7.radc import WAVELENGTH_M, extract_launch_angle
from openflight.kld7.two_ray import (
    _TOUR_LAUNCH_DEG,
    ACQ_MS,
    MPH_TO_FTS,
    SAMPLE_DT_MS,
    SAMPLES,
    TIER1_CONFIDENCE,
    TIER2_BOOST_CONFIDENCE,
    TIER2_CONFIDENCE,
    _derive_tier_config,
    _net_slant_ft,
    _tier_config_for,
    classify_two_ray_tier,
    estimate_two_ray,
    two_ray_fit,
)

FT_TO_M = 0.3048
G_FT_S2 = 32.17

MOUNT_DEG = 10.0
OFFSET_DEG = 2.5
DISTANCE_FT = 5.0
BALL_ABOVE_RADAR_FT = -4.0 / 12.0
RANGE_UNAMB_FT = 5.0 * 3.28084  # 5 m FSK setting


def _trajectory(t_s: np.ndarray, la_deg: float, speed_mph: float):
    v = speed_mph * MPH_TO_FTS
    la = math.radians(la_deg)
    x = DISTANCE_FT + v * math.cos(la) * t_s
    y = BALL_ABOVE_RADAR_FT + v * math.sin(la) * t_s - 0.5 * G_FT_S2 * t_s**2
    return x, y


def make_two_ray_payload(
    frame_t_ms: float,
    la_deg: float,
    speed_mph: float,
    rho: float = 0.45,
    amp: float = 3000.0,
    noise: float = 25.0,
    seed: int = 0,
) -> bytes:
    """RADC payload for a frame ENDING frame_t_ms after impact."""
    rng = np.random.default_rng(seed)
    n = np.arange(SAMPLES)
    t_ms = frame_t_ms - (SAMPLES - n) * SAMPLE_DT_MS
    t_s = t_ms / 1000.0

    x, y = _trajectory(t_s, la_deg, speed_mph)
    y_img = 2.0 * BALL_ABOVE_RADAR_FT - y
    r_b = np.hypot(x, y) * FT_TO_M
    r_i = np.hypot(x, y_img) * FT_TO_M

    boresight = math.radians(MOUNT_DEG + OFFSET_DEG)
    spacing = 8.0e-3  # effective Rx spacing used by the angle code
    th_b = np.arctan2(y, x) - boresight
    th_i = np.arctan2(y_img, x) - boresight
    delta_b = 2.0 * math.pi * spacing / WAVELENGTH_M * np.sin(th_b)
    delta_i = 2.0 * math.pi * spacing / WAVELENGTH_M * np.sin(th_i)

    # Two-way carrier phase; K-LD7 convention: receding = positive Doppler
    phi_b = 4.0 * math.pi * r_b / WAVELENGTH_M
    phi_i = 4.0 * math.pi * r_i / WAVELENGTH_M
    # F1B leads F1A by the FSK range phase 2*pi*r/R_unambiguous
    psi_b = 2.0 * math.pi * (r_b / FT_TO_M) / RANGE_UNAMB_FT
    psi_i = 2.0 * math.pi * (r_i / FT_TO_M) / RANGE_UNAMB_FT

    a, b = amp, amp * rho
    s1 = a * np.exp(1j * phi_b) + b * np.exp(1j * phi_i)
    s2 = a * np.exp(1j * (phi_b - delta_b)) + b * np.exp(1j * (phi_i - delta_i))
    s1b = a * np.exp(1j * (phi_b + psi_b)) + b * np.exp(1j * (phi_i + psi_i))

    payload = np.empty(0, dtype=np.uint16)
    for sig in (s1, s2, s1b):
        i_ch = 2048 + sig.real + rng.normal(0, noise, SAMPLES)
        q_ch = 2048 + sig.imag + rng.normal(0, noise, SAMPLES)
        payload = np.concatenate(
            [
                payload,
                np.clip(i_ch, 0, 4095).astype(np.uint16),
                np.clip(q_ch, 0, 4095).astype(np.uint16),
            ]
        )
    return payload.tobytes()


def make_quiet_payload(seed: int = 99) -> bytes:
    rng = np.random.default_rng(seed)
    samples = np.clip(2048 + rng.normal(0, 25.0, SAMPLES * 6), 0, 4095)
    return samples.astype(np.uint16).tobytes()


def _frames_for_shot(la_deg: float, speed_mph: float, impact_ts: float) -> list[dict]:
    frames = [
        {"timestamp": impact_ts - 0.40 + 0.0287 * i, "radc": make_quiet_payload(seed=i)}
        for i in range(4)
    ]
    # rho=0.25 keeps the synthetic inside the validated fringe regime: the
    # exact mirror geometry synthesized here produces a faster fringe than
    # observed indoors (chi_dot ~0.7 vs 0.1-0.5 rad/ms measured), and at
    # high rho that stresses the decomposition beyond what real sessions
    # showed (see docs/kld7-subframe-stft-findings.md).
    for k, t_ms in enumerate((30.0, 55.0, 75.0)):
        frames.append(
            {
                "timestamp": impact_ts + t_ms / 1000.0,
                "radc": make_two_ray_payload(t_ms, la_deg, speed_mph, rho=0.25, seed=k),
            }
        )
    return frames


def _far_flight_frames(la_deg: float, speed_mph: float, impact_ts: float) -> list[dict]:
    """Like _frames_for_shot but extends past the FSK range wrap.

    At 100 mph the synthetic ball's slant range crosses the 16.4 ft
    unambiguous range near 80 ms, so the 100/125/150 ms frames carry a
    WRAPPED F1B range phase (the payload models the wrap exactly). Early
    frames stay un-wrapped to anchor tau, exactly as a real far-net shot.
    """
    frames = [
        {"timestamp": impact_ts - 0.40 + 0.0287 * i, "radc": make_quiet_payload(seed=i)}
        for i in range(4)
    ]
    for k, t_ms in enumerate((30.0, 55.0, 75.0, 100.0, 125.0, 150.0)):
        frames.append(
            {
                "timestamp": impact_ts + t_ms / 1000.0,
                "radc": make_two_ray_payload(t_ms, la_deg, speed_mph, rho=0.25, seed=k),
            }
        )
    return frames


class TestTwoRayFit:
    def test_recovers_ball_phase_and_rho(self):
        delta_b, delta_i, rho, chi_dot = 0.9, -0.5, 0.6, 0.7
        t = np.linspace(-12.0, 12.0, 13)
        g = rho * np.exp(1j * 0.8)
        u, v = np.exp(-1j * delta_b), np.exp(-1j * delta_i)
        z = (u + g * np.exp(1j * chi_dot * t) * v) / (1.0 + g * np.exp(1j * chi_dot * t))
        fit = two_ray_fit(z, t, np.ones_like(t))
        assert fit is not None
        assert fit["resid"] < 1e-6
        # The model is symmetric under swapping the two components (with
        # chi -> -chi, rho -> 1/rho); disambiguation happens downstream.
        direct = abs(fit["ball_phase"] - delta_b) < 0.02 and abs(fit["rho"] - rho) < 0.02
        swapped = abs(fit["ball_phase"] - delta_i) < 0.02 and abs(fit["rho"] - 1.0 / rho) < 0.05
        assert direct or swapped, fit
        assert abs(abs(fit["chi_dot"]) - chi_dot) < 0.05

    def test_too_few_points_returns_none(self):
        t = np.linspace(-5, 5, 5)
        assert two_ray_fit(np.ones(5, dtype=complex), t, np.ones(5)) is None


class TestEstimateTwoRay:
    def test_recovers_launch_angle(self):
        la_true, speed, impact_ts = 18.0, 100.0, 1000.0
        est = estimate_two_ray(
            _frames_for_shot(la_true, speed, impact_ts),
            impact_ts,
            speed,
            MOUNT_DEG,
            OFFSET_DEG,
            DISTANCE_FT,
            BALL_ABOVE_RADAR_FT,
        )
        assert est.refusal_reason is None, est.diagnostics
        assert est.launch_angle_deg == pytest.approx(la_true, abs=2.5)
        assert est.confidence >= 0.68
        assert abs(est.diagnostics["tau_range_ms"]) < 15.0

    def test_dc_blind_zone_refused(self):
        # 128 mph keeps every frame's RADIAL alias inside the +/-4 km/h
        # clutter core (the gate is per-frame radial now, not per-shot
        # impact speed: 124 or even 130 mph shots have escape frames
        # and measure; only the narrow true core refuses)
        est = estimate_two_ray(
            _frames_for_shot(18.0, 128.0, 1000.0),
            1000.0,
            128.0,
            MOUNT_DEG,
            OFFSET_DEG,
            DISTANCE_FT,
            BALL_ABOVE_RADAR_FT,
        )
        assert est.launch_angle_deg is None
        assert est.refusal_reason == "dc_blind_zone"
        assert est.diagnostics["refusal_reason"] == "dc_blind_zone"
        assert est.diagnostics["n_frames_dc_core_skipped"] >= 3

    def test_no_impact_timestamp_refused(self):
        est = estimate_two_ray(
            [], None, 100.0, MOUNT_DEG, OFFSET_DEG, DISTANCE_FT, BALL_ABOVE_RADAR_FT
        )
        assert est.refusal_reason == "no_impact_timestamp"

    def test_no_frames_refused(self):
        est = estimate_two_ray(
            [], 1000.0, 100.0, MOUNT_DEG, OFFSET_DEG, DISTANCE_FT, BALL_ABOVE_RADAR_FT
        )
        assert est.launch_angle_deg is None
        assert est.refusal_reason == "no_range_track"


class TestDealias:
    """Far-net de-aliasing: unwrap frames whose range wrapped past the FSK
    unambiguous range (16.4 ft at the 5 m setting), gated so that nets at or
    inside the wrap take the original capped path untouched."""

    SPEED = 100.0
    IMPACT = 1000.0
    LA = 18.0

    def _est(self, frames, net):
        return estimate_two_ray(
            frames,
            self.IMPACT,
            self.SPEED,
            MOUNT_DEG,
            OFFSET_DEG,
            DISTANCE_FT,
            BALL_ABOVE_RADAR_FT,
            net_distance_ft=net,
        )

    def test_net_slant_geometry(self):
        # Slant range to a ball at the net at nominal launch; monotonic in net.
        assert _net_slant_ft(10.0, DISTANCE_FT, BALL_ABOVE_RADAR_FT) == pytest.approx(
            15.32, abs=0.1
        )
        s10 = _net_slant_ft(10.0, DISTANCE_FT, BALL_ABOVE_RADAR_FT)
        s12 = _net_slant_ft(12.0, DISTANCE_FT, BALL_ABOVE_RADAR_FT)
        s25 = _net_slant_ft(25.0, DISTANCE_FT, BALL_ABOVE_RADAR_FT)
        assert s10 < s12 < s25

    def test_default_net_does_not_dealias(self):
        # Default 10 ft net (slant 15.3 < 16.4 wrap): original capped path.
        est = self._est(_frames_for_shot(self.LA, self.SPEED, self.IMPACT), 10.0)
        assert est.refusal_reason is None, est.diagnostics
        assert est.diagnostics["dealias"] is False
        assert "n_frames_unwrapped" not in est.diagnostics
        assert est.launch_angle_deg == pytest.approx(self.LA, abs=2.5)

    def test_no_net_does_not_dealias(self):
        # net_distance_ft=None disables the net entirely: never de-aliases.
        est = self._est(_frames_for_shot(self.LA, self.SPEED, self.IMPACT), None)
        assert est.refusal_reason is None, est.diagnostics
        assert est.diagnostics["dealias"] is False
        assert "n_frames_unwrapped" not in est.diagnostics

    def test_gate_flips_at_unambiguous_range(self):
        # The gate is exactly net_slant > unambiguous (16.40 ft). 11 ft net ->
        # slant 16.37 (just inside) stays capped; 12 ft -> 17.42 de-aliases.
        frames = _far_flight_frames(self.LA, self.SPEED, self.IMPACT)
        assert self._est(frames, 11.0).diagnostics["dealias"] is False
        assert self._est(frames, 12.0).diagnostics["dealias"] is True

    def test_far_net_unwraps_wrapped_frames(self):
        # 25 ft net (slant 31 ft) is well past the wrap: the 100/125/150 ms
        # frames unwrap from <11 ft back onto the real ballistic range.
        est = self._est(_far_flight_frames(self.LA, self.SPEED, self.IMPACT), 25.0)
        assert est.refusal_reason is None, est.diagnostics
        d = est.diagnostics
        assert d["dealias"] is True
        assert d["n_frames_unwrapped"] >= 1
        # At least one frame now sits past a single wrap, but never past the net.
        net_slant = _net_slant_ft(25.0, DISTANCE_FT, BALL_ABOVE_RADAR_FT)
        ranges = [f["range_ft"] for f in d["frames"] if f["range_ft"] is not None]
        assert any(r > RANGE_UNAMB_FT for r in ranges)
        assert max(ranges) <= net_slant + 1.0

    def test_far_net_recovers_frames_capped_path_loses(self):
        # The contrast that proves the feature does real work: identical
        # far-flight frames keep MORE valid frames with the far net than the
        # wrap-capped (no-net) path, and reach ranges the capped path cannot.
        frames = _far_flight_frames(self.LA, self.SPEED, self.IMPACT)
        capped = self._est(frames, None)
        dealiased = self._est(frames, 25.0)
        assert capped.diagnostics["dealias"] is False
        assert dealiased.diagnostics["dealias"] is True
        assert dealiased.diagnostics["n_frames_valid"] > capped.diagnostics["n_frames_valid"]
        capped_max = max(f["range_ft"] or 0.0 for f in capped.diagnostics["frames"])
        dealiased_max = max(f["range_ft"] or 0.0 for f in dealiased.diagnostics["frames"])
        assert capped_max <= RANGE_UNAMB_FT < dealiased_max


class TestExtractLaunchAngleTwoRay:
    def test_end_to_end_two_ray_estimator(self):
        la_true, speed, impact_ts = 18.0, 100.0, 1000.0
        results = extract_launch_angle(
            _frames_for_shot(la_true, speed, impact_ts),
            ops243_ball_speed_mph=speed,
            angle_offset_deg=OFFSET_DEG,
            orientation="vertical",
            vertical_estimator="two_ray",
            shot_timestamp=impact_ts,
            impact_timestamp=impact_ts,
            mount_deg=MOUNT_DEG,
            distance_ft=DISTANCE_FT,
            ball_above_radar_ft=BALL_ABOVE_RADAR_FT,
        )
        assert results, "expected at least one shot result"
        best = results[0]
        assert best["estimator"] == "two_ray"
        assert best["selection_path"] == "two_ray"
        assert best["launch_angle_deg"] == pytest.approx(la_true, abs=2.5)
        assert best["confidence"] >= 0.68
        assert best["two_ray"]["la_curve_deg"] == pytest.approx(la_true, abs=2.5)

    def test_refusal_falls_back_to_geometry_path(self):
        # Blind-zone ball speed: two_ray must refuse and the pipeline must
        # still return via the geometry/naive fallback without crashing.
        la_true, speed, impact_ts = 18.0, 128.0, 1000.0
        results = extract_launch_angle(
            _frames_for_shot(la_true, speed, impact_ts),
            ops243_ball_speed_mph=speed,
            angle_offset_deg=OFFSET_DEG,
            orientation="vertical",
            vertical_estimator="two_ray",
            shot_timestamp=impact_ts,
            impact_timestamp=impact_ts,
            mount_deg=MOUNT_DEG,
            distance_ft=DISTANCE_FT,
            ball_above_radar_ft=BALL_ABOVE_RADAR_FT,
        )
        for res in results:
            assert res["estimator"] != "two_ray"
            assert res["two_ray"]["refusal_reason"] == "dc_blind_zone"


class TestFrameTiming:
    def test_acquisition_constants_consistent(self):
        # 256 samples must span one ~28.7 ms frame
        assert ACQ_MS == pytest.approx(SAMPLES * SAMPLE_DT_MS)
        assert 28.0 < ACQ_MS < 29.5


def _diag(pos=None, single=None, nval=0, maxel=0.0, maxsep=0.0):
    """Build a two_ray diagnostics dict with one frame carrying the requested
    maxel (max el_deg) and maxsep (|el_deg - el_image_deg|)."""
    return {
        "la_position_deg": pos,
        "la_single_frame_deg": single,
        "n_frames_valid": nval,
        "frames": [{"el_deg": maxel, "el_image_deg": maxel - maxsep}],
    }


class TestTwoRayTierClassifier:
    """The tour-anchored 2-tier decision on top of two_ray. Every club's gates
    and boost are derived from its tour-average launch (see _tier_config_for);
    the coefficients are seeded so the 7-iron reproduces its validated config."""

    def test_unknown_club_returns_none(self):
        # UNKNOWN / no club -> None so the caller falls back to geometry.
        d = _diag(pos=15.0, nval=3, maxel=11.0, maxsep=11.0)
        assert classify_two_ray_tier(d, 14.0, ClubType.UNKNOWN) is None
        assert classify_two_ray_tier(d, 14.0, None) is None

    def test_tier1_clean_gate_trusts_position(self):
        d = _diag(pos=15.0, nval=3, maxel=11.0, maxsep=11.0)
        r = classify_two_ray_tier(d, 14.0, ClubType.IRON_7)
        assert r.tier == 1 and r.boosted is False
        assert r.launch_angle_deg == 15.0  # la_position as-is
        assert r.confidence == TIER1_CONFIDENCE

    def test_tier1_requires_position_fit(self):
        # Gate metrics pass but no la_position -> not Tier-1; drops to Tier-2.
        d = _diag(pos=None, single=14.0, nval=3, maxel=11.0, maxsep=11.0)
        r = classify_two_ray_tier(d, 13.0, ClubType.IRON_7)
        assert r.tier == 2 and r.boosted is False  # maxel 11 >= far_el gate 7
        assert r.launch_angle_deg == 14.0  # single-frame fallback

    def test_tier2_corrupted_low_boosted_to_tour(self):
        # Low far_el (corrupted-low) -> boost toward the 7i tour average (~+4).
        cfg = _tier_config_for(ClubType.IRON_7)
        d = _diag(pos=None, single=12.0, nval=1, maxel=6.0)  # < 7i far_el ~7
        r = classify_two_ray_tier(d, 12.0, ClubType.IRON_7)
        assert r.tier == 2 and r.boosted is True
        assert r.launch_angle_deg == pytest.approx(12.0 + cfg.boost_deg)
        assert r.confidence == TIER2_BOOST_CONFIDENCE

    def test_tier2_not_boosted_when_far_el_ok(self):
        # Fails Tier-1 (low maxsep) but far_el fine -> Tier-2, no boost.
        d = _diag(pos=16.0, nval=3, maxel=11.0, maxsep=3.0)
        r = classify_two_ray_tier(d, 16.0, ClubType.IRON_7)
        assert r.tier == 2 and r.boosted is False
        # No-boost keeps the 0.65 floor (2 dots); boosted drops to 0.35 (1 dot).
        assert r.confidence == TIER2_CONFIDENCE
        assert TIER2_BOOST_CONFIDENCE < TIER2_CONFIDENCE
        assert r.launch_angle_deg == 16.0

    def test_estimate_falls_back_to_curve_when_no_fits(self):
        # No position/single-frame fit -> uses two_ray's primary (fallback).
        cfg = _tier_config_for(ClubType.IRON_7)
        d = _diag(pos=None, single=None, nval=0, maxel=6.0)  # < 7i far_el -> boosted
        r = classify_two_ray_tier(d, 10.0, ClubType.IRON_7)
        assert r.boosted is True
        assert r.launch_angle_deg == pytest.approx(10.0 + cfg.boost_deg)

    def test_per_club_gate_differs(self):
        # maxel=10 clears the 7i gate but not the (higher) PW gate.
        seven = _tier_config_for(ClubType.IRON_7)
        pw_cfg = _tier_config_for(ClubType.PW)
        assert seven.gate_maxel_deg < 10.0 < pw_cfg.gate_maxel_deg
        d = _diag(pos=20.0, nval=3, maxel=10.0, maxsep=11.0)
        assert classify_two_ray_tier(d, 19.0, ClubType.IRON_7).tier == 1
        assert classify_two_ray_tier(d, 19.0, ClubType.PW).tier == 2

    def test_per_club_boost_differs(self):
        # PW's tour-derived boost exceeds the 7i's (higher tour average).
        seven = _tier_config_for(ClubType.IRON_7)
        pw_cfg = _tier_config_for(ClubType.PW)
        assert pw_cfg.boost_deg > seven.boost_deg
        d = _diag(pos=None, single=16.0, nval=1, maxel=8.0)  # < PW far_el -> boosted
        pw = classify_two_ray_tier(d, 16.0, ClubType.PW)
        assert pw.tier == 2 and pw.boosted is True
        assert pw.launch_angle_deg == pytest.approx(16.0 + pw_cfg.boost_deg)

    # --- all-club coverage: every club tour-derived --------------------------

    def test_seven_iron_and_pw_are_tour_derived(self):
        # No hand-tuned overrides: 7i and PW come from the tour-average formula
        # like every other club. Coefficients are seeded so 7i lands on its old
        # validated config (gate 9 / far_el 7 / boost 4).
        seven = _tier_config_for(ClubType.IRON_7)
        assert seven == _derive_tier_config(_TOUR_LAUNCH_DEG[ClubType.IRON_7])
        assert seven.gate_maxel_deg == pytest.approx(9.0, abs=0.05)
        assert seven.boost_deg == pytest.approx(4.0, abs=0.05)
        pw = _tier_config_for(ClubType.PW)
        assert pw == _derive_tier_config(_TOUR_LAUNCH_DEG[ClubType.PW])

    def test_every_real_club_characterized(self):
        # Every club except UNKNOWN has a tier config (full enable); UNKNOWN
        # and None fall through to geometry.
        for club in ClubType:
            cfg = _tier_config_for(club)
            if club is ClubType.UNKNOWN:
                assert cfg is None
            else:
                assert cfg is not None, club
        assert _tier_config_for(None) is None

    def test_derive_formula_reproduces_seven_iron(self):
        # The derivation coefficients are seeded to land on the 7i validated
        # config at its tour-average launch (16.3 deg).
        derived = _derive_tier_config(_TOUR_LAUNCH_DEG[ClubType.IRON_7])
        assert derived.gate_maxel_deg == pytest.approx(9.0, abs=0.05)
        assert derived.far_el_gate_deg == pytest.approx(7.0, abs=0.05)
        assert derived.boost_deg == pytest.approx(4.0, abs=0.05)

    def test_derived_gate_monotonic_in_tour_launch(self):
        # Higher-launching clubs get a higher trust gate / far_el / boost.
        low = _derive_tier_config(11.0)  # ~4-iron
        high = _derive_tier_config(20.4)  # ~9-iron
        assert high.gate_maxel_deg > low.gate_maxel_deg
        assert high.far_el_gate_deg > low.far_el_gate_deg
        assert high.boost_deg > low.boost_deg

    def test_derived_club_tier1_clean_gate(self):
        # A previously-uncharacterized club (driver) now runs two_ray: a clean
        # gate trusts the position fit (Tier-1), it does NOT return None.
        d = _diag(pos=12.0, nval=3, maxel=8.0, maxsep=11.0)  # maxel >> driver gate 3.1
        r = classify_two_ray_tier(d, 11.0, ClubType.DRIVER)
        assert r is not None and r.tier == 1 and r.boosted is False
        assert r.launch_angle_deg == 12.0
        assert r.confidence == TIER1_CONFIDENCE

    def test_derived_club_tier2_corrupted_low_boosted(self):
        # 5-iron reads corrupted-low (maxel below its derived far_el ~5.2) ->
        # Tier-2 with a derived boost toward its tour average.
        cfg = _tier_config_for(ClubType.IRON_5)
        d = _diag(pos=None, single=8.0, nval=1, maxel=cfg.far_el_gate_deg - 1.0)
        r = classify_two_ray_tier(d, 8.0, ClubType.IRON_5)
        assert r.tier == 2 and r.boosted is True
        assert r.launch_angle_deg == pytest.approx(8.0 + cfg.boost_deg)
        assert r.confidence == TIER2_BOOST_CONFIDENCE
