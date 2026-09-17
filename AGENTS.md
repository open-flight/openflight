# AGENTS.md

This file provides mandatory guidance to coding agents working in this
repository. It applies to the entire repository. More specific `AGENTS.md` files,
such as `ui/AGENTS.md`, add rules for their directories and must also be read.

## Project Overview

OpenFlight is a DIY golf launch monitor using the OPS243-A Doppler radar and K-LD7 angle radars (deprecated — superseded by a more capable radar chip; K-LD7 support is kept for existing builds only). It measures ball speed, club speed, launch angle, club path, spin rate, and carry distance.

## Development Rules

- **Always use `uv` for Python commands.** Use `uv run` to execute Python tools (pytest, pylint, ruff, etc.). Never use bare `python`, `pip`, `pytest`, etc.
- **Update `pyproject.toml` when adding dependencies.** If new Python packages are introduced, add them to the appropriate dependency list in `pyproject.toml`.
- **Bug reports: write a failing test first.** When the user reports a bug, write a test that reproduces and confirms the bug before investigating or fixing it.
- **Default startup is `scripts/start-kiosk.sh`.** Assume the project is started via this script unless told otherwise. It handles venv activation, UI build, and server launch.

## Scope and Reviewability

Agents must produce small, correct, and reviewable changes.

- Keep every change scoped to one feature, fix, or documentation objective.
- Make the smallest coherent diff that completely solves the requested problem.
- Do not include unrelated cleanup, formatting, renames, dependency updates, or
  opportunistic refactors.
- Refactor only when it is necessary to implement or verify the requested change.
  Put broader refactors in a separate proposal or pull request.
- Do not change public APIs, data formats, hardware assumptions, architecture, or
  module boundaries unless the task explicitly requires it.
- If the task expands while investigating it, stop and surface the added scope
  instead of silently broadening the change.
- Never modify generated, vendored, binary, or model files unless the task
  specifically targets them. Regenerate artifacts using their documented source
  process rather than editing generated output by hand.

## Implementation Guidelines

- Inspect the surrounding code, tests, and local instructions before editing.
- Search for existing helpers and patterns before adding new implementations.
  Reuse or extend equivalent behavior instead of duplicating it.
- Follow the existing structure and style. Prefer consistency with nearby code
  over introducing a new pattern.
- Prefer simple, explicit logic over clever or compact code.
- Do not add wrappers, layers, configuration, fallbacks, or abstractions without
  a concrete requirement. Shared abstractions must represent behavior that is
  genuinely reused, not a hypothetical future need.
- Keep functions and components focused, responsibilities separate, and coupling
  visible. Avoid hidden side effects.
- Handle realistic failure modes and boundary cases explicitly. Do not add
  speculative edge-case machinery with no evidence that the case can occur.
- Do not add a dependency when the standard library or an existing dependency
  already provides the needed behavior.

### Comments

- Prefer clear code and names over explanatory comments.
- Do not add comments that restate the next line or narrate the implementation.
- Add comments only for non-obvious intent, hardware or protocol constraints,
  compatibility requirements, or decisions that cannot be made clear in code.

## Validation

- New or changed behavior requires tests. Bug fixes require a regression test
  that fails before the fix and passes after it.
- Test observable behavior and failure paths. Do not weaken assertions, remove
  coverage, or change tests merely to make an implementation pass.
- Run targeted checks while iterating, then run all checks relevant to the
  changed area before handing off. Use the commands documented below.
- UI changes must follow `ui/AGENTS.md` and include the appropriate unit or E2E
  coverage for affected kiosk sizes and input behavior.
- Hardware-dependent work must state exactly what was tested on real hardware.
  Mock or simulated validation must not be described as hardware validation.
- Never claim a command passed unless it was actually run successfully. Report
  skipped or unavailable checks and the reason.

## Pull Requests and Anti-Slop Audit

Pull requests must follow `.github/pull_request_template.md` and
[CONTRIBUTING.md](CONTRIBUTING.md). The workflows under `.github/workflows/`
enforce the following rules:

- Work from a feature branch; `main` and `master` are blocked as PR source
  branches.
- Treat 50 changed files and 2,000 changed lines as hard ceilings, not targets.
  Split a change well before either limit when it contains separable concerns.
- Provide a concise, non-empty description of at most 6,000 characters, with no
  more than two emojis and no more than 20 code references.
- Preserve the PR template, complete every required section, and check every item
  in `Checklist`. In particular, explain why the change is required, list the
  automated tests, and describe manual human testing.
