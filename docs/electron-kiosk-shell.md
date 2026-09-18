# Electron Kiosk Shell

`scripts/start-kiosk.sh` launches the React UI inside Electron
(`ui/electron/main.js`) rather than shelling out to whatever browser
happens to be installed on the Pi. This document explains why that's an
improvement, and sketches how it could support self-updating later. It does
not describe anything implemented yet beyond the shell itself — see
[Auto-Updates (Future Work)](#auto-updates-future-work).

## Why Electron Instead Of A System Browser

The old `launch_kiosk_browser` tried `chromium-browser`, then `chromium`,
then `google-chrome`, then `firefox` — whichever the OS image happened to
have, with `--kiosk` flags tuned mostly for Chromium. That worked, but it
carried a few risks an Electron shell removes:

| Concern | System browser | Electron shell |
|---|---|---|
| Rendering engine version | Whatever `apt` installed/upgraded on that Pi — can silently drift between units or after an OS update | Pinned in `ui/package-lock.json` (`electron@44.1.0` today), identical across every Pi until deliberately bumped |
| Kiosk lockdown | `--kiosk` behaves differently across Chromium, Chrome, and Firefox; Firefox's kiosk mode in particular is looser (menu/shortcuts still reachable) | One `BrowserWindow` with `kiosk: true`, no application menu, and `setWindowOpenHandler` denying any popup — the same guarantees everywhere |
| Startup noise | Chromium's "restore previous session" / crash bubbles needed extra flags (`--disable-session-crashed-bubble`) to suppress | Electron has no Chromium session-restore prompt to suppress. Its **default session still persists** under the app `userData` directory (`~/.config/openflight-ui` on Linux) — [Session](https://www.electronjs.org/docs/latest/api/session), [app.getPath('userData')](https://www.electronjs.org/docs/latest/api/app#appgetpathname). That is a *different* profile from system Chromium (`~/.config/chromium` / `chromium-browser`) |
| Maintenance surface | A 4-branch `if/elif` detection ladder to keep working across Raspberry Pi OS Bookworm/Bullseye, Lite/Desktop images | One binary, one launch path; `npm ci` makes the exact runtime reproducible in CI the same way any other dependency is |
| Extensibility | A browser tab is sandboxed from the OS — no filesystem, process, or native API access | The Electron **main process** is a regular Node.js process with full OS access, which is what makes [self-updating](#auto-updates-future-work) possible at all |

The old detection ladder is kept as a fallback (`launch_kiosk_browser` still
tries `chromium-browser`/`chromium` if `ui/node_modules/.bin/electron` is
missing), so a Pi that hasn't installed Electron doesn't lose its kiosk
entirely — it just loses the guarantees above until Electron is installed.

`start-kiosk.sh` builds `ui/dist` only when that directory is missing. If the
UI is already built but Electron is not installed, it *tries* `npm install`
when Node.js is 22.12+. Old Node, an offline Pi, or a failed install logs a
warning and continues to the Chromium fallback instead of aborting startup.

## Browser-local state (breaking on first Electron launch)

Electron does **not** reuse the system Chromium profile. The first time a unit
switches from Chromium to Electron, browser-local `localStorage` looks empty:

| Data | Storage | Survives the switch? |
|---|---|---|
| Profiles and shot logs | Server (`~/.config/openflight/profiles.json`, session JSONL) | Yes |
| Units, theme, language, pinned Live metric | Chromium `localStorage` | No — re-set in the footer / Live grid |
| Validation annotations (comparator device, speed, notes) | `localStorage` key `openflight-validation-entries` | No |

**Before** switching a validation unit to Electron, export the Shots CSV
(**Export CSV** on the Shots tab) while still on Chromium. After the switch,
re-enter units, theme, language, and the pinned metric once.

This is an accepted one-time reset, not a silent migration. Chromium's LevelDB
profile is not copied into Electron `userData`.

## What Didn't Change

Electron here is a shell, not a rewrite: `ui/electron/main.js` opens a
`BrowserWindow` and points it at the same URL the browser used to load
(`http://localhost:8080`, served by Flask from `ui/dist`). The React app,
the WebSocket connection (`socketService.ts`), and the Flask server are
untouched — `getServerOrigin()` still resolves to `window.location.origin`,
which is the Electron window's origin now instead of a browser tab's.

## Process Ownership

`scripts/kiosk-browser.sh` launches Electron (or the Chromium fallback) with
`setsid`, so the whole browser tree, including the zygote, GPU, network and
renderer helpers Chromium forks, lives in one process group that nothing
else on the Pi belongs to. Shutdown signals that group and nothing else. The
earlier cleanup matched the Electron binary path with `pkill -f`, which also
killed kiosks started by *other* launcher instances; see the changelog for
the boot-service crash loop that exposed it. `start-kiosk.sh` additionally
holds `/tmp/openflight-kiosk-<port>.lock` for its lifetime and exits with
status 3 if another instance already holds it.

## Auto-Updates (Future Work)

Nothing below is implemented. It's worth writing down now because "Electron
shell" and "auto-update" are usually mentioned in the same breath, and
because OpenFlight's deployment shape (a small fleet of Pis you personally
maintain, not a public app store release) points toward a different design
than the default Electron answer.

There are two separate things that could be "updated," and they call for
different mechanisms.

### 1. UI content (the React build) — already effectively live

Electron loads a URL, not a bundled copy of `ui/dist`. Whatever Flask is
currently serving is what the window shows. So once a Pi has pulled a new
`ui/dist` (via the existing `git pull && npm run build` flow in
[splash-screen.md](setup/splash-screen.md#updating-an-existing-pi)) and the
service restarts, the Electron window shows the new UI on its next launch —
no Electron-specific update logic needed for this layer. This is already
true today.

### 2. The Electron shell itself

`electron` is a normal `devDependency` in `ui/package.json`. Bumping its
version is a normal dependency bump: change the version, `npm install`,
commit the updated lockfile, `git pull` on each Pi. No runtime auto-update
machinery is needed for this either, as long as updates continue to arrive
through `git pull` + reinstall rather than an out-of-band download.

Installing that package (not running the Electron binary) needs **Node.js
22.12+** on the Pi. Node 20 prints `npm WARN EBADENGINE` for `electron@44`
and its `@electron/get` helper. See the Node install step in
[Raspberry Pi setup](setup/raspberry-pi.md).

### 3. The interesting case: OpenFlight self-updating without an SSH session

The capability an Electron main process adds that a browser tab never had
is **the kiosk can update itself**, because `main.js` runs as a full
Node.js process on the Pi rather than inside a sandboxed tab. Two designs,
in increasing order of complexity:

**A. Main-process-driven `git pull` (recommended starting point)**

The main process periodically (or on a UI-triggered "Check for Updates"
action, via a `contextBridge` preload script) does the same thing an
operator does by hand today:

1. `git fetch` and compare `HEAD` against `origin/<branch>`.
2. If behind: `git pull`, `uv sync`, `npm run build` (in `ui/`).
3. Decide how to apply it:
   - Content-only change (`ui/` touched, `ui/electron/` and
     `ui/package.json`'s `electron` version untouched) → `win.loadURL()`
     again, or just wait for the operator's next launch.
   - Shell change (Electron itself bumped, or `main.js` changed) →
     `app.relaunch(); app.exit(0)`, or restart the systemd unit
     (`systemctl --user restart openflight` / `sudo systemctl restart
     openflight`, per `scripts/setup/openflight.service`) so the new
     `main.js` is picked up.

This reuses the exact update path already documented for manual updates —
it just runs it from inside the app instead of over SSH. It also keeps
using GitHub as the source of truth, so no new release infrastructure,
signing, or hosting is required.

Things to get right if this is built:
- **Trust boundary:** whatever triggers the pull (a timer or a UI button)
  must not be reachable by anything the Flask server exposes over the
  network — this must stay a main-process-only action, not a socket event
  or HTTP endpoint, so a device on the same LAN can't trigger arbitrary
  `git pull`/`uv sync` execution on the Pi.
- **Partial-failure safety:** a `git pull` that succeeds but an `npm run
  build` that fails should not leave the Pi worse off than before — keep
  the previous `ui/dist` until the new build succeeds (e.g. build to a
  temp directory and swap), and skip the restart on build failure.
- **Mid-round updates:** don't apply an update (especially the
  shell-restart kind) while a shot/session is in progress; gate it on
  session/idle state the same way the splash screen gates on startup state.
- **Network dependence:** the Pi may be on a golf-sim LAN with no general
  internet access even when it can reach GitHub, or vice versa — the check
  should fail closed (skip silently) rather than block startup.

**B. `electron-updater` + a packaged build**

The conventional Electron answer — `electron-builder` packages the app,
`electron-updater`'s `autoUpdater.checkForUpdatesAndNotify()` polls a feed
(GitHub Releases, S3, or a self-hosted static server) and swaps the
installed build. This is the right model for shipping to users you don't
operate the hardware for.

It's a bigger lift than option A here, for two reasons specific to this
project:
- It requires the packaging step this shell deliberately skipped (see the
  original Electron-shell decision: "just run from source, no installers").
  `ui/dist` would need to be bundled into the package rather than loaded
  live from Flask, which reintroduces the "which layer updates independently"
  question this doc just resolved for the source-checkout model.
- `electron-updater`'s Linux auto-update support is limited to the AppImage
  format. That's buildable for `arm64` (Raspberry Pi OS 64-bit, which this
  fleet already requires), but it's a new build target, a new artifact to
  test on real hardware, and a release/signing pipeline to stand up — none
  of which exists for this project today.

**Recommendation:** start with (A) if/when self-updating is prioritized. It
matches the fleet's actual shape (Pis you `git pull` on, not an app store
audience), reuses infrastructure that already exists (`uv sync`, `npm run
build`, the systemd unit), and doesn't require adopting a packaging and
release pipeline before there's a concrete need for one. Revisit (B) only if
OpenFlight starts distributing prebuilt images to people who don't run `git
pull` themselves.
