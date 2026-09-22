# Zensical Docs Site — Migration Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-08-23
**Status:** Proposed — awaiting decisions on Issues 1–4
**Scope:** Convert `docs/` into a published Zensical site with a coherent
information architecture, without losing content or breaking existing links.

---

## 1. What exists today

### 1.1 The Zensical scaffold (untracked, unmodified)

`zensical new` was run *inside* `docs/`, producing:

| Path | State |
|---|---|
| `docs/zensical.toml` | Default scaffold. `site_url` points at a GitHub repo URL, not a Pages URL. `nav` lists only the two starter pages. |
| `docs/docs/index.md` | Zensical's "Get started" demo page — feature showcase, zero OpenFlight content. |
| `docs/docs/markdown.md` | Zensical's "Markdown in 5min" demo page. |
| `docs/docs/assets/images/` | `openflight-logo.png`, `favicon.svg` — real assets, not yet referenced by the theme config. |
| `docs/site/` | Built output of the two demo pages. Untracked, not gitignored. |
| `docs/.cache/` | Zensical build cache. Untracked, not gitignored. |
| `docs/.github/workflows/docs.yml` | Deploy workflow in the **wrong location** — GitHub only reads `.github/workflows/` at the repo root. |

Zensical `0.0.57` is installed globally (mise Python 3.11), **not** in the
project venv and **not** in `pyproject.toml`. `uv run zensical` currently works
only by falling through to the global binary.

Nothing from the real documentation set has been migrated. The scaffold is a
blank slate.

### 1.2 The real documentation set

**47 real Markdown files** live under `docs/` (49 including the two scaffold demo
pages), plus 3 standalone HTML pages, 6 PDFs, and ~15 images/SVGs. Another 6
Markdown files sit outside `docs/` but are linked from it.

Grouped by what they actually are:

**Build & setup guides (9 files, 15,602 words) — the highest-value content**

- `PARTS.md` — bill of materials, cost summary, IWR6843 vs deprecated K-LD7
- `sound-trigger-wiring.md` — SEN-14262 → OPS243 `HOST_INT`, R17 solder step
- `ops243-uart-migration.md` — USB → Pi GPIO UART, prerequisite for the IWR6843
- `iwr6843/README.md` — **5,647 words**, the single largest doc: wire, flash,
  mount, aim, measure geometry, verify first capture, club path
- `iwr6843/low-confidence-recovery.md` — OPS-guided vertical recovery policy
- `inclinometer/README.md` — LIS3DH tilt compensation wiring + calibration
- `battery/README.md` — battery provider architecture, UI indicator states
- `battery/geekworm.md` — X1202/X1206 operator guide
- `raspberry-pi-setup.md` — full Pi setup, auto-start, kiosk mode

**Operating guides (7 files, ~5k words)**

- `simulator/README.md`, `simulator/gspro.md`, `simulator/opengolfsim.md`
- `cloud-sync.md`
- `observability.md` — Grafana Cloud / Alloy log shipping
- `swing-speed-training.md`
- `rolling_buffer_spin_detection.md`

**Theory & reference (4 files + 3 HTML)**

- `openflight-cloud-uploader-spec.md` — wire contract, versioned API
- `spin-dechirp-replay.md` — next-gen spin estimator test bench
- `trackman-test-process.md` — validation methodology
- `yolo-performance-tuning.md` — camera experiments (explicitly non-production)
- `iwr6843_field_report_2026-07.html` — **75 KB**, self-styled dark-theme page.
  Plain-language explanation of the OPS243 + IWR6843 pipeline and LCMF-v1.
- `kld7-launch-angle-explained.html` — **57 KB**, same treatment for K-LD7
- `color_palette.html` — UI colour swatch page referenced from `CONTRIBUTING.md`

**Deprecated K-LD7 hardware (5 files, ~10k words)**

`kld7.md`, `kld7-troubleshooting.md`, `kld7-ball-detection-theory.md`,
`kld7-session-review.md`, plus the two K-LD7 datasheet PDFs.

