# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenFlight is a DIY golf launch monitor using the OPS243-A Doppler radar and K-LD7 angle radars (deprecated — superseded by a more capable radar chip; K-LD7 support is kept for existing builds only). It measures ball speed, club speed, launch angle, club path, spin rate, and carry distance.

## Development Rules

- **Always use `uv` for Python commands.** Use `uv run` to execute Python tools (pytest, pylint, ruff, etc.). Never use bare `python`, `pip`, `pytest`, etc.
- **Update `pyproject.toml` when adding dependencies.** If new Python packages are introduced, add them to the appropriate dependency list in `pyproject.toml`.
- **Bug reports: write a failing test first.** When the user reports a bug, write a test that reproduces and confirms the bug before investigating or fixing it.
- **Default startup is `scripts/start-kiosk.sh`.** Assume the project is started via this script unless told otherwise. It handles venv activation, UI build, and server launch.

# Claude Code Prompt for Plan Mode

Review this plan thoroughly before making any code changes. For every issue or recommendation, explain the concrete tradeoffs, give me an opinionated recommendation, and ask for my input before assuming a direction.

My engineering preferences (use these to guide your recommendations):

- DRY is important—flag repetition aggressively.
- Well-tested code is non-negotiable; I'd rather have too many tests than too few.
- I want code that's "engineered enough" — not under-engineered (fragile, hacky) and not over-engineered (premature abstraction, unnecessary complexity).
- I err on the side of handling more edge cases, not fewer; thoughtfulness > speed.
- Bias toward explicit over clever.

## 1. Architecture review

Evaluate:

- Overall system design and component boundaries.
- Dependency graph and coupling concerns.
- Data flow patterns and potential bottlenecks.
- Scaling characteristics and single points of failure.
- Security architecture (auth, data access, API boundaries).

## 2. Code quality review

Evaluate:

- Code organization and module structure.
- DRY violations—be aggressive here.
- Error handling patterns and missing edge cases (call these out explicitly).
- Technical debt hotspots.
- Areas that are over-engineered or under-engineered relative to my preferences.

## 3. Test review

Evaluate:

- Test coverage gaps (unit, integration, e2e).
- Test quality and assertion strength.
- Missing edge case coverage—be thorough.
- Untested failure modes and error paths.

## 4. Performance review

Evaluate:

- N+1 queries and database access patterns.
- Memory-usage concerns.
- Caching opportunities.
- Slow or high-complexity code paths.

**For each issue you find**

For every specific issue (bug, smell, design concern, or risk):

- Describe the problem concretely, with file and line references.
- Present 2–3 options, including "do nothing" where that's reasonable.
- For each option, specify: implementation effort, risk, impact on other code, and maintenance burden.
- Give me your recommended option and why, mapped to my preferences above.
- Then explicitly ask whether I agree or want to choose a different direction before proceeding.

**Workflow and interaction**

- Do not assume my priorities on timeline or scale.
- After each section, pause and ask for my feedback before moving on.

---

BEFORE YOU START:
Ask if I want one of two options:
1/ BIG CHANGE: Work through this interactively, one section at a time (Architecture → Code Quality → Tests → Performance) with at most 4 top issues in each section.
2/ SMALL CHANGE: Work through interactively ONE question per review section

FOR EACH STAGE OF REVIEW: output the explanation and pros and cons of each stage's questions AND your opinionated recommendation and why, and then use AskUserQuestion. Also NUMBER issues and then give LETTERS for options and when using AskUserQuestion make sure each option clearly labels the issue NUMBER and option LETTER so the user doesn't get confused. Make the recommended option always the 1st option.

## Commands

### Python Backend

```bash
# Run tests
uv run pytest tests/ -v

# Run single test file
uv run pytest tests/test_launch_monitor.py -v

# Run single test
uv run pytest tests/test_launch_monitor.py::TestLaunchMonitor::test_name -v

# Lint (must score 9.0+)
uv run pylint src/openflight/ --fail-under=9

# Format check
uv run ruff check src/openflight/
uv run ruff format --check src/openflight/
```

### React UI (in /ui directory)

Kiosk / touch conventions for agents: see `ui/AGENTS.md`.

```bash
npm run dev      # Development server with hot reload
npm run build    # Production build
npm run lint     # ESLint
```

### Radar Setup (One-Time)

The OPS243-A must have rolling buffer mode saved to persistent memory for hardware triggers to work.
This is due to a firmware bug where HOST_INT pin mode switches when transitioning modes at runtime.

```bash
# Configure and save rolling buffer mode to flash (one-time)
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup
# Power cycle the radar (unplug USB, wait 3s, replug)
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test
```

### Running the Application

```bash
scripts/start-kiosk.sh              # Default: rolling buffer + sound trigger
scripts/start-kiosk.sh --mock       # Development mode without hardware
scripts/start-kiosk.sh --kld7                          # With K-LD7 angle radars (deprecated; auto-detects horizontal)
```

### Sound Trigger Testing

