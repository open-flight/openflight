# Enhanced Launch Angle Estimation Design

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

## Problem

The camera-based launch angle measurement is fragile (Hough circle detection, depth estimation heuristics, lighting sensitivity). The fallback is a per-club lookup table that only uses ball speed, producing low-confidence estimates. Additionally, launch angle is never used in carry distance calculation -- it only affects the uncertainty band width.

## Goal

Improve launch angle estimation using data we already collect (smash factor, spin rate), and wire launch angle into the carry distance model. Target: 3-5 degree accuracy for normal swings, with honest confidence signaling.

## Design

### 1. Enhanced `estimate_launch_angle()`

**Location:** `server.py` (existing function)

**Current inputs:** club type, ball speed
**New inputs:** club type, ball speed, club_speed (optional), spin_rpm (optional)

**Smash factor adjustment:**
Smash factor (ball_speed / club_speed) indicates strike quality. For each club category, there's an optimal smash factor. Deviation from optimal shifts the launch angle estimate:

- Low smash (thin/toe) -> lower launch. ~0.3-0.5 deg per 0.01 smash below optimal.
- High smash (slight up-on-it for driver) -> slightly higher launch. ~0.2 deg per 0.01 above optimal.

Optimal smash factors by club category:
- Driver: 1.48
- Fairway woods: 1.42-1.45
- Hybrids: 1.36-1.40
- Irons: 1.33-1.38
- Wedges: 1.20-1.25

**Spin rate adjustment (when available):**
Spin correlates with launch angle. High spin + low smash = thin topped (very low launch). High spin + high smash = high-launching iron. Use spin deviation from optimal as a secondary signal, weighted less than smash factor.

**Confidence model:**
- Ball speed only: 0.2
- Ball speed + smash factor: 0.35
- Ball speed + smash factor + spin: 0.5

### 2. Launch angle carry adjustment

**Location:** New function in `launch_monitor.py`

Each club has an optimal launch angle (from TrackMan data, already in the lookup table). Deviation from optimal costs carry:

- Too low: -2.0 yards per degree below optimal (ball doesn't get enough height)
- Too high: -1.5 yards per degree above optimal (ball balloons, less severe due to Magnus lift)
- Cap: 10% max penalty to prevent wild swings from bad estimates
- Apply confidence weighting: scale the adjustment by launch angle confidence so low-confidence estimates produce smaller corrections

### 3. Integration into Shot carry calculation

Wire into `Shot.estimated_carry_yards` and `Shot.estimated_carry_range`:
- If launch angle is available, apply the angle-based carry adjustment
- Update `estimated_carry_range` to reflect the tighter estimate when angle is known
- Keep backward compatibility: no launch angle = current behavior unchanged

### 4. Unified carry estimation

Consider a single `estimate_carry()` that accepts all available inputs (ball speed, club, launch angle, spin, smash factor) and applies adjustments incrementally. This avoids the current pattern of separate `estimate_carry_distance()` and `estimate_carry_with_spin()` functions that don't compose.

## Non-goals

- No new hardware
- No camera changes
- No changes to the UI (it already displays launch angle)