- Use a conventional PR title in the form
  `<type>(optional scope): <description>`. Allowed types are `feat`, `fix`,
  `docs`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`, `style`, and
  `revert`.
- Keep each commit message at or below 500 characters.
- Ensure every changed text file ends with a newline.
- Add no more than 25 comment lines across changed files. This is a ceiling;
  include only comments that meet the comment rules above.
- Do not change `SECURITY.md`, `LICENSE`, or `CODE_OF_CONDUCT.md` in an ordinary
  PR. Such changes require explicit maintainer coordination.
- Allow maintainers to modify the source branch. More than two combined
  thumbs-down or confused reactions also triggers an audit finding.

The audit additionally checks contributor spam signals: accounts must be at
least 30 days old, must not fork more than six repositories in 24 hours, must
have at least two populated profile signals, and must have at least a 30% global
merge ratio. Draft PRs and configured bots are exempt. Maintainers can use the
`anti-slop-exempt` label for legitimate exceptions.

The audit reports an overall failure after four separate findings. It currently
runs in audit mode and does not label, close, or lock a PR. Treat every check as
a contribution requirement; do not try to spend the four-finding allowance or
game the heuristics.

## GitHub Communication

- Write issue descriptions, PR descriptions, reviews, and comments for humans.
  Lead with the relevant point and include only context needed to act on it.
- Do not post generated walls of text, exhaustive code summaries, repeated
  explanations, or play-by-play accounts of the work.
- Keep claims specific and verifiable. Distinguish observed behavior from
  assumptions and proposals.
- Do not post comments, open issues, change labels, or submit reviews unless the
  user explicitly asks for that external action.

## AI-Assisted Contributions

All agents and contributors must follow [AI-POLICY.md](AI-POLICY.md). In
particular, contributors remain responsible for every submitted line, must be
able to explain the change, must disclose substantive AI assistance, and must
not present generated claims as human testing or investigation.

# Codex Prompt for Plan Mode

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
                              ├── KLD7Tracker (vertical, RADC → launch angle)
                              ├── KLD7Tracker (horizontal, RADC → aim direction)
                              │
                              └── SessionLogger (JSONL files)
```

### Data Flow

1. **SoundTrigger** detects club impact via SEN-14262 GATE → OPS243 HOST_INT
2. **OPS243Radar** (`ops243.py`) dumps rolling buffer I/Q data (4096 samples)
3. **RollingBufferProcessor** (`rolling_buffer/processor.py`) runs FFT + mode-based speed extraction
4. Creates `Shot` object with ball_speed, club_speed, spin, carry
5. **KLD7Trackers** extract launch angle (vertical) and aim direction (horizontal) from RADC phase interferometry, filtered by OPS243 ball speed
6. **Flask server** (`server.py`) emits WebSocket "shot" event
7. **React UI** (`ui/src/`) renders shot data

### Key Modules

- `ops243.py` - OPS243 radar driver, rolling buffer capture, I/Q processing
- `clubs/` - Built-in club types and immutable physics defaults
- `launch_monitor.py` - Shot dataclass and carry estimation
- `rolling_buffer/` - Trigger strategies, I/Q processor, spin detection
- `kld7/` - K-LD7 angle radar (deprecated hardware): RADC streaming, phase interferometry, dual-radar support
- `kld7/radc.py` - FFT, CFAR detection, per-bin angle extraction from raw ADC
- `server.py` - Flask server, shot processing, K-LD7 correlation, carry estimation
- `session_logger.py` - JSONL logging for post-session analysis

### Processing Mode

**Rolling Buffer** is the default and only production mode. The OPS243-A continuously buffers I/Q data. When the sound trigger fires, the buffer is dumped and analyzed for ball speed, club speed, and spin rate. K-LD7 data is correlated via the OPS243 impact timestamp.

## Key Constants

- Sample rate: 30,000 Hz
- FFT window: 128 samples, zero-padded to 4096
- CFAR threshold: SNR > 15.0
- DC mask: 150 bins (~15 mph exclusion zone)
- Shot timeout: 0.5 seconds
- Min ball speed: 35 mph

## Session Logging

Logs written to `~/openflight_sessions/session_*.jsonl` with entry types:

- `session_start`, `session_end` - Session metadata
- `shot_detected` - Detected shots with metrics (ball_speed, club_speed, spin_rpm, carry_spin_adjusted)
- `trigger_event` - Trigger accept/reject with latency (for rolling buffer mode)
- `rolling_buffer_capture` - Raw I/Q samples (4096 each) for offline analysis
- `kld7_buffer`, `iwr6843_capture`, `camera_capture` - Optional hardware evidence correlated by shot number
- `connection`, `ops_clock_sync`, `config_change`, `power_status`, `error` - Runtime diagnostics

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
