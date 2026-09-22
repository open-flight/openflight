# OpenFlight Generative AI Policy

OpenFlight accepts thoughtful contributions made with or without generative AI.
This policy applies to code, documentation, issues, pull requests, reviews, and
other project communication created with large language models, code assistants,
or similar tools ("AI tools").

## Guiding Principle

**AI assistance does not transfer responsibility.**

The person submitting a contribution is accountable for its correctness,
security, licensing, scope, and clarity. AI-generated work must meet the same
standards as any other contribution. A contributor must understand the entire
change, personally review it, and be able to explain and defend it during review.

## Acceptable Use

AI tools may be used to:

- learn how an existing part of the project works;
- brainstorm or compare focused implementation approaches;
- complete small routines or repetitive boilerplate;
- draft tests that the contributor reviews, strengthens, and runs;
- refactor or reformat content within the requested scope; and
- proofread or analyze a contributor's own work.

These uses are acceptable only when the contributor:

- remains involved from investigation through validation;
- reviews every changed line and removes speculative or unnecessary output;
- understands the relevant code paths and verifies assumptions against the
  repository, authoritative documentation, or hardware behavior;
- runs appropriate automated and manual tests and reports the results honestly;
- keeps the contribution to one coherent feature or fix;
- follows [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), and the
  project's licensing and security requirements; and
- discloses substantive AI assistance as described below.

## Unacceptable Use

Do not use AI tools to:

- submit code, documentation, or communication that has not been carefully
  reviewed and edited by the contributor;
- generate broad rewrites, speculative features, unrelated cleanup, or other
  changes beyond the stated task;
- rely on generated output as the sole basis for technical or architectural
  decisions without independent investigation and validation;
- claim that tests, hardware checks, benchmarks, or manual verification were
  performed when they were not;
- submit a change that the contributor cannot explain, contextualize, or
  justify during review;
- flood issues, pull requests, reviews, or discussions with generated reports,
  repetitive comments, or responses that do not add personal judgment;
- reproduce third-party work without compatible licensing and required
  attribution; or
- send secrets, credentials, private session data, or other non-public project
  information to an AI service that is not approved to receive it.

## Transparency

Disclose substantive AI assistance in the pull request or other submission. A
concise disclosure should identify the tool's role and the human review and
validation performed. For example: "AI assistance drafted the test scaffolding;
I revised every case and ran the full test file."

Disclosure is not required for spelling corrections, search, or small
autocomplete suggestions that did not materially shape the contribution.
AI-assisted translation is permitted, but the contributor remains responsible
for checking that it accurately represents their meaning.

## Licensing

By contributing, you represent that you have the right to submit the work under
OpenFlight's AGPL-3.0-or-later license. AI tools may produce material derived
from unidentified sources. If you cannot establish that generated material can
be contributed under the project's license, do not submit it.

## Review and Enforcement

Maintainers may ask for a contribution to be reduced, rewritten, tested, or
explained by the contributor. Unreviewed, undisclosed, misleading, or low-quality
AI-assisted submissions may be closed or rejected. Repeated low-quality or
automated submissions may result in contribution restrictions.

This policy was informed by the
[Cilium Generative AI Policy](https://github.com/cilium/community/blob/main/AI-POLICY.md)
and adapted for OpenFlight's development and review workflow.
