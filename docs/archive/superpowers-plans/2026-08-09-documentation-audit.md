# Documentation Audit Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenFlight's active documentation concise, internally linked, and consistent with the current hardware, CLI, setup scripts, and runtime behavior.

**Architecture:** Treat source code, checked-in configuration, and CLI parsers as authoritative. Keep historical plans, specs, PR notes, changelog entries, and `archive/` as records; update active guides and READMEs. Consolidate duplicate guidance by linking to one owner document per topic.

**Tech Stack:** Markdown, Bash CLI inspection, `uv run` Python validation, Git.

---

### Task 1: Add the optional battery and camera hardware

**Files:**
- Modify: `docs/PARTS.md`
- Modify: `docs/raspberry-pi-setup.md`

- [x] **Step 1: Add the products and ballpark prices to the parts list**

Add the Geekworm X1202 UPS HAT at approximately `$48`, its four separately
sold compatible 18650 cells at approximately `$25 total`, and the InnoMaker
OV9281 camera at approximately `$30`. Mark the camera software path as
experimental so the purchase list does not imply production kiosk support.

- [x] **Step 2: Add matching optional prerequisite bullets**

Keep current optional hardware separate from the deprecated K-LD7 list:

```markdown
**Optional:**
- Geekworm X1202 UPS HAT + 4 compatible 18650 cells — portable Pi 5 power
- InnoMaker OV9281 global-shutter camera — experimental camera work
```

- [x] **Step 3: Check the two supplied links and Markdown table rendering**

Run: `git diff --check -- docs/PARTS.md docs/raspberry-pi-setup.md`

Expected: no output and exit status 0.

### Task 2: Refresh the main build path

**Files:**
- Modify: `README.md`
- Modify: `docs/raspberry-pi-setup.md`
- Modify: `CONTRIBUTING.md`

- [x] **Step 1: Make the README describe the current hardware**

Make IWR6843 the current launch-angle and experimental club-path source,
label K-LD7 as legacy, and describe OPS spin as an experimental candidate that
does not drive carry by default. Replace the K-LD7 architecture/positioning
diagram and remove the deleted `--kld7-geometry` example.

- [x] **Step 2: Correct startup examples**

Use current commands only:

```bash
scripts/start-kiosk.sh
scripts/start-kiosk.sh --iwr6843 --ops-port /dev/ttyAMA0
scripts/start-kiosk.sh --swing-speed
scripts/start-kiosk.sh --mock
```

Refer IWR6843 geometry to `docs/iwr6843/README.md`; do not duplicate example
measurements as universal requirements.

- [x] **Step 3: De-duplicate Raspberry Pi setup guidance**

Keep the install, OPS persistence, IWR6843 handoff, auto-start, and concise
troubleshooting paths. Replace repeated K-LD7 setup/calibration prose with a
short legacy pointer to `docs/kld7.md` and `docs/kld7-troubleshooting.md`.
Replace the nonexistent `openflight --port ... --info` command with the
hardware diagnostic.

- [x] **Step 4: Align contributor structure and commands**

Keep `uv` as the only documented Python runner and include `iwr6843/`, `sim/`,
and `cloud/` in the current package map.

### Task 3: Correct current subsystem guides

**Files:**
- Rewrite: `docs/rolling_buffer_spin_detection.md`
- Modify: `docs/kld7.md`
- Modify: `docs/kld7-troubleshooting.md`
- Modify: `docs/trackman-test-process.md`
- Modify: `docs/simulator/README.md`
- Modify: `docs/simulator/gspro.md`
- Modify: `docs/simulator/opengolfsim.md`

- [x] **Step 1: Replace the obsolete rolling-buffer implementation plan**

Document only current behavior: persistent `GC` mode, 4096 I/Q samples,
sound trigger as the production default, overlapping FFT speed extraction,
the experimental ungated multitaper spin candidate, and its carry limitation.
Remove deleted `--mode`, unsupported `sound-gpio`, old `G1` instructions,
the level-shifter requirement, speculative implementation phases, and sample
code that no longer matches the implementation.

- [x] **Step 2: Repair legacy K-LD7 guidance**

Use this startup shape everywhere:

```bash
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees>
```

Remove `--kld7-geometry`, link the analysis tooling and TrackMan process from
the K-LD7 landing guide, and drop the broken timing-drift link.

- [x] **Step 3: Repair simulator startup commands and diagrams**

Use `scripts/start-kiosk.sh --sim` for the base case. Describe angle sources as
optional instead of requiring deprecated K-LD7 hardware.