**Internal development artifacts (22 files, 39,228 words) — should not publish**

- `docs/plans/` (11 files) — dated design + implementation plans, many
  containing full source listings and test bodies
- `docs/superpowers/plans/` (5) and `docs/superpowers/specs/` (5)
- `docs/prs/` (1) — a single PR write-up

These are engineering history. They are valuable, but as *repository* history,
not as pages on a user-facing site.

**Docs outside `docs/` that are linked from inside it**

- `firmware/README.md` — IWR6843 firmware build guide (linked twice from
  `iwr6843/README.md`, once from root `README.md`)
- `scripts/analysis/kld7_analysis_tooling.md` (linked from `kld7.md`)
- `cad/IARC_case/README.md` (linked from `PARTS.md`)
- `CONTRIBUTING.md`, `ui/README.md`, `src/analysis/README.md`
- `archive/golf-launch-monitor-wiring-guide.md` — superseded

**Root `README.md` (15.7 KB)** duplicates a large share of the above: overview,
what it measures, hardware table, 5-step getting started, TV display mode, swing
speed, system architecture, radar positioning, configuration, Python API,
limitations, hardware diagnostic, project structure, and a 21-item hand-maintained
documentation index.

---

## 2. Proposed information architecture

Seven top-level sections, organised by **what the reader is trying to do**, not
by which subsystem the file happens to describe. `navigation.tabs` should be
enabled — eight top-level entries in a sidebar is a wall.

```
Home                     index.md            (new — what it is, what it measures,
                                              accuracy claims, pick your path)

Get started
  Overview                                   (new — how a shot becomes numbers)
  Parts list               PARTS.md
  Build order              (new — the sequence, with prerequisites called out)
  Quick start              (new — from assembled hardware to first shot)

Build
  Sound trigger wiring     sound-trigger-wiring.md
  OPS243 → GPIO UART       ops243-uart-migration.md
  IWR6843 angle radar
    Overview               iwr6843/index.md   (split from iwr6843/README.md)
    Wiring                 iwr6843/wiring.md
    Flashing firmware      iwr6843/flashing.md
    Mounting & aiming      iwr6843/mounting.md
    Geometry & calibration iwr6843/geometry.md
    First capture          iwr6843/verify.md
    Club path              iwr6843/club-path.md
  Inclinometer (LIS3DH)    inclinometer/index.md
  Battery (Geekworm)       battery/geekworm.md
  Enclosure & CAD          (new — imported from cad/IARC_case/README.md)

Setup
  Raspberry Pi setup       raspberry-pi-setup.md
  Rolling buffer setup     (extracted — the one-time flash-persist procedure,
                            currently duplicated in 3 places)
  Auto-start & kiosk mode  (extracted from raspberry-pi-setup.md)
  Hardware diagnostic      (new — from superpowers/specs/…hardware-diagnostic)

Using OpenFlight
  Running & modes          (new — every start-kiosk.sh flag, one table)
  TV display mode          (extracted from README.md)
  Swing speed training     swing-speed-training.md
  Simulator connectors
    Overview               simulator/index.md
    GSPro                  simulator/gspro.md
    OpenGolfSim            simulator/opengolfsim.md
  Cloud sync               cloud-sync.md
  Battery monitoring       battery/index.md
  Observability            observability.md

How it works
  Measurement pipeline     (new — architecture diagram, mermaid)
  Rolling buffer & spin    rolling_buffer_spin_detection.md
  IWR6843 launch angle     (converted from iwr6843_field_report_2026-07.html)
  Ballistics & carry       (new — extracted from README + ballistics.py)
  Radar positioning        (extracted from README.md)

Reference
  CLI flags                (new)
  Configuration files      (new — sim.json, cloud.json, config/)
  Session log schema       (new — consolidated from observability.md + CLAUDE.md)
  Cloud uploader contract  openflight-cloud-uploader-spec.md
  Constants                (new)
  Datasheets               (index page linking the 6 PDFs)

Troubleshooting
  Symptom index            (new — routes by symptom to the right guide section)
  …plus the existing per-guide troubleshooting sections, left in place

Development
  Contributing             CONTRIBUTING.md (imported)
  Architecture             (new)
  Testing                  (extracted from CONTRIBUTING.md)
  Firmware build           firmware/README.md (imported)
  Analysis tooling         scripts/analysis/kld7_analysis_tooling.md (imported)
  Spin replay bench        spin-dechirp-replay.md
  TrackMan test process    trackman-test-process.md
  Camera / YOLO            yolo-performance-tuning.md
  UI colour palette        color_palette.html (verbatim asset)

Legacy (K-LD7)             — collapsed section, deprecation banner on every page
  Overview                 kld7.md
  Troubleshooting          kld7-troubleshooting.md
  Ball detection theory    kld7-ball-detection-theory.md
  Session review           kld7-session-review.md
  Launch angle explained   kld7-launch-angle-explained.html (verbatim asset)

Changelog                  CHANGELOG.md
```

