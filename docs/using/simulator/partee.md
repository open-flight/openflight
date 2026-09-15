# PAR-TEE

OpenFlight streams shots into [PAR-TEE](https://playpartee.com), a golf
simulator for iPhone and iPad, over the **OpenConnect V1** protocol. PAR-TEE
listens on the phone's Wi-Fi address (TCP **921** by default), so the `partee`
connector reuses the same shared codec as GSPro, pointed at the phone and
reported as "PAR-TEE".

See the connector architecture in [README.md](https://github.com/jewbetcha/openflight/blob/main/README.md). This page covers
setup specific to PAR-TEE.

## Requirements

- **PAR-TEE 1.2 or later** with OpenFlight chosen as the launch monitor
  (**Settings → MONITOR → DEVICE → OPENFLIGHT**). That screen shows the address
  OpenFlight should connect to (`ip:port`) and lets you change the port.
- **Same Wi-Fi.** The machine running OpenFlight (e.g. the Raspberry Pi) and
  the phone must be on the same network. The address PAR-TEE shows is the
  phone's Wi-Fi address; off Wi-Fi it reads "Join Wi-Fi to get an address".
- **PAR-TEE in the foreground.** The listener stops when the app goes to the
  background and comes back when the app returns; OpenFlight reconnects on its
  own.
- No account/credentials are sent by OpenFlight. OpenConnect V1 has no auth.

## Setup

1. In PAR-TEE, open **Settings → MONITOR** and choose **OPENFLIGHT** under **DEVICE**.
   Note the address shown (e.g. `192.168.1.70:921`).
2. **Configure OpenFlight.** Copy the example config if you haven't already:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   Enable the PAR-TEE connector with the phone's address:
   ```jsonc
   {
     "connectors": [
       { "type": "partee", "enabled": true, "host": "192.168.1.70", "port": 921 }
     ]
   }
   ```
3. **Start OpenFlight with simulator connectors on** (`--sim`, off by default):
   ```bash
   scripts/start-kiosk.sh --sim
   ```
   The header PAR-TEE pill should turn **green**, and PAR-TEE's device pill
   reports the monitor as ready after the first heartbeat.
4. **Hit a shot** with PAR-TEE on the practice range or a hole. With debug mode
   on, the "Sent to PAR-TEE" panel shows the values sent with measured/estimated
   badges.

## What gets sent

The same OpenConnect V1 payload as [GSPro](gspro.md#what-gets-sent). PAR-TEE
runs its own ball flight from the launch values:

| Field | PAR-TEE |
|---|---|
| `BallData.Speed` | required; a shot without it is refused with a `501` |
| `BallData.VLA` / `HLA` / `TotalSpin` / `SpinAxis` | used |
| `ClubData.Speed` / `Path` | used when non-zero |
| `BallData.BackSpin` / `SideSpin` / `CarryDistance` | ignored |
| `Units` | ignored; leave it at `Yards` |

PAR-TEE replies `200` to a played shot (and to a resend of an already-played
`ShotNumber`, which it does not replay), `501` to a shot with no ball speed or
a malformed frame, and nothing to a heartbeat.

## Club selection

PAR-TEE has no club picker to push, so it never sends a `201 Player` update.
Set the club in OpenFlight as usual; it drives OpenFlight's shot tagging and
the per-club spin model used when spin is not measured.

## Troubleshooting

- **Pill stays amber (connecting / reconnecting):** OpenFlight can't reach
  `host:port`. Check that `sim.json` matches the address PAR-TEE shows, that
  both are on the same Wi-Fi, and that PAR-TEE is in the foreground with
  OPENFLIGHT selected. A port changed in PAR-TEE needs the same change in
  `sim.json`. OpenFlight retries automatically with backoff (1→2→4→…→30s).
- **Shots are acknowledged but don't play:** PAR-TEE only plays a shot on a
  screen where you can hit (the practice range, or a hole with the ball at
  rest). On any other screen the shot is acknowledged and dropped. Check
  `sim_send` entries in the session log to confirm OpenFlight is sending.
- **Pill red (error):** PAR-TEE returned a `501`; hover the pill for the
  message. The connection stays up.

## References

- [PAR-TEE](https://playpartee.com)
- [GSPro OpenConnect V1 spec](https://gsprogolf.com/GSProConnectV1.html)
