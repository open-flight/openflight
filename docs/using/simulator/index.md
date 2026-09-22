# Simulator Connectors

OpenFlight can stream each shot into golf simulators in real time. Connectors
are **optional** and **additive**: you can run zero, one, or several at once
(e.g. GSPro and OpenGolfSim simultaneously). The shot-detection pipeline is
never modified — connectors are listeners that hang off the shot it already
produces.

Supported simulators:

- [GSPro](gspro.md) (OpenConnectV1)
- [OpenGolfSim](opengolfsim.md)
- [PAR-TEE](partee.md)

This document explains how the connector layer works and how to add a new
simulator. For setting up a specific simulator, see its page above.

## Quick start

1. Copy the example config and enable the simulator(s) you use:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   ```jsonc
   {
     "connectors": [
       // OpenGolfSim via its Developer API on 3111 (speaks OpenConnect V1).
       // Club sync needs the scripts/setup/opengolfsim patch; see opengolfsim.md.
       { "type": "opengolfsim", "enabled": true, "host": "127.0.0.1", "port": 3111 },
       // GSPro
       { "type": "gspro", "enabled": false, "host": "192.168.1.50", "port": 921 },
       // PAR-TEE (iPhone/iPad) on the phone's Wi-Fi address; see partee.md.
       { "type": "partee", "enabled": false, "host": "192.168.1.70", "port": 921 }
     ]
   }
   ```
   A connector's `type` is the *product*. All three ride the shared OpenConnect
   V1 codec and differ only in name and default port (GSPro 921, OpenGolfSim's
   Developer API 3111, PAR-TEE 921).
2. **Enable the feature at launch with `--sim`** (off by default). Connectors
   marked `enabled` in the file then come up:
   ```bash
   scripts/start-kiosk.sh --sim
   ```
   Without `--sim`, no connectors run regardless of the file. (Once the feature
   is broadly stable it may default on.)

The header shows a status pill per connector (green = connected, amber =
connecting/reconnecting, red = error, gray = disabled). In **debug mode**, a
per-shot "Sent to <sim>" panel shows every field that was sent with an **M**
(measured) or **E** (estimated) badge.

## How it works

```
OPS243 + optional angle radar  ──►  shot pipeline  ──►  on_shot_detected()
                                              │  (UI emit unchanged)
                                              ▼
                                       resolve_shot(shot)            ← sim/resolver.py
                                              │  ResolvedShot + provenance
                                              ▼
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                  OpenConnect V1 codec (gspro/codec.py)    (future)   ← per-sim codec
                              │               │
                        TcpSimClient    TcpSimClient                   ← sim/transport.py
                              ▼               ▼
                          GSPro :921     OpenGolfSim :3111
```

The design separates the parts that are **shared across all simulators** from
the parts that are **specific to one protocol**:

| Layer | File | Shared? |
|---|---|---|
| TCP lifecycle, reconnect/backoff, JSON framing, optional heartbeat | `sim/transport.py` | shared |
| Shot → resolved fields + measured/estimated provenance (fallback table) | `sim/resolver.py` | shared |
| Connection state, player state, inbound event types, resolved shot | `sim/types.py` | shared |
| Connector = codec + transport, and the codec registry | `sim/codec.py` | shared |
| Config (`config/sim.json` + CLI merge) | `sim/config.py` | shared |
| **Wire format**: serialize a shot, parse inbound messages, heartbeat | `gspro/codec.py`, `opengolfsim/codec.py` | **per-sim** |

The key idea: two simulators differ only in their **wire format** and their
**inbound message shapes**. Everything else — connecting, reconnecting,
filling in missing fields, tracking provenance, fanning out to multiple
targets — is written once and reused.

### The resolver and provenance

`resolve_shot()` turns a `Shot` into a `ResolvedShot` with every
simulator-relevant field populated, applying a fallback when a measurement is
missing (e.g. no measured spin → a per-club model value). Each field is tagged
`measured` or `estimated`. This logic lives in exactly one place, so every
connector is honest about what came from hardware versus a model, and the UI
renders identical badges for any simulator.

A shot is **only dropped** when ball speed is missing — every other field has
a model fallback.

### The codec contract

A codec is a small class implementing the `Codec` protocol
(`sim/transport.py`):

```python
class Codec(Protocol):
    name: str
    def build_shot(self, resolved: ResolvedShot) -> bytes: ...      # serialize a shot
    def parse_inbound(self, frame: bytes) -> list[InboundEvent]: ... # decode sim → us
    def heartbeat_bytes(self) -> Optional[bytes]: ...                # None = no keepalive
    def on_connect_bytes(self) -> Optional[bytes]: ...               # e.g. a hello frame
    def fields_for_target(self) -> list[str]: ...                    # which fields it sends
```

Inbound messages are normalized into protocol-neutral events
(`PlayerUpdate`, `ShotAck`, `SimError`) so the server handles club changes and
errors the same way regardless of simulator.

> **Club selection is one-way: simulator → OpenFlight.** Both GSPro and
> OpenGolfSim treat the sim as the source of truth for the current club and
> push it to the launch monitor; neither protocol accepts a club change *from*
> the launch monitor. OpenFlight applies the sim's club to its shot tagging and
> carry/spin model. (If a future sim's API supports setting the club from the
> device, the codec is where you'd add an outbound `build_club_change`-style
> method — no other layer changes.)

## Adding a new simulator

1. **Create a codec.** Add `src/openflight/<sim>/codec.py` implementing the
   `Codec` protocol. Map `ResolvedShot` fields to the simulator's wire format
   in `build_shot`, and translate inbound messages to `PlayerUpdate` /
   `ShotAck` / `SimError` in `parse_inbound`. Return `None` from
   `heartbeat_bytes()` if the protocol has no keepalive.
2. **Register it.** Add the type to `_codec_for()` and to `_DEFAULTS` /
   `KNOWN_TYPES` in `sim/config.py` (default host/port/units).
3. **No new CLI flag needed.** The single `--sim` gate enables all connectors
   from `config/sim.json`; just add your connector there.
4. **UI display names.** Add the target → display-name entry in
   `ui/src/components/SimStatus.tsx` and `SimShotBadges.tsx`.
5. **Tests.** Add a codec round-trip test (serialize + parse inbound) and a
   club-mapping test. The shared transport/resolver are already covered.
6. **Docs.** Add `docs/simulator/<sim>.md` and link it from this page.

You do **not** touch the transport, the resolver, the server fan-out, or the
config loader — that's the point of the abstraction.

## Session logging

Each connector logs three entry types to the session JSONL:

- `sim_send` — a shot forwarded to a simulator (target, shot number, per-field
  values + provenance)
- `sim_status` — a connection-state change (connected, reconnecting, error)
- `sim_player` — a player/club update pushed back by the simulator