Accounting for all 25 publishable files: **23 move as-is** (plus 3 imported from
outside `docs/` — firmware, analysis tooling, IARC case), **2 are split**
(`iwr6843/README.md` → 7 pages, `raspberry-pi-setup.md` → 3), **1 is converted
from HTML**, and **13 are newly authored**. The 22 internal plan/spec/PR files
leave `docs/` entirely (Issue 2).

---

## Issue 1 — `docs_dir` layout: the scaffold nests `docs/docs/`

**Problem.** `docs/zensical.toml` uses the default `docs_dir = "docs"`, resolved
relative to the config file. Content root is therefore `docs/docs/`. Every one of
the ~40 existing relative links (`docs/PARTS.md` from the README,
`../sound-trigger-wiring.md` from `iwr6843/README.md`) assumes content lives at
`docs/`, and `docs/docs/` is an awkward path to type, link, and explain.

`config.py:390` confirms `docs_dir` and `site_dir` are both configurable, both
resolved relative to the project root (the directory holding `zensical.toml`),
and both validated to be distinct and inside the project root.

**Option 1A — Move `zensical.toml` to the repo root, `docs_dir = "docs"`.**
Delete `docs/docs/`, keeping only its `assets/images/` (move to `docs/assets/`).
Content root becomes `docs/`. `site_dir = "site"` at the repo root.

- Effort: 20 minutes.
- Risk: low.
- Impact: **every existing relative link and every `docs/…` README link keeps
  working unchanged.** `edit_uri = "edit/main/docs/"` becomes correct. The
  workflow's `zensical build --clean` from the repo root works with no
  `working-directory` hack.
- Maintenance: config sits with `pyproject.toml` / `Makefile` where a
  contributor expects it.

**Option 1B — Keep `docs/zensical.toml`, set `docs_dir = "."`.**

- Effort: 5 minutes.
- Risk: **high.** `site_dir` would then be nested inside `docs_dir`, and the
  build cache in `docs/.cache/` sits inside the content root. Zensical only
  validates that the two paths differ, not that they do not nest.
- Recommend against.

**Option 1C — Move all content into `docs/docs/`.**

- Effort: 2 hours plus link rewriting.
- Risk: medium — breaks every inbound link from the README, `CONTRIBUTING.md`,
  and any external bookmark or GitHub permalink.
- Recommend against.

> **Recommendation: 1A.** It is the only option that preserves the existing link
> graph for free, and it puts the config where the rest of the project's tooling
> config lives. Nothing else is close.

---

## Issue 2 — 22 internal plan/spec documents would be published

**Problem.** With `docs_dir = "docs"`, everything under `docs/plans/`,
`docs/superpowers/`, and `docs/prs/` builds into the site. That is 39,228 words of
dated implementation plans containing full source listings and test bodies —
**53% of the site's word count and 47% of its pages** (39,228 of 73,743 words;
22 of 47 files), all of it noise for anyone trying to build or run a launch
monitor, and all of it indexed by search.

Zensical exposes no `exclude_docs` / `not_in_nav` key (verified against
`config.py`), so leaving these files in the content root and omitting them from
`nav` does **not** stop them being built and indexed — it only orphans them.

