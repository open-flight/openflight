# Enhanced Launch Angle Estimation — Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve launch angle estimation by incorporating smash factor and spin rate, then wire launch angle into carry distance calculation.

**Architecture:** Enhance `estimate_launch_angle()` in `server.py` to accept optional club_speed and spin_rpm. Add `adjust_carry_for_launch_angle()` in `launch_monitor.py`. Update `Shot` properties to use the new carry adjustment. All changes are additive — no inputs = current behavior.

**Tech Stack:** Python, pytest. No new dependencies.

---

### Task 1: Enhanced launch angle estimation — smash factor adjustment

**Files:**
- Modify: `src/openflight/server.py:70-116` (the `estimate_launch_angle` function)
- Test: `tests/test_server.py`

**Step 1: Write failing tests for smash factor adjustment**

Add to `tests/test_server.py` in `TestEstimateLaunchAngle`:

```python
def test_low_smash_lowers_launch(self):
    """Low smash factor (thin hit) should lower launch angle."""
    baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
    angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=110)
    # smash = 143/110 = 1.30, well below optimal 1.48
    assert angle < baseline

def test_optimal_smash_no_change(self):
    """Optimal smash factor should not shift launch angle much."""
    baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
    angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=96.6)
    # smash = 143/96.6 ≈ 1.48 (optimal for driver)
    assert abs(angle - baseline) <= 0.5

def test_smash_raises_confidence(self):
    """Providing club speed should raise confidence from 0.2 to 0.35."""
    _, conf = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=96.6)
    assert conf == 0.35

def test_iron_smash_adjustment(self):
    """Iron smash factor adjustment should work correctly."""
    baseline, _ = estimate_launch_angle(ClubType.IRON_7, 100)
    # Low smash for 7-iron: smash = 100/80 = 1.25, below optimal ~1.34
    angle, _ = estimate_launch_angle(ClubType.IRON_7, 100, club_speed_mph=80)
    assert angle < baseline

def test_no_club_speed_unchanged(self):
    """Without club speed, behavior should be identical to current."""
    angle, conf = estimate_launch_angle(ClubType.DRIVER, 143)
    assert angle == 11.0
    assert conf == 0.2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::TestEstimateLaunchAngle -v`
Expected: FAIL — `estimate_launch_angle()` doesn't accept `club_speed_mph` parameter

**Step 3: Implement smash factor adjustment**

Update `estimate_launch_angle()` in `src/openflight/server.py`:

```python
def estimate_launch_angle(
    club: ClubType,
    ball_speed_mph: float,
    club_speed_mph: Optional[float] = None,
    spin_rpm: Optional[float] = None,
) -> tuple:
    """
    Estimate launch angle from club type, ball speed, and optional smash/spin data.

    Uses TrackMan averages as baseline, then adjusts:
    - Ball speed deviation from club average
    - Smash factor deviation from optimal (if club speed available)
    - Spin rate deviation from optimal (if spin available)

    Returns (vertical_angle, confidence).
    """
    # Baseline launch angles by club
    # Format: (avg_launch_deg, avg_ball_speed_mph, deg_per_mph_deviation)
    _CLUB_LAUNCH_MODEL = {
        ClubType.DRIVER: (11.0, 143, 0.15),
        ClubType.WOOD_3: (12.5, 135, 0.18),
        ClubType.WOOD_5: (14.0, 128, 0.20),
        ClubType.WOOD_7: (15.5, 122, 0.20),
        ClubType.HYBRID_3: (13.5, 123, 0.22),
        ClubType.HYBRID_5: (15.0, 118, 0.22),
        ClubType.HYBRID_7: (16.5, 112, 0.25),
        ClubType.HYBRID_9: (18.0, 106, 0.25),
        ClubType.IRON_2: (13.0, 120, 0.25),
        ClubType.IRON_3: (14.5, 118, 0.25),
        ClubType.IRON_4: (16.0, 114, 0.28),
        ClubType.IRON_5: (17.5, 110, 0.28),
        ClubType.IRON_6: (19.0, 105, 0.30),
        ClubType.IRON_7: (20.5, 100, 0.30),
        ClubType.IRON_8: (23.0, 94, 0.30),
        ClubType.IRON_9: (25.5, 88, 0.30),
        ClubType.PW: (28.0, 82, 0.30),
        ClubType.GW: (30.0, 76, 0.30),
        ClubType.SW: (32.0, 73, 0.30),
        ClubType.LW: (35.0, 70, 0.30),
        ClubType.UNKNOWN: (18.0, 120, 0.25),
    }

    # Optimal smash factors by club category
    _OPTIMAL_SMASH = {
        ClubType.DRIVER: 1.48,
        ClubType.WOOD_3: 1.44, ClubType.WOOD_5: 1.43, ClubType.WOOD_7: 1.42,
        ClubType.HYBRID_3: 1.39, ClubType.HYBRID_5: 1.38,
        ClubType.HYBRID_7: 1.37, ClubType.HYBRID_9: 1.36,
        ClubType.IRON_2: 1.37, ClubType.IRON_3: 1.36,
        ClubType.IRON_4: 1.35, ClubType.IRON_5: 1.34,
        ClubType.IRON_6: 1.33, ClubType.IRON_7: 1.34,
        ClubType.IRON_8: 1.33, ClubType.IRON_9: 1.33,
        ClubType.PW: 1.25, ClubType.GW: 1.23,
        ClubType.SW: 1.22, ClubType.LW: 1.20,
        ClubType.UNKNOWN: 1.35,
    }

    avg_launch, avg_speed, deg_per_mph = _CLUB_LAUNCH_MODEL.get(
        club, (18.0, 120, 0.25)
    )

    # 1. Ball speed adjustment (existing logic)
    speed_delta = ball_speed_mph - avg_speed
    adjustment = -speed_delta * deg_per_mph

    confidence = 0.2

    # 2. Smash factor adjustment (new)
    if club_speed_mph is not None and club_speed_mph > 0:
        smash = ball_speed_mph / club_speed_mph
        optimal_smash = _OPTIMAL_SMASH.get(club, 1.35)
        smash_delta = smash - optimal_smash

        # Low smash = thin/toe = lower launch: 0.4 deg per 0.01 below optimal
        # High smash = up-on-it = slightly higher: 0.2 deg per 0.01 above optimal
        if smash_delta < 0:
            adjustment += smash_delta * 100 * 0.4  # negative delta → negative adj
        else:
            adjustment += smash_delta * 100 * 0.2  # positive delta → positive adj

        confidence = 0.35

    launch_angle = max(5.0, round(avg_launch + adjustment, 1))
    return (launch_angle, confidence)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py::TestEstimateLaunchAngle -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openflight/server.py tests/test_server.py
git commit -m "feat: add smash factor adjustment to launch angle estimation"
```

---

### Task 2: Spin rate adjustment to launch angle

**Files:**
- Modify: `src/openflight/server.py:70-116` (the `estimate_launch_angle` function)
- Test: `tests/test_server.py`

**Step 1: Write failing tests for spin adjustment**

Add to `tests/test_server.py` in `TestEstimateLaunchAngle`:

```python
def test_high_spin_raises_launch(self):
    """High spin should nudge launch angle up."""
    baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
    angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=4000)
    # 4000 rpm is above optimal ~2500 for driver at 143 mph
    assert angle > baseline

def test_low_spin_lowers_launch(self):
    """Low spin should nudge launch angle down."""
    baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
    angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=1000)
    assert angle < baseline

def test_spin_with_smash_raises_confidence(self):
    """Providing both club speed and spin should raise confidence to 0.5."""
    _, conf = estimate_launch_angle(
        ClubType.DRIVER, 143, club_speed_mph=96.6, spin_rpm=2500
    )
    assert conf == 0.5

def test_spin_alone_confidence(self):
    """Spin without club speed should raise confidence to 0.35."""
    _, conf = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=2500)
    assert conf == 0.35
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::TestEstimateLaunchAngle::test_high_spin_raises_launch tests/test_server.py::TestEstimateLaunchAngle::test_low_spin_lowers_launch tests/test_server.py::TestEstimateLaunchAngle::test_spin_with_smash_raises_confidence tests/test_server.py::TestEstimateLaunchAngle::test_spin_alone_confidence -v`
Expected: FAIL — spin_rpm is accepted but not used yet (or not accepted at all)

