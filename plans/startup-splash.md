# Plan: OpenFlight Startup Splash

> Source PRD: Conversation brief approved on 2026-08-16

## Architectural decisions

- **Feature gate**: The splash is opt-in through `--startup-splash` until Raspberry Pi field validation is complete. Without the flag, startup behavior is unchanged.
- **Routes**: The splash is served only on loopback and redirects to the configured OpenFlight kiosk URL after that URL responds successfully.
- **Status schema**: Startup state will use a versioned document with component states `waiting`, `starting`, `ready`, `skipped`, and `error`.
- **Component visibility**: Disabled components are omitted rather than displayed as inactive rows.
- **Initialization model**: Hardware remains sequential and synchronous. The splash adds observability but does not move hardware initialization into background threads.
- **Status ownership**: Startup publishes explicit structured status. The splash will not infer progress by parsing human-readable logs.
- **Assets**: The splash uses local OpenFlight branding and has no network dependency.
- **Compatibility**: Normal and Battery Power launchers share the same splash behavior through `scripts/start-kiosk.sh`.

---

## Phase 1: Feature-Flagged Fast First Paint

**User stories**: As a golfer, I immediately see that my launch tap registered; as an operator, I can disable the experiment without changing normal startup.

### What to build

Add an opt-in, branded splash that opens Chromium before dependency preparation and hardware initialization. The splash displays a generic preparation message, polls the configured OpenFlight URL, and redirects to the real application when the server becomes reachable. Existing startup remains unchanged without the flag.

### Acceptance criteria

- [x] `--startup-splash` enables the new path and is not forwarded to the OpenFlight server.
- [x] Chromium begins opening the local splash before dependency synchronization and hardware initialization.
- [x] The splash uses the OpenFlight logo and clearly says that software and hardware are being prepared.
- [x] The splash automatically replaces itself with the configured OpenFlight URL when it becomes reachable.
- [x] The splash helper and browser participate in the existing cleanup lifecycle.
- [x] A splash startup failure falls back safely without changing the flag-off path.
- [x] Automated tests cover flag parsing, launch ordering, redirect behavior, and shell syntax.

---

## Phase 2: Truthful Component Progress

**User stories**: As a golfer, I can see which configured subsystem is currently starting; as a technician, I can distinguish slow initialization from a failure.

### What to build

Introduce the versioned startup status document and publish state transitions from the existing shell preparation and sequential server initialization boundaries. Show only configured components, including OPS, TI IWR6843 or legacy K-LD7, camera, inclinometer, simulator connections, and power monitoring where applicable.

### Acceptance criteria

- [x] Every displayed component is enabled in the active configuration.
- [x] Each displayed state reflects an explicit startup event rather than elapsed-time guesses or parsed logs.
- [x] OPS, angle radar, and camera success paths are covered by automated tests.
- [x] Optional inclinometer, simulator, legacy radar, and power states use the same schema.
- [x] Status publication does not make hardware initialization asynchronous.

---

## Phase 3: Useful Startup Failures

**User stories**: As a golfer, I know why OpenFlight did not become ready; as a technician, I know where to find the supporting log.

### What to build

Keep a useful splash state visible when preparation or hardware initialization fails. Identify the failed component, show a concise recovery-oriented message, and expose the relevant log location while preserving orderly process and hardware cleanup.

### Acceptance criteria

- [x] Preparation, server timeout, and component initialization failures display distinct messages.
- [x] Failure details avoid raw tracebacks while identifying the relevant session or terminal log.
- [x] Failed startup does not leave a splash helper, duplicate server, browser, or hardware owner behind.
- [x] Failure behavior is tested without requiring physical radar hardware.

---

## Phase 4: Raspberry Pi Rollout and Default Decision

**User stories**: As an operator, I can demo the splash safely on the Pi; as a maintainer, I can promote or roll it back predictably.

### What to build

Deploy the feature branch into a separate Raspberry Pi work folder with a dedicated desktop launcher. Validate normal and Battery Power configurations, measure perceived and actual startup timing, then decide whether to enable the splash by default.

### Acceptance criteria

- [x] The demo uses a separate Pi work folder and desktop entry.
- [x] The production launcher and checkout remain unchanged during evaluation.
- [x] Repeated taps cannot create concurrent hardware owners.
- [x] Tap-to-splash and tap-to-ready behavior is accepted on the target Pi.
- [x] Promotion and rollback steps are documented before changing the default.

Field validation completed on a Raspberry Pi at 800×480 on 2026-08-17. The
operator exercised successful OPS/TI/power startup, unplugged OPS and TI error
states, a wedged TI firmware state, repeated taps, failure dismissal, and
relaunch. Operational setup and rollback are documented in
`docs/splash-screen.md`.