**Option 2A — Move to a repo-root `design/` directory.**
`docs/plans/` → `design/plans/`, `docs/superpowers/` → `design/`,
`docs/prs/` → `design/prs/`.

- Effort: 30 minutes (`git mv`, plus fixing ~4 inbound links).
- Risk: low. These files have almost no inbound links from published docs.
- Impact: content root contains only publishable pages. History preserved and
  browsable on GitHub. A single "Design notes" reference page in the site can
  link to `design/` on GitHub for anyone who wants the archaeology.
- Maintenance: creates an obvious home for future design docs, and a clear rule:
  *`docs/` is published, `design/` is not.*

**Option 2B — Publish them under a collapsed "Archive" section.**

- Effort: 1 hour (needs a deprecation banner on each).
- Risk: low technically, high for signal-to-noise. Search results for "spin"
  would surface a 2026-04 implementation plan above the operating guide.
- Do this only if the design history is considered part of the public value
  proposition of the project.

**Option 2C — Do nothing; leave them orphaned in the content root.**

- Effort: zero.
- Risk: medium. Orphan pages are still built, still in `sitemap.xml`, still in
  `search.json`, still indexed by Google. Worst of both worlds.
- Recommend against.

> **Recommendation: 2A.** The DRY/signal argument is decisive: these documents
> describe work that has already shipped, and the shipped behaviour is documented
> in the operating guides. Keeping two descriptions of the same subsystem —
> one current, one a snapshot from five months ago — is exactly the drift the
> existing `2026-08-09-documentation-audit.md` plan was written to fix.

---

## Issue 3 — Three self-styled HTML pages

**Problem.** `iwr6843_field_report_2026-07.html` (75 KB),
`kld7-launch-angle-explained.html` (57 KB), and `color_palette.html` (8.7 KB)
each ship their own `<style>` block, dark palette, and Google Fonts import.
Zensical copies non-Markdown files verbatim, so they will render as unthemed
islands: no nav, no search index, no dark/light toggle, no mobile handling, no
edit link.

The IWR6843 field report is the best "how does this actually work" explanation in
the repo and is linked directly from `iwr6843/README.md:23`. It deserves to be a
first-class page.

**Option 3A — Convert all three to Markdown.**

- Effort: high — ~6 hours. The field report is 75 KB of hand-authored HTML with
  inline SVG figures and custom layout.
- Risk: medium — real chance of losing figures or nuance in translation.
- Impact: everything searchable, themed, and linkable by heading anchor.

**Option 3B — Keep all three as verbatim assets under `docs/assets/reports/`.**

- Effort: 15 minutes.
- Risk: low.
- Impact: three pages that visibly do not belong to the site. Not searchable.

**Option 3C — Convert the IWR6843 field report only; keep the other two verbatim.**

- Effort: ~4 hours for the field report; 15 minutes for the rest.
- Risk: low-medium.
- Rationale: the field report documents **current, supported** hardware and is
  the natural anchor of the "How it works" section. `kld7-launch-angle-explained.html`
  documents deprecated hardware — it belongs in the Legacy section where the bar
  is "preserved and reachable", not "polished". `color_palette.html` is a
  developer swatch reference where the custom styling *is* the content;
  re-theming it would destroy it.

> **Recommendation: 3C.** Spend the conversion budget on the one page that
> current builders will actually read. Revisit the K-LD7 explainer only if K-LD7
> ever comes back, which the deprecation notice says it will not.

---

## Issue 4 — The README duplicates most of the site

**Problem.** Root `README.md` carries ~2,400 words that will exist verbatim or
near-verbatim in the site: Overview, What It Measures, Hardware at a Glance, the
5-step Getting Started, TV Display Mode, Swing Speed Training, System
Architecture, Doppler Radar Basics, Positioning, Configuration, Python API,
Limitations, Hardware Diagnostic, Project Structure — plus a **hand-maintained
21-entry documentation index** (`README.md:304-325`) that must be updated by hand
on every doc change.