**Step 3: Add spin adjustment to `estimate_launch_angle()`**

Add after the smash factor block, before the `max(5.0, ...)` line:

```python
    # 3. Spin rate adjustment (new)
    if spin_rpm is not None:
        # Use optimal spin lookup from rolling_buffer module
        from .rolling_buffer.monitor import get_optimal_spin_for_ball_speed
        optimal_spin = get_optimal_spin_for_ball_speed(ball_speed_mph, club)
        spin_delta = spin_rpm - optimal_spin

        # High spin → higher launch (~0.3 deg per 500 rpm above optimal)
        # Low spin → lower launch (~0.3 deg per 500 rpm below optimal)
        # This is a secondary signal, weighted less than smash
        adjustment += (spin_delta / 500) * 0.3

        # Bump confidence
        if confidence >= 0.35:
            confidence = 0.5  # had smash + now spin
        else:
            confidence = 0.35  # spin alone
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py::TestEstimateLaunchAngle -v`
Expected: PASS (all tests including old ones)

**Step 5: Commit**

```bash
git add src/openflight/server.py tests/test_server.py
git commit -m "feat: add spin rate adjustment to launch angle estimation"
```

---

### Task 3: Launch angle carry adjustment function

**Files:**
- Modify: `src/openflight/launch_monitor.py` (add function after `estimate_carry_distance`)
- Test: `tests/test_launch_monitor.py`

**Step 1: Write failing tests**

Add to `tests/test_launch_monitor.py`:

```python
from openflight.launch_monitor import adjust_carry_for_launch_angle


class TestAdjustCarryForLaunchAngle:
    """Tests for launch-angle-based carry distance adjustment."""

    def test_optimal_angle_no_penalty(self):
        """Optimal launch angle should return base carry unchanged."""
        # Driver optimal is ~11 degrees
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=11.0, club=ClubType.DRIVER, confidence=0.5
        )
        assert result == pytest.approx(250, abs=1)

    def test_low_angle_reduces_carry(self):
        """Below-optimal launch angle should reduce carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 4 degrees low * 2.0 yards/deg = -8 yards
        assert result < 250
        assert result == pytest.approx(242, abs=1)

    def test_high_angle_reduces_carry(self):
        """Above-optimal launch angle should reduce carry (less severe)."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=16.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 5 degrees high * 1.5 yards/deg = -7.5 yards
        assert result < 250
        assert result == pytest.approx(242.5, abs=1)

    def test_confidence_scaling(self):
        """Low confidence should reduce the adjustment magnitude."""
        full_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        low_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=0.2
        )
        # Low confidence should produce a smaller penalty
        assert low_conf > full_conf
        # But still some penalty
        assert low_conf < 250

    def test_penalty_capped_at_10_percent(self):
        """Carry penalty should never exceed 10% of base carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=0.0, club=ClubType.DRIVER, confidence=1.0
        )
        assert result >= 250 * 0.90

    def test_iron_optimal_angle(self):
        """Iron clubs should use their own optimal launch angle."""
        # 7-iron optimal is ~20.5 degrees
        result = adjust_carry_for_launch_angle(
            base_carry=150, launch_angle=20.5, club=ClubType.IRON_7, confidence=0.5
        )
        assert result == pytest.approx(150, abs=1)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_launch_monitor.py::TestAdjustCarryForLaunchAngle -v`
Expected: FAIL — `adjust_carry_for_launch_angle` doesn't exist

**Step 3: Implement the function**

Add to `src/openflight/launch_monitor.py` after `estimate_carry_distance()` (after line 149):

