# Pureplay Connect

OpenFlight can send shots to [Pureplay](https://github.com/PureplayGolf/Pureplay-Connect)
using the Pureplay Connect v1 protocol. Unlike GSPro, OpenGolfSim and PAR-TEE,
it is a **binary** protocol, so it has its own codec (`src/openflight/pureplay/`).

OpenFlight acts as the launch monitor ("device") and connects **out** to
Pureplay (the "host") over TCP, on port 9760 by default.

## Setup

1. Make sure Pureplay is running and reachable from the Pi, and note its IP
   address.
2. Add a connector to `config/sim.json`:
   ```jsonc
   { "type": "pureplay", "enabled": true, "host": "192.168.1.60", "port": 9760 }
   ```
3. Start OpenFlight with the simulator gate on:
   ```bash
   scripts/start-kiosk.sh --sim
   ```

The header pill shows connection state, as for the other simulators.

## What is sent

Each shot is sent as a `BallData` message, followed by a `ClubData` message when
any club reading exists. Speeds are converted from mph to m/s.

| Sent | Fields |
|---|---|
| `BallData` | ball speed, launch angle, horizontal launch angle, total spin, back spin, side spin, spin axis |
| `ClubData` | club head speed, club path |

Carry distance is not sent; Pureplay simulates the flight itself.

Pureplay's validity mask means a field was **actually measured**, and Pureplay
decides how strict to be with `SetStrictMode`:

- **Strict mode off (the default, for casual rounds).** Every value OpenFlight
  has is sent, estimated ones included (the **E** badge in debug mode), so
  Pureplay gets a complete shot even when spin or launch angle come from the
  model rather than the radar.
- **Strict mode on (tournaments).** Only fields OpenFlight actually measured (the
  **M** badge) are marked valid. Estimated values are cleared and Pureplay sees
  them as not measured.

OpenFlight acknowledges both requests with `StrictModeAck`. Acknowledging strict
mode means OpenFlight is following its rules: from then on, nothing estimated is
sent. The mode resets to off on every new connection, so Pureplay sets it again
after reconnecting.

## What Pureplay can control

- **Club and handedness.** Pureplay's `SetClubInfo` updates OpenFlight's current
  club and handedness, the same way GSPro does. Putters map to unknown.
- **Ignored.** `SetDetect`, `SetBallType` and `SetShotContext` are advisory;
  OpenFlight captures every shot the same way, so they have no effect.

## Link behaviour

The codec sends `Hello` on connect, answers `HelloAck` with `ReadyState`,
answers `Ping` with a heartbeat, and sends heartbeats while connected. Frames
carry a CRC32C; bad or incomplete frames are skipped so noise on the link cannot
stall later frames. On reconnect, the connector reconnects with the shared
backoff and the shot counter restarts.

## Status

Verified with automated tests against the published protocol's byte-level
vectors and a fake host over localhost. See the pull request for what has and
has not been tested against the real Pureplay application.