This is the DRY violation with the highest ongoing cost in the repo, and it is
already known: `docs/superpowers/plans/2026-08-09-documentation-audit.md` exists
precisely because these two sources drifted.

**Option 4A — Slim the README to a landing pitch.**
Keep: one-paragraph description, the accuracy/limitations summary, a hardware
photo, the 4-line quick start, links to the docs site, licence, acknowledgements.
Target ~120 lines, down from ~340. Everything else moves into site pages, and the
21-entry index is replaced by a single link to the site nav.

- Effort: 2 hours.
- Risk: low. GitHub visitors get a shorter README with a prominent docs link —
  the standard pattern for any project with a docs site.
- Impact: eliminates the drift surface entirely. The nav becomes the index and
  maintains itself.

**Option 4B — Keep both, accept drift.**

- Effort: zero now; a recurring audit cost forever.
- Risk: high. The repo has already paid this cost once.

**Option 4C — Use `pymdownx.snippets` to include README fragments in site pages.**

- Effort: 3 hours; requires splitting the README into fragment files.
- Risk: medium. Snippet boundaries are invisible when editing the README, and
  fragments that render correctly in both GitHub and Zensical are fiddly
  (relative image paths and link bases differ).
- Reasonable for **one** genuinely shared block, not as a general strategy.

> **Recommendation: 4A.** The README's job on GitHub is to make someone want to
> build one and tell them where the instructions are. It is currently trying to
> be the instructions.

---

## Issue 5 — Cross-links that point outside `docs_dir`

**Problem.** Four published pages link to files outside the content root; these
will 404 on the built site:

| Source | Target |
|---|---|
| `iwr6843/README.md:21,840` | `../../firmware/README.md` |
| `raspberry-pi-setup.md:151` | `../firmware/README.md` |
| `PARTS.md:19` | `../cad/IARC_case/README.md` |
| `kld7.md:118` | `../scripts/analysis/kld7_analysis_tooling.md` |
| `CONTRIBUTING.md:82` | `docs/color_palette.html` |

**Option 5A — Import the three real guides into `docs/`.**
`firmware/README.md` → `docs/development/firmware.md`,
`scripts/analysis/kld7_analysis_tooling.md` → `docs/development/analysis-tooling.md`,
`cad/IARC_case/README.md` → `docs/build/enclosure.md`. Leave stub READMEs in the
original directories pointing at the site (developers browsing `firmware/` still
find their way).

- Effort: 45 minutes.
- Risk: low, but introduces a second copy unless the originals become stubs —
  they must become stubs, not duplicates.

**Option 5B — Rewrite as absolute GitHub blob URLs.**

- Effort: 15 minutes.
- Risk: low.
- Impact: the reader leaves the docs site mid-build for a raw GitHub page. For
  `firmware/README.md`, which is a full build guide reachable from the middle of
  the IWR6843 flashing procedure, that is a poor handoff.

> **Recommendation: 5A for `firmware`, `analysis-tooling`, and the IARC case
> guide** (they are documentation), **5B for `ui/README.md` and
> `src/analysis/README.md`** (they are code-adjacent notes for people already in
> the tree). Stub the originals so there is exactly one copy.

---

## Issue 6 — Deploy workflow is misplaced and unpinned

**Problem.** `docs/.github/workflows/docs.yml` is never read by GitHub — Actions
only discovers workflows in `.github/workflows/` at the repo root. Beyond
location, it: runs on every push to `main` regardless of whether docs changed,
installs `zensical` unpinned (`pip install zensical`), and never builds on pull
requests, so a broken link or bad config is only discovered after merge.

**Fix (no real alternatives worth debating):**

1. Move to `.github/workflows/docs.yml`.
2. Add a `paths:` filter — `docs/**`, `zensical.toml`, and the workflow itself.
3. Pin the version: `pip install zensical==0.0.57` (or install via
   `uv sync --group docs`, so CI and local resolve identically — see Issue 7).
4. Add a `pull_request` job running `zensical build --strict` so broken internal
   links fail the PR instead of shipping.