```python
def adjust_carry_for_launch_angle(
    base_carry: float,
    launch_angle: float,
    club: ClubType = ClubType.DRIVER,
    confidence: float = 1.0,
) -> float:
    """
    Adjust carry distance based on launch angle deviation from optimal.

    Deviation from optimal launch angle costs carry:
    - Too low: -2.0 yards per degree (ball doesn't get enough height)
    - Too high: -1.5 yards per degree (ball balloons, less severe)
    - Penalty is scaled by confidence and capped at 10% of base carry.

    Args:
        base_carry: Base carry distance in yards (from speed-only model)
        launch_angle: Estimated or measured launch angle in degrees
        club: Club type (determines optimal launch angle)
        confidence: Confidence in the launch angle (0-1), scales the adjustment

    Returns:
        Adjusted carry distance in yards
    """
    # Optimal launch angles by club (from TrackMan data)
    _OPTIMAL_LAUNCH = {
        ClubType.DRIVER: 11.0, ClubType.WOOD_3: 12.5,
        ClubType.WOOD_5: 14.0, ClubType.WOOD_7: 15.5,
        ClubType.HYBRID_3: 13.5, ClubType.HYBRID_5: 15.0,
        ClubType.HYBRID_7: 16.5, ClubType.HYBRID_9: 18.0,
        ClubType.IRON_2: 13.0, ClubType.IRON_3: 14.5,
        ClubType.IRON_4: 16.0, ClubType.IRON_5: 17.5,
        ClubType.IRON_6: 19.0, ClubType.IRON_7: 20.5,
        ClubType.IRON_8: 23.0, ClubType.IRON_9: 25.5,
        ClubType.PW: 28.0, ClubType.GW: 30.0,
        ClubType.SW: 32.0, ClubType.LW: 35.0,
        ClubType.UNKNOWN: 18.0,
    }

    optimal = _OPTIMAL_LAUNCH.get(club, 18.0)
    angle_delta = launch_angle - optimal

    if angle_delta < 0:
        # Too low: steeper penalty
        raw_penalty = abs(angle_delta) * 2.0
    else:
        # Too high: less severe
        raw_penalty = angle_delta * 1.5

    # Scale by confidence so low-confidence estimates have less impact
    penalty = raw_penalty * confidence

    # Cap at 10% of base carry
    max_penalty = base_carry * 0.10
    penalty = min(penalty, max_penalty)

    return base_carry - penalty
```

Also add to the `__init__.py` or the import in test to make it accessible. Update `src/openflight/__init__.py` if `adjust_carry_for_launch_angle` needs exporting.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_launch_monitor.py::TestAdjustCarryForLaunchAngle -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openflight/launch_monitor.py tests/test_launch_monitor.py
git commit -m "feat: add launch angle carry adjustment function"
```

---

### Task 4: Wire launch angle into Shot carry properties

**Files:**
- Modify: `src/openflight/launch_monitor.py:219-248` (Shot properties)
- Test: `tests/test_launch_monitor.py`

**Step 1: Write failing tests**

Add to `tests/test_launch_monitor.py` in `TestShot`:

```python
def test_carry_adjusts_for_launch_angle(self):
    """Shot with launch angle should adjust carry distance."""
    shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
    shot_low_angle = Shot(
        ball_speed_mph=150.0, timestamp=datetime.now(),
        launch_angle_vertical=7.0,  # well below 11 optimal for driver
        launch_angle_confidence=1.0,
    )
    assert shot_low_angle.estimated_carry_yards < shot_no_angle.estimated_carry_yards

def test_carry_unchanged_without_launch_angle(self):
    """Shot without launch angle should use current behavior."""
    shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
    base = estimate_carry_distance(150.0, ClubType.DRIVER)
    assert shot.estimated_carry_yards == base

def test_carry_range_tighter_with_angle(self):
    """Shot with launch angle should have tighter carry range."""
    shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
    shot_angle = Shot(
        ball_speed_mph=150.0, timestamp=datetime.now(),
        launch_angle_vertical=11.0,
        launch_angle_confidence=0.5,
    )
    no_angle_spread = shot_no_angle.estimated_carry_range[1] - shot_no_angle.estimated_carry_range[0]
    angle_spread = shot_angle.estimated_carry_range[1] - shot_angle.estimated_carry_range[0]
    assert angle_spread < no_angle_spread
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_launch_monitor.py::TestShot::test_carry_adjusts_for_launch_angle tests/test_launch_monitor.py::TestShot::test_carry_unchanged_without_launch_angle tests/test_launch_monitor.py::TestShot::test_carry_range_tighter_with_angle -v`
Expected: FAIL — `estimated_carry_yards` doesn't use launch angle

**Step 3: Update Shot properties**

In `src/openflight/launch_monitor.py`, update `estimated_carry_yards`:

```python
@property
def estimated_carry_yards(self) -> float:
    """
    Estimated carry distance based on ball speed, club type,
    and launch angle (when available).
    """
    base = estimate_carry_distance(self.ball_speed_mph, self.club)
    if self.launch_angle_vertical is not None:
        return adjust_carry_for_launch_angle(
            base,
            self.launch_angle_vertical,
            self.club,
            self.launch_angle_confidence or 0.2,
        )
    return base
