# TODO: K-LD7 RADC Club Head Extraction

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Status:** Not started
**Priority:** High — needed for club path and angle of attack

## What We Need

Two measurements from the K-LD7 RADC data that we currently don't extract:

### 1. Club Path (horizontal K-LD7)
The horizontal angle of the club head at impact. Combined with the ball's aim direction (which we already measure), this gives the club path relative to target — a key metric for understanding shot shape (draw/fade/push/pull).

### 2. Angle of Attack (vertical K-LD7)
The vertical angle of the club head at impact. This tells you whether the club is ascending or descending at impact — critical for driver optimization (hitting up) vs iron play (hitting down).

## Approach

Same pipeline as ball extraction — the OPS243 already returns `club_speed_mph` on each shot. Use it exactly like we use `ball_speed_mph`:

1. OPS club speed → compute aliased velocity bin via `ball_bin_range_from_speed(club_speed_mph)`
2. Search RADC frames for energy at that bin (same `find_impact_frames` + CFAR)
3. Phase interferometry at the club's velocity bin gives the angle

The main difference is timing — the club appears **before** the ball in the RADC buffer (club swing → impact → ball launch). The existing `extract_launch_angle` function may already find it if we pass `club_speed_mph` instead of `ball_speed_mph`. It might be as simple as calling the same function twice with different speeds.

## Key Differences from Ball Extraction

| | Ball | Club |
|--|------|------|
| Speed | From OPS243 directly | Estimated from OPS or smash factor |
| Timing | After impact (ball in flight) | Before impact (downswing) |
| Distance | 3-5m (far range) | 1-2m (close range) |
| RCS | Small (golf ball) | Large (club head) |
| Duration in beam | 1-2 frames | 1-2 frames |
| Velocity aliasing | Yes (>62 mph) | Sometimes (>62 mph club speed) |

## Files to Modify

- `src/openflight/kld7/radc.py` — add `extract_club_angle()` function
- `src/openflight/kld7/tracker.py` — add `_extract_club_radc()` method, wire into shot processing
- `src/openflight/server.py` — pass club angle to Shot object (`club_angle_deg` field already exists)
- `tests/test_kld7.py` — add tests with synthetic club head signals

## Data Available for Development

Session logs with RADC data from both vertical and horizontal radars are in `session_logs/`. The RADC frames in the ring buffer contain the club head signal — it just needs to be found and extracted.

Use the capture script to collect RADC data with known club types:
```bash
./scripts/capture_kld7_radc.py --port /dev/kld7_vertical --ops243-port /dev/ttyACM0 --duration 60 --club 7i
```
Then analyze offline with `scripts/analyze_kld7_radc.py`.