5. Confirm GitHub Pages source is set to "GitHub Actions" in repo settings —
   the workflow assumes this and will fail loudly otherwise.

---

## Issue 7 — Zensical is not a project dependency

**Problem.** `zensical` resolves to `~/.local/share/mise/installs/python/3/bin/zensical`,
not the project venv, and appears nowhere in `pyproject.toml` or `uv.lock`. This
violates the project rule in `CLAUDE.md` — *"Always use `uv` for Python commands"*
and *"Update `pyproject.toml` when adding dependencies"* — and means a fresh clone
cannot build the docs.

**Fix:**

```toml
[dependency-groups]
docs = ["zensical>=0.0.57,<0.1"]
```

Zensical is pre-`0.1`; an upper bound is warranted. Add `Makefile` targets:

```make
docs-serve:  ; uv run --group docs zensical serve
docs-build:  ; uv run --group docs zensical build --clean --strict
```

---

## Issue 8 — Build output is not gitignored

`docs/site/` and `docs/.cache/` are untracked build artifacts. `.gitignore` has
`build/` but not `site/`. After Issue 1 moves the config to the repo root, add:

```gitignore
# Zensical
/site/
.cache/
```

---

## Issue 9 — `site_url` is wrong

`site_url = "https://github.com/jewbetcha/openflight/docs"` is a repository URL.
Zensical uses `site_url` for `sitemap.xml`, canonical `<link>` tags, and social
card URLs — all three are currently wrong. Set it to the actual Pages URL
(`https://jewbetcha.github.io/openflight/`) or a custom domain if one is planned.
Also fill in `site_description`, `site_author`, and `copyright`, none of which
are set.

---

## Issue 10 — Theme is unbranded

`docs/docs/assets/images/openflight-logo.png` and `favicon.svg` exist but are not
wired into `zensical.toml`. `docs/color_palette.html` defines an established
brand palette (gold `#d4af37`, cream `#f5f0e6`, near-black `#0a0a0f`) that the UI
already uses. Wire up:

- `theme.logo` and `theme.favicon`
- `primary`/`accent` palette colours matched to the UI gold
- `extra.social` → GitHub repo link
- `navigation.tabs` and `navigation.tabs.sticky` (eight top-level sections)
- `content.action.edit` and `content.action.view` (currently commented out)
- `toc.follow`

---

## Issue 11 — Troubleshooting is scattered across six guides

`kld7-troubleshooting.md`, `iwr6843/README.md`, `raspberry-pi-setup.md`,
`sound-trigger-wiring.md`, `battery/geekworm.md`, and `inclinometer/README.md`
each carry their own troubleshooting section. There is real overlap (serial port
identification, permissions, "device not detected") but the surrounding context
differs enough that merging them would make each one worse.

**Recommendation: do not centralise the prose.** Add one `troubleshooting/index.md`
that routes **by symptom** to the correct section anchor:

> *No shots register* → sound trigger § No trigger received · rolling buffer § Verify
> *Launch angle reads zero* → IWR6843 § Verify the first capture
> *Battery shows red `--`* → Geekworm § OpenFlight shows a red `--`

This is the "engineered enough" answer: one new index page, no content moves, no
risk of losing hardware-specific nuance, and the reader gets a single entry point.

---

## Issue 12 — The one-time rolling-buffer setup is documented three times

The `test_rolling_buffer_persist.py --setup` → power-cycle → `--test` procedure
is spelled out in **five published pages** — `sound-trigger-wiring.md:105`,
`raspberry-pi-setup.md:101`, `rolling_buffer_spin_detection.md:20`,
`ops243-uart-migration.md:179`, and `swing-speed-training.md` — plus `CLAUDE.md`
and `AGENTS.md`. The power-cycle wait time and the exact invocation vary between
them. (The root `README.md` correctly links out rather than repeating it.)

**Recommendation:** extract to `setup/rolling-buffer.md` as the single source,
and replace the other four with a one-line link. This is a small enough block
that `pymdownx.snippets` is also viable if inline text is preferred — but a
dedicated page with a stable anchor is simpler and survives editing better.

