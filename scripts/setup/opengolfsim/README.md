# OpenGolfSim Developer API — club-sync patch

> **Obsolete for OpenGolfSim 1.21.2 and later.** This patch targets 1.13.0's
> OpenConnect `lib/launch/api.js`. In 1.21.2 that file is a thin wrapper over a
> native-JSON Developer API that pushes the selected club itself (`type:
> "player"`), and OpenFlight's `opengolfsim` connector uses the native codec.
> See `docs/using/simulator/opengolfsim.md`. Do not apply it to 1.21.2.

A tiny patch to OpenGolfSim's **bundled Developer API** driver
(`lib/launch/api.js`, TCP **3111**) that makes it report the **real selected
club** to a connected launch monitor — so OpenFlight's club picker follows the
club you pick in OGS.

## Why this (and not the OpenConnect plugin on 921)

We originally tried a user-installed OpenConnect plugin on port 921, but
**OpenGolfSim desktop 1.13.0 does not load user-folder launch plugins** — every
launch monitor is a bundled module in `lib/launch/*.js`. Investigation of those
modules showed:

- The **Developer API** (`api.js`, 3111) already speaks OpenConnect V1 / GSPro
  format (it handles `BallData` / `ShotDataOptions` / heartbeats).
- It already sends a `201 Player` block back — but with **`Club: "DR"`
  hardcoded**, and the real club (delivered to every driver via
  `base.js#setClub(clubId)`) is only logged, never sent.

So club sync is one stubbed field away. This patch finishes it.

## What it changes (≈24 lines of code)

In `lib/launch/api.js`:
1. `ogsClubToOpenConnect()` — map an OGS club id (`7I`, `3W`, `DR`) to an
   OpenConnect/GSPro code (`I7`, `W3`, `DR`).
2. Override `setClub(clubId)` — store the mapped club in `this.currentClub` and
   push a `201 Player` to connected clients immediately (live club changes).
3. The existing `201` reply sends `Club: this.currentClub` instead of `"DR"`.

The essential idea is ~3 lines (store the club, send it); the rest is the id
mapper and the live-push.

## Why a local patch works at all

OGS's Electron **asar-integrity fuse is OFF**
(`EnableEmbeddedAsarIntegrityValidation = 0`), so a modified `app.asar` loads
without rehashing or re-signing. Without that, this wouldn't be feasible.

## Apply / re-apply

```bash
./apply.sh                       # defaults to /Applications/OpenGolfSim.app
```

It backs up `app.asar`, extracts it, applies `api.js.club-sync.patch`, repacks
(keeping native `.node` modules unpacked, verified against the shipped set), and
installs it. If macOS App Management protection blocks the final write, it drops
the patched asar in `~/Downloads/` for you to install via Finder.

**Re-run after every OGS update** — an update overwrites `app.asar`.

`api.patched-reference.js` is the full patched file, for hand-applying if a
future OGS release changes `api.js` enough that the patch doesn't apply cleanly.

## OpenFlight side

Point the connector at the Developer API:

```json
{ "connectors": [ { "type": "opengolfsim", "transport": "openconnect", "enabled": true, "host": "127.0.0.1", "port": 3111 } ] }
```

Then in OGS select the **Developer API** device. Shots stream in; changing the
club in OGS updates OpenFlight's picker and carry/spin model.

## The real fix

This should be **upstreamed to OpenGolfSim** — it finishes their own stub
(`Club:"DR"` → real club). Merged, no local patching is ever needed. See
`api.js.club-sync.patch` for the change.
