# OpenGolfSim

OpenFlight streams shots into [OpenGolfSim](https://opengolfsim.com/) through its
built-in **Developer API** on TCP **3111**. Current OpenGolfSim speaks its **own
JSON format** there (not OpenConnect), so the `opengolfsim` connector uses a
dedicated codec (`src/openflight/opengolfsim/`).

> **Version note.** OpenGolfSim **1.13.0**'s bundled Developer API accepted
> OpenConnect V1 on 3111. **1.21.2 does not**: it answers OpenConnect with
> `{"status":400,"error":"Unable to parse payload"}` and plays nothing
> (verified against 1.21.2's `lib/developerAPI.js` and on hardware). If you are
> stuck on an old build that only takes OpenConnect, use
> `{"type": "gspro", "port": 3111, ...}` instead, which sends OpenConnect to
> any port.

See the connector architecture in [index.md](index.md). This page covers setup
specific to OpenGolfSim.

## Requirements

- **OpenGolfSim desktop app**, signed in (the Developer API only starts after
  login), with the **Developer API** launch-monitor device selected. With any
  other device (e.g. Mouse) OGS echoes API shots back but does not play them.
- **Network reachability.** OGS listens on all interfaces on 3111 (its log says
  "localhost", but the server binds every interface). The OpenFlight machine
  must reach the OGS PC's IP on 3111; mind the host firewall.
- No account/credentials are sent by OpenFlight. The API has no auth.

## Setup

1. In OpenGolfSim: **Settings → Shot Data → Developer API**.
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

## Wire format

On connect, OpenFlight sends `{"type":"device","status":"ready"}`. Each shot:

```json
{"type":"shot","shot":{"ballSpeed":135.0,"verticalLaunchAngle":11.1,
 "horizontalLaunchAngle":1.2,"spinAxis":-2.5,"spinSpeed":4800}}
```

- Speeds are always sent in **mph** with no `unit` field (OGS's default). OGS
  multiplies `ballSpeed` by 2.237 when `"unit":"metric"`, so the codec never
  sets it.
- OGS computes carry itself and takes no club or face data.
- OGS parses each TCP read as one JSON document, so frames are never batched.

OGS sends back (newline-terminated, to **every** connected client):

| Message | Meaning | OpenFlight event |
|---|---|---|
| `{"type":"shot","shot":{...}}` | shot accepted (echo) | `ShotAck` |
| `{"type":"result","data":{"result":{carry, height, roll, total, lateral},"club","shot","sessionId"}}` | ball came to rest; distances in **meters** | ignored |
| `{"type":"player","data":{"player":{...},"club":{"name":...}}}` | player or club changed in OGS | `PlayerUpdate` (club sync) |
| `{"status":400,"error":"Unable to parse payload"}` | payload rejected | `SimError` |

`spinSpeed` uses the measured total spin when high-confidence, otherwise a
per-club model. The "Sent to OpenGolfSim" badges (debug mode) show which.

## Club sync

OGS 1.21.2 pushes a `player` message with the selected club whenever it
changes, and OpenFlight applies it to its club picker and carry/spin model. No
OGS patch is needed. (This is read from OGS's source, `lib/launch/index.js`;
the club `name` values OGS uses have not yet been checked against
`ogs_club_to_club` on hardware.) The `scripts/setup/opengolfsim/` patch targets
1.13.0's OpenConnect `api.js` and does not apply to 1.21.2.

> Club sync is **one-way: OGS → OpenFlight.** The Developer API accepts a
> `club` message from the device, but OpenFlight does not send one; the sim is
> the source of truth.

## Troubleshooting

- **Pill stays amber (connecting / reconnecting):** OpenFlight can't reach
  `host:port`. Check that OGS is signed in (3111 only opens after login), the IP
  is correct, 3111 isn't firewalled, and you launched with `--sim`.
- **Shots don't appear, but OGS echoes them:** OGS's launch-monitor device is
  not **Developer API**. Select it in Settings → Shot Data.
- **`SimError` "400 Unable to parse payload":** something sent a non-native
  payload, e.g. a connector typed `gspro` pointed at 3111 on a current OGS.
  Use `"type": "opengolfsim"`.
- **Shots echo but nothing flies:** OGS must be on a hittable screen (range or a
  course loaded).

## References

- [OpenGolfSim Developer API](https://help.opengolfsim.com/desktop/apis/)
- [Shot data format](https://help.opengolfsim.com/desktop/apis/shot-data/)