---

## 3. Phased execution

Each phase ends with a green `zensical build --strict` and a reviewable diff.

### Phase 1 — Make it build (½ day)

1. `git mv docs/zensical.toml zensical.toml`; set `docs_dir = "docs"`,
   `site_dir = "site"`, correct `site_url`, add `site_description`/`copyright`.
2. `git mv docs/docs/assets docs/assets`; delete `docs/docs/`.
3. `git mv docs/.github/workflows/docs.yml .github/workflows/docs.yml`; add
   `paths:` filter, pin the version, add the `pull_request` strict-build job.
4. Add the `docs` dependency group to `pyproject.toml`; add `Makefile` targets.
5. Add `/site/` and `.cache/` to `.gitignore`.
6. `git mv` the 22 internal plan/spec/PR files to `design/` (Issue 2).
7. Author a minimal `docs/index.md` and a `nav` covering every existing file, so
   nothing is orphaned even before restructuring.

**Exit criterion:** `make docs-build` is green; every existing `docs/*.md` is
reachable from the nav; no page 404s.

### Phase 2 — Structure (1 day)

1. Reorganise files into the section directories from §2, using `git mv` so
   history follows.
2. Add `index.md` to each section directory (`navigation.indexes` is already
   enabled and expects them).
3. Split `iwr6843/README.md` into its seven pages, and
   `raspberry-pi-setup.md` into setup / rolling-buffer / auto-start.
4. Rewrite internal links to the new paths; import `firmware/README.md`,
   `kld7_analysis_tooling.md`, and the IARC case guide, leaving stubs (Issue 5).
5. Add the deprecation banner to every Legacy (K-LD7) page.

**Exit criterion:** strict build green; nav matches §2; no orphan pages.

### Phase 3 — Author the gaps (1–2 days)

The 13 new pages from §2, in priority order:

1. `index.md` — the landing page
2. `get-started/overview.md` — how a shot becomes numbers (mermaid diagram)
3. `get-started/build-order.md` — the sequence, with prerequisites explicit
4. `get-started/quick-start.md`
5. `using/running.md` — every `start-kiosk.sh` flag in one table
6. `troubleshooting/index.md` — the symptom router (Issue 11)
7. `setup/rolling-buffer.md` — the deduplicated one-time procedure (Issue 12)
8. `reference/session-log-schema.md` — consolidated from `observability.md` and `CLAUDE.md`
9. `reference/cli.md`, `reference/configuration.md`, `reference/constants.md`
10. `how-it-works/pipeline.md`, `how-it-works/ballistics.md`
11. `reference/datasheets.md` — index for the six PDFs

**Exit criterion:** every nav entry resolves to real content.

### Phase 4 — Convert and polish (1 day)

1. Convert `iwr6843_field_report_2026-07.html` → Markdown (Issue 3C).
2. Move `kld7-launch-angle-explained.html` and `color_palette.html` to
   `docs/assets/reports/`; update the two inbound links.
3. Wire up logo, favicon, palette, social links, `navigation.tabs`,
   `content.action.edit` (Issue 10).
4. Slim the root `README.md` (Issue 4A).

### Phase 5 — Ship (½ day)

1. Set GitHub Pages source to "GitHub Actions".
2. Merge; confirm the deploy.
3. Update `pyproject.toml`'s `[project.urls] Documentation` to the Pages URL
   (currently `…openflight#readme`).
4. Add a docs-site link to the README header and the repo description.

**Total: 4–5 days.** Phases 1–2 alone (1.5 days) produce a working, complete,
navigable site — everything after that is quality.

---

## 4. Decisions needed before Phase 1

| # | Question | Recommendation |
|---|---|---|
| 1 | Config at repo root with `docs_dir = "docs"`? | **1A — yes** |
| 2 | Move the 22 plan/spec docs out of `docs/`? | **2A — `design/`** |
| 3 | How much HTML to convert? | **3C — field report only** |
| 4 | Slim the README? | **4A — yes** |

Issues 5–12 are mechanical and follow from these four.
