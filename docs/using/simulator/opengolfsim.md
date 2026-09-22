# OpenGolfSim

OpenFlight streams shots into [OpenGolfSim](https://opengolfsim.com/) through its
built-in **Developer API** (TCP **3111**), which speaks the **OpenConnect V1**
protocol — so the `opengolfsim` connector reuses the same shared codec as GSPro,
just pointed at OGS and reported as "OpenGolfSim".

See the connector architecture in [README.md](https://github.com/jewbetcha/openflight/blob/main/README.md). This page covers
setup specific to OpenGolfSim.

## Requirements

- **OpenGolfSim desktop app** with the **Developer API** launch-monitor device
  selected (it listens on TCP 3111).
- **Network reachability.** The machine running OpenFlight (e.g. the Raspberry
  Pi) and the PC running OpenGolfSim must be on the same LAN, and OGS's 3111 must
  be reachable (mind the host firewall). You need the OpenGolfSim PC's IP.
- No account/credentials are sent by OpenFlight — the API has no auth.

## Setup

1. In OpenGolfSim, select the **Developer API** launch-monitor device (port 3111).
2. **Find the OpenGolfSim PC's IP** (e.g. `192.168.1.60`).
3. **Configure OpenFlight.** Copy the example config if you haven't already:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   Enable the OpenGolfSim connector with the PC's IP:
   ```jsonc
   {
     "connectors": [
       { "type": "opengolfsim", "enabled": true, "host": "192.168.1.60", "port": 3111 }
     ]
   }
   ```
4. **Start OpenFlight with simulator connectors on** (`--sim`, off by default):
   ```bash
   scripts/start-kiosk.sh --sim
   ```
   The header OpenGolfSim pill should turn **green**.
5. **Hit a shot.** It appears in OpenGolfSim; with debug mode on, the "Sent to
   OpenGolfSim" panel shows the values sent with measured/estimated badges.

## What gets sent

OpenConnect V1 ball data (OGS computes carry itself; club/face data isn't sent):

```json
{ "DeviceID": "OpenFlight", "Units": "Yards", "ShotNumber": 1, "APIversion": "1",
  "BallData": { "Speed": 135.0, "VLA": 11.1, "HLA": 1.2, "TotalSpin": 4800, "SpinAxis": -2.5, ... } }
```

`TotalSpin` uses the measured value when high-confidence, otherwise a per-club
model — the "Sent to OpenGolfSim" badges (debug mode) show which.

## Club sync (needs a small OGS patch)

OGS's Developer API already replies with an OpenConnect `201 Player` block, but
ships with a hardcoded `Club:"DR"` — the real club reaches the bundled driver
via `setClub(clubId)` but isn't wired into the reply (confirmed against OGS's own
source). A small local patch finishes it:

1. Apply **`scripts/setup/opengolfsim/`** (this repo) to your local OGS — it
   makes the Developer API send the *real* club in the `201`. See that folder's
   `README.md` for `apply.sh` and the re-apply-after-update note.
2. Relaunch OGS (with the Developer API device selected).

With the patch, changing the club in OpenGolfSim pushes a `201 Player` that
OpenFlight applies to its club picker and carry/spin model. Without it, shots
still stream fine — you just set the club manually in OpenFlight.

> Club sync is **one-way: OGS → OpenFlight.** OGS's API has no command for a
> device to set the club, so OpenFlight can't push its club choice to the sim;
> the sim is the source of truth. The durable fix is upstreaming the ~3-line
> Developer-API change to OGS so no local patch is needed.

## Troubleshooting

- **Pill stays amber (connecting / reconnecting):** OpenFlight can't reach
  `host:port`. Verify the Developer API device is selected in OGS, the IP is
  correct, 3111 isn't firewalled, and you launched with `--sim`. ("Connecting"
  means it has never connected yet; "reconnecting" means an established
  connection dropped.)
- **Shots don't appear:** confirm OGS is on a hittable screen with the Developer
  API connected. Check `sim_send` entries in the session log.
- **First shot after connecting sometimes doesn't register:** the first shot on a
  fresh connection occasionally doesn't play in OGS while later shots do. This
  appears to depend on OpenGolfSim's screen/round state (it must be on a hittable
  screen), not on OpenFlight — OpenFlight sends the shot correctly (confirm the
  `sim_send` entry in the session log; OGS replies, e.g. `Code 200 "Club Data
  received"`). Workaround: make sure OGS is on a hittable screen before you start,
  and/or hit a throwaway first shot.
- **Club shows "DR" / doesn't follow OGS:** the club-sync patch isn't applied (or
  an OGS update reverted it) — re-run `scripts/setup/opengolfsim/apply.sh`.

## References

- [OpenGolfSim Developer API](https://help.opengolfsim.com/desktop/apis/)