- [x] **Step 4: Convert remaining user-facing bare Python commands to `uv run`**

Limit this to active guides; do not rewrite historical changelog entries or
archived plans.

### Task 4: Make camera documentation honest and useful

**Files:**
- Rewrite: `docs/yolo-performance-tuning.md`
- Modify: `docs/PARTS.md`
- Modify: `docs/raspberry-pi-setup.md`
- Modify: `README.md`

- [x] **Step 1: Replace obsolete production-camera claims**

State that `scripts/start-kiosk.sh` currently passes `--no-camera`, the setup
script does not install camera dependencies, and the hardware is optional for
development/experimentation. Do not claim the camera is enabled by default.

- [x] **Step 2: Keep only verified experiment commands**

Use the real script path and `uv`:

```bash
uv run python scripts/vision/test_yolo_detection.py --help
```

Retain concise tuning guidance only where its flag exists in that script.

- [x] **Step 3: Link the camera guide from the purchase and setup paths**

This turns the previously orphaned guide into an explicit optional path.

### Task 5: Remove superseded and orphaned documentation states

**Files:**
- Delete: `docs/cloud-sync-design.md`
- Delete: `docs/openflight_diagram.html`
- Delete: `docs/spin_detection_diagram.html`
- Modify: `docs/openflight-cloud-uploader-spec.md`
- Modify: `docs/cloud-sync.md`
- Modify: `docs/iwr6843/README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `src/analysis/README.md`

- [x] **Step 1: Remove the superseded cloud draft**

The implemented uploader spec and `docs/cloud-sync.md` remain authoritative;
remove references that tell readers to compare against the obsolete draft.

- [x] **Step 2: Fix cloud setup commands**

Replace `uv pip` and bare `python -m` examples with repository-standard `uv`
commands.

- [x] **Step 3: Make the analysis README useful**

Document `src/analysis/analyze_capture.py` in a few lines and point users to
`scripts/analysis/capture_iq.py`, preserving the directory-local README rather
than leaving a one-line orphan.

- [x] **Step 4: Resolve orphaned active HTML pages**

Link the current UI palette and IWR6843 field report from their owner guides.
Remove the unreachable diagrams that present streaming as current, rolling
buffer as new, and obsolete `G1` spin behavior.

### Task 6: Validate the documentation set

**Files:**
- Verify: all modified Markdown files

- [x] **Step 1: Validate representative CLI examples against parsers**

Run:

```bash
scripts/start-kiosk.sh --dry-run
scripts/start-kiosk.sh --iwr6843 --ops-port /dev/ttyAMA0 --dry-run
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt 10 --dry-run
scripts/start-kiosk.sh --sim --dry-run
uv run openflight-server --help
uv run python scripts/hardware-test/diagnose.py --help
uv run python scripts/vision/test_yolo_detection.py --help
```

Expected: each command exits 0; dry runs print the corresponding valid server
arguments.

- [x] **Step 2: Check relative links and orphaned active guides**

Run the repository-local read-only Markdown link audit with `uv run --no-sync
python`; expect no missing relative file targets. Review remaining zero-inbound
READMEs and historical records manually rather than treating directory-local
READMEs as dead files.

- [x] **Step 3: Check stale commands and formatting**

Run:

```bash
rg -n -- '--kld7-geometry|--mode rolling-buffer|sound-gpio|camera is enabled by default' README.md docs CONTRIBUTING.md
git diff --check
git status --short
```

Expected: obsolete phrases appear only in historical changelog/plan records;
the diff check passes; unrelated user files remain untouched.

- [x] **Step 4: Commit the documentation update on main**

```bash
git add README.md CONTRIBUTING.md \
  docs/PARTS.md docs/raspberry-pi-setup.md \
  docs/rolling_buffer_spin_detection.md docs/kld7.md \
  docs/kld7-troubleshooting.md docs/kld7-ball-detection-theory.md \
  docs/trackman-test-process.md docs/iwr6843/README.md \
  docs/simulator/README.md docs/simulator/gspro.md \
  docs/simulator/opengolfsim.md docs/yolo-performance-tuning.md \
  docs/openflight-cloud-uploader-spec.md docs/cloud-sync.md \
  docs/superpowers/plans/2026-08-09-documentation-audit.md \
  scripts/analysis/capture_iq.py scripts/vision/test_yolo_detection.py \
  src/analysis/README.md
git add -u docs/cloud-sync-design.md docs/openflight_diagram.html \
  docs/spin_detection_diagram.html
git commit -m "docs: refresh build and setup guidance"
```
