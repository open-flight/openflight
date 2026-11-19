## What does this PR do?

<!--
This PR must be scoped to a SINGLE feature or fix. If you find yourself
writing "and also...", split it into separate PRs.

Tell the story:
- What is the change?
- Why was it required? What problem does it solve or what need does it meet?
- Reference related issues with "Fixes #123".
-->

## Why was this required?

<!-- The motivation / story. What prompted this work and what is the impact of not doing it? -->

## Automated tests

<!--
Every PR must include tests. Describe the tests you added or updated and
what behavior they cover. If you believe tests are genuinely not applicable,
explain why here.
-->

## Manual (human) testing

<!--
Every PR must describe the manual testing a human performed. Be specific:
- What did you actually run? (mock mode, real hardware, which UI flows)
- What did you observe? Include numbers/screenshots where relevant.
- What edge cases did you exercise by hand?

If this PR changes the UI (ui/src/), you MUST include screenshots of the
change (drag and drop images here). The PR Checks workflow fails without
them. For UI changes with no visible effect, add the "no-screenshots-needed"
label and explain why.

"Tests pass" is not manual testing — describe what YOU verified by hand.
-->

## Checklist

- [ ] **Single feature/fix** — this PR is scoped to one thing with a clear story above
- [ ] **Automated tests included** — new or updated tests cover this change
- [ ] **Manual testing described** — I documented what I verified by hand above
- [ ] Python tests pass (`uv run pytest tests/ -v`)
- [ ] Pylint passes (`uv run pylint src/openflight/ --fail-under=9`)
- [ ] Ruff passes (`uv run ruff check src/openflight/`)
- [ ] UI builds (`cd ui && npm run build`)
- [ ] UI lint passes (`cd ui && npm run lint`)
- [ ] Updated docs or CHANGELOG if needed
- [ ] No unrelated changes mixed in
