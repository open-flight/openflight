---
icon: lucide/archive
---

# Archive

**These documents are engineering history, not instructions.**

Everything in this section is a dated design note, implementation plan, or pull
request write-up describing work that has already shipped. They are kept because
they record *why* subsystems are shaped the way they are — the constraints, the
alternatives considered, and the measurements that settled the argument.

!!! warning "Do not follow these as guides"

    Archive documents describe the state of the project on the date in their
    filename. Commands, file paths, constants, and APIs in them may no longer
    match the code. For anything you intend to *do*, use the current guides:

    - Building the hardware → [Build](../build/sound-trigger.md)
    - Running the software → [Using OpenFlight](../using/simulator/index.md)
    - How a measurement works → [How it works](../how-it-works/rolling-buffer.md)

## Design notes

The "what and why" documents — problem statement, approach, architecture, and
non-goals — written before implementation started.

| Date | Document |
| --- | --- |
| 2026-03-03 | [Enhanced launch angle estimation](plans/2026-03-03-enhanced-launch-angle-design.md) |
| 2026-03-23 | [K-LD7 angle radar integration](plans/2026-03-23-kld7-angle-radar-design.md) |
| 2026-03-28 | [K-LD7 full stack integration](plans/2026-03-28-kld7-integration-design.md) |
| 2026-04-05 | [K-LD7 raw ADC processing](plans/2026-04-05-kld7-radc-processing-design.md) |
| 2026-04-10 | [Spin detection rework](plans/2026-04-10-spin-detection-rework-design.md) |
| 2026-04-15 | [Spin & angle data quality validation](specs/2026-04-15-spin-angle-validation-design.md) |
| 2026-04-20 | [Hardware diagnostic script](specs/2026-04-20-hardware-diagnostic-design.md) |
| 2026-06-09 | [K-LD7 timing and PRF probe](specs/2026-06-09-kld7-prf-probing-design.md) |
| 2026-07-25 | [IWR6843 club path from pre-impact frames](specs/2026-07-25-iwr6843-club-path-design.md) |
| 2026-08-09 | [Optional battery and camera build items](specs/2026-08-09-optional-build-items-design.md) |

## Implementation plans

Task-by-task execution plans, most containing the source and test bodies as they
were written at the time.

| Date | Document |
| --- | --- |
| 2026-03-03 | [Enhanced launch angle estimation](plans/2026-03-03-enhanced-launch-angle-plan.md) |
| 2026-03-28 | [K-LD7 full stack integration](plans/2026-03-28-kld7-integration-plan.md) |
| 2026-04-05 | [K-LD7 raw ADC processing](plans/2026-04-05-kld7-radc-processing-plan.md) |
| 2026-04-06 | [RADC launch angle full-stack](plans/2026-04-06-radc-launch-angle-fullstack.md) |
| 2026-04-10 | [Spin detection rework](plans/2026-04-10-spin-detection-rework-plan.md) |
| 2026-04-15 | [Spin & angle data quality validation](superpowers-plans/2026-04-15-spin-angle-validation.md) |
| 2026-04-20 | [Hardware diagnostic script](superpowers-plans/2026-04-20-hardware-diagnostic.md) |
| 2026-06-09 | [K-LD7 timing and PRF probe](superpowers-plans/2026-06-09-kld7-prf-probing.md) |
| 2026-06-13 | [Sim connector abstraction](superpowers-plans/2026-06-13-sim-connector-abstraction.md) |
| 2026-08-09 | [Documentation audit](superpowers-plans/2026-08-09-documentation-audit.md) |
| 2026-08-23 | [Zensical docs site migration](plans/2026-08-23-zensical-docs-site-plan.md) |
| — | [K-LD7 RADC club head extraction (TODO)](plans/kld7-club-extraction-TODO.md) |

## Pull request notes

| Date | Document |
| --- | --- |
| 2026-04-02 | [K-LD7 shot correlation and false-positive filtering](prs/2026-04-02-kld7-shot-correlation-pr.md) |