```

Update `estimated_carry_range`:

```python
@property
def estimated_carry_range(self) -> tuple:
    """
    Return (min, max) carry distance estimate to show uncertainty.
    """
    base = self.estimated_carry_yards
    if self.has_launch_angle:
        return (base * 0.95, base * 1.05)
    return (base * 0.90, base * 1.10)
```

(The range property is unchanged in structure but now `base` incorporates the angle adjustment.)

**Step 4: Run ALL tests to verify nothing breaks**

Run: `uv run pytest tests/ -v`
Expected: PASS (including existing carry range test which checks ±10% for shots without angle)

**Step 5: Commit**

```bash
git add src/openflight/launch_monitor.py tests/test_launch_monitor.py
git commit -m "feat: wire launch angle into carry distance calculation"
```

---

### Task 5: Update server call site to pass smash/spin data

**Files:**
- Modify: `src/openflight/server.py:697-704` (the `on_shot_detected` call site)
- Test: `tests/test_server.py`

**Step 1: Write failing test**

Add to `tests/test_server.py`:

```python
def test_estimate_passes_club_speed(self):
    """estimate_launch_angle should be called with club speed when available."""
    angle_with_smash, conf = estimate_launch_angle(
        ClubType.DRIVER, 143, club_speed_mph=96.6
    )
    angle_without, _ = estimate_launch_angle(ClubType.DRIVER, 143)
    assert conf > 0.2
    # With optimal smash, angle should be close to baseline
    assert abs(angle_with_smash - angle_without) < 1.0

def test_estimate_passes_spin(self):
    """estimate_launch_angle should accept spin data."""
    angle, conf = estimate_launch_angle(
        ClubType.DRIVER, 143, club_speed_mph=96.6, spin_rpm=2500
    )
    assert conf == 0.5
```

(These tests verify the API works — the actual call site change is in the server.)

**Step 2: Run tests to verify current state**

Run: `uv run pytest tests/test_server.py -v`
Expected: Should pass (these test the function directly)

**Step 3: Update the server call site**

In `src/openflight/server.py`, update lines 697-704:

```python
    # If no camera launch angle, estimate from club type and ball speed
    if shot.launch_angle_vertical is None and shot.mode != 'mock':
        estimated = estimate_launch_angle(
            shot.club,
            shot.ball_speed_mph,
            club_speed_mph=shot.club_speed_mph,
            spin_rpm=shot.spin_rpm,
        )
        shot.launch_angle_vertical = estimated[0]
        shot.launch_angle_horizontal = 0.0
        shot.launch_angle_confidence = estimated[1]
        logger.info("Estimated launch angle: %.1f° (conf: %.0f%%)",
                     estimated[0], estimated[1] * 100)
```

**Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openflight/server.py tests/test_server.py
git commit -m "feat: pass smash factor and spin to launch angle estimation"
```

---

### Task 6: Run full lint and test suite

**Step 1: Run ruff check**

Run: `uv run ruff check src/openflight/`
Expected: PASS (no errors)

**Step 2: Run ruff format check**

Run: `uv run ruff format --check src/openflight/`
Expected: PASS

**Step 3: Run pylint**

Run: `uv run pylint src/openflight/ --fail-under=9`
Expected: Score >= 9.0

**Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 5: Fix any issues found, then commit**

```bash
git add -A
git commit -m "chore: fix lint and formatting issues"
```

(Only if there were fixes needed.)