```bash
# Test persistent rolling buffer + hardware trigger (recommended)
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test

# Test direct hardware sound trigger (GATE → HOST_INT)
uv run python scripts/hardware-test/test_sound_trigger_hardware.py
```

## Architecture

```
React UI (WebSocket) ──► Flask Server ──► RollingBufferMonitor ──► OPS243Radar
                              │                │
                              │                └── SoundTrigger (SEN-14262 → HOST_INT)
                              │
                              ├── IWR6843Runtime (optional, 60 GHz → launch angle & club path)
                              ├── KLD7Tracker (vertical/horizontal, deprecated)
                              ├── Ballistics Simulator (RK4 trajectory & carry)
                              ├── SimConnectors (OpenGolfSim, GSPro, E6, etc.)
                              ├── CloudSync (optional telemetry & session backup)
                              │
                              └── SessionLogger (JSONL files)
```

### Data Flow

1. **SoundTrigger** detects club impact via SEN-14262 GATE → OPS243 HOST_INT
2. **OPS243Radar** (`ops243.py`) dumps rolling buffer I/Q data (4096 samples)
3. **RollingBufferProcessor** (`rolling_buffer/processor.py`) runs FFT + mode-based speed extraction
4. Creates `Shot` object with ball_speed, club_speed, spin, carry
5. **IWR6843** (or legacy **KLD7Trackers**) extracts launch angle and club path from radar measurements
6. **Ballistics engine** (`ballistics.py`) computes trajectory and carry distance
7. **Flask server** (`server.py`) emits WebSocket "shot" event and forwards to connected simulator software
8. **React UI** (`ui/src/`) renders shot data

### Key Modules

- `ops243.py` - OPS243 radar driver, rolling buffer capture, I/Q processing
- `clubs/` - Built-in club types and immutable physics defaults
- `launch_monitor.py` - Shot dataclass and carry estimation
- `ballistics.py` - Numerical ballistic trajectory simulation (drag + Magnus RK4)
- `iwr6843/` - TI IWR6843 mmWave radar driver, L3 raw dump parser, LCMF-v1 launch angle & club path
- `inclinometer.py` - LIS3DH accelerometer tilt compensation service
- `sim/` - Simulator connectors (OpenGolfSim, GSPro, E6 Connect, Garmin) and network transports
- `cloud/` - Telemetry, cloud configuration, session upload, and push error handling
- `rolling_buffer/` - Trigger strategies, I/Q processor, spin detection
- `kld7/` - K-LD7 angle radar (deprecated hardware): RADC streaming, phase interferometry, dual-radar support
- `kld7/radc.py` - FFT, CFAR detection, per-bin angle extraction from raw ADC
- `server.py` - Flask server, AppState runtime management, staged shot processing pipeline
- `session_logger.py` - JSONL logging for post-session analysis

### Processing Mode

**Rolling Buffer** is the default and only production mode. The OPS243-A continuously buffers I/Q data. When the sound trigger fires, the buffer is dumped and analyzed for ball speed, club speed, and spin rate. IWR6843 or legacy K-LD7 data is correlated via the OPS243 impact timestamp.

## Key Constants

- Sample rate: 30,000 Hz
- FFT window: 128 samples, zero-padded to 4096
- DC mask: 150 bins (~15 mph exclusion zone)
- Min ball speed: 15 mph (35 mph for speed-triggered mode)
- K-LD7 OS-CFAR threshold factor: 8.0 (deprecated hardware)

## Session Logging

Logs written to `~/openflight_sessions/session_*.jsonl` with entry types:

- `session_start`, `session_end` - Session metadata
- `reading_accepted` - Individual radar readings
- `shot_detected` - Detected shots with metrics (ball_speed, club_speed, spin_rpm, carry_spin_adjusted)
- `iq_reading` - I/Q streaming detections with SNR/CFAR data
- `iq_blocks` - Raw I/Q data for post-session analysis
- `trigger_event` - Trigger accept/reject with latency (for rolling buffer mode)
- `rolling_buffer_capture` - Raw I/Q samples (4096 each) for offline analysis

## Sound Trigger Hardware

The SparkFun SEN-14262 detects club impact and triggers the OPS243-A via HOST_INT.

**Wiring:**

```
SEN-14262 GATE → OPS243-A HOST_INT (J3 Pin 3)
SEN-14262 VCC  → Pi 3.3V
SEN-14262 GND  → Pi GND (shared with OPS243-A)
```

A through-hole resistor must be soldered into **R17** on the SEN-14262 to reduce preamp gain at 3.3V (47kΩ recommended, lower for noisy environments).

See [docs/sound-trigger-wiring.md](docs/build/sound-trigger.md) for full instructions.

**Trigger Latency:**
| Trigger | Latency | Description |
|---------|---------|-------------|
| `sound` | ~10μs | Hardware: SEN-14262 GATE → HOST_INT |
| `speed` | ~5-6ms | Radar speed detection triggers capture |
