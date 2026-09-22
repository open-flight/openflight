# Sim Connector Abstraction — GSPro + OpenGolfSim

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-06-13
**Status:** Plan — awaiting approval to start Phase 1
**Branch:** `feat/sim-connectors` (new, off `main`)
**Supersedes:** the stale `feat/gspro-integration` branch (unmergeable — ~40k lines diverged from main)

## Goal

Stream OpenFlight shots into golf simulators through a **protocol-agnostic connector layer**. Ship GSPro (OpenConnectV1) and OpenGolfSim as the first two connectors, with a third sim reducible to a single new `codec.py`. The shot-detection pipeline is **not** modified — connectors hang off the existing fan-out point.

## Decisions locked (2026-06-13)

1. **Salvage:** port Coleman's `src/openflight/gspro/` module + its 10 tests onto the new branch, then refactor behind the shared seam. Reuse the tested code; don't rewrite.
2. **Abstraction depth:** share **both** the TCP transport **and** the fallback/provenance resolver in a `sim/` core. Codecs only serialize + parse.
3. **Concurrency:** support **N simultaneous connectors**. `config/sim.json` lists targets; `on_shot_detected` fans out to all enabled+connected.
4. **UI events:** generic `sim_status` / `sim_shot` / `sim_player` carrying a `target` name. UI is greenfield on main (none of Coleman's GSPro UI is on main), so no migration cost.

## Protocol comparison (the reason for a codec seam)

| | GSPro OpenConnectV1 | OpenGolfSim |
|---|---|---|
| Transport | TCP :921, JSON | TCP :3111, JSON |
| Shot frame | flat `ShotPayload`, PascalCase, `DeviceID`/`APIversion`/`ShotDataOptions` | nested `{type,unit,shot:{…}}`, camelCase |
| Ball fields | Speed, HLA, VLA, CarryDistance | ballSpeed, horizontalLaunchAngle, verticalLaunchAngle |
| Spin fields | TotalSpin + BackSpin + SideSpin + SpinAxis | spinSpeed + spinAxis only |
| Carry | sent | not sent (sim computes it) |
| Heartbeat | yes, 5s | none documented |
| On connect | nothing | optional `{type:"device","status":"ready"}` |
| Inbound | code 201 `Player{Handed,Club}`; 200 ack; 5xx error | `{type:"player",…}` (club name + two-letter id); shot-result events |
| Ack codes | 200 / 201 / 5xx | none documented |

Shared across both: a TCP client (connect/reconnect/backoff/recv-loop/brace-balanced JSON framing) and the `Shot → fields` fallback+provenance logic. Different per protocol: the final serialize step and the inbound parse. That boundary is the seam.

## Target module layout

```
src/openflight/sim/
├── __init__.py        # public: SimConnector, build_connectors, load_sim_config, ConnectionState
├── transport.py       # TcpSimClient: socket lifecycle, framing, reconnect, OPTIONAL heartbeat (codec-driven)
├── resolver.py        # resolve_shot(Shot, PlayerState) -> ResolvedShot (+ provenance). The fallback table, once.
├── types.py           # ConnectionState, PlayerState, ResolvedShot, InboundEvent variants, club-map helpers
├── config.py          # load_sim_config(): config/sim.json + CLI merge -> list[ConnectorConfig]
└── codec.py           # Codec Protocol + build_connectors(configs) factory

src/openflight/gspro/
└── codec.py           # GSProCodec: ResolvedShot -> OpenConnectV1 bytes; parse 200/201/5xx; heartbeat; club map

src/openflight/opengolfsim/
└── codec.py           # OpenGolfSimCodec: ResolvedShot -> {type:shot,…}; parse player/shot; no heartbeat; device-ready; club map
```

### Core interfaces (sim/types.py + sim/codec.py)

```python
# types.py
class ConnectionState(Enum):
    DISABLED, CONNECTING, CONNECTED, RECONNECT_BACKOFF, STOPPED

@dataclass
class ResolvedShot:
    ball_speed_mph: float
    vla: float
    hla: float
    total_spin_rpm: float
    spin_axis_deg: float
    back_spin_rpm: float
    side_spin_rpm: float
    carry_yards: float
    club_speed_mph: Optional[float]   # None => no club data
    club_path_deg: float
    club: ClubType
    provenance: Dict[str, str]        # logical field ("ball_speed","vla",…) -> "measured"|"estimated"

@dataclass
class PlayerUpdate: handed: Optional[str]; club: Optional[ClubType]
@dataclass
class ShotAck:     shot_number: Optional[int]; ok: bool; message: str = ""
@dataclass
class SimError:    message: str
InboundEvent = Union[PlayerUpdate, ShotAck, SimError]

@dataclass
class PlayerState:                    # one shared instance across connectors
    handed: str = "RH"
    club: ClubType = ClubType.DRIVER
    shot_counter: int = 0
    def next_shot_number(self) -> int: ...

# codec.py
class Codec(Protocol):
    name: str                                                 # "gspro" | "opengolfsim"
    def build_shot(self, r: ResolvedShot, p: PlayerState) -> bytes: ...
    def parse_inbound(self, frame: bytes) -> list[InboundEvent]: ...
    def heartbeat_bytes(self, p: PlayerState) -> Optional[bytes]: ...   # None => no heartbeat thread
    def on_connect_bytes(self) -> Optional[bytes]: ...                  # OGS device-ready; None for GSPro
    def fields_for_target(self) -> list[str]: ...                       # which logical fields this sim emits (for UI badges)
```

`TcpSimClient` (transport.py) is Coleman's `gspro/client.py` with the GSPro-specific bits removed: it takes a `Codec`, runs the recv loop, calls `codec.parse_inbound`, dispatches each `InboundEvent` to `on_inbound`, and only spawns the heartbeat thread when `codec.heartbeat_bytes(...)` is non-None. The `_find_json_end` brace-balanced framing stays in transport (both protocols are JSON-over-TCP).

`SimConnector` = `(codec, TcpSimClient)` pair plus `send_shot(resolved)`/`is_connected()`/`start()`/`stop()`. `build_connectors(configs)` maps each `ConnectorConfig.type` to its codec class.

### Resolver (sim/resolver.py) — the shared fallback table

Lifts Coleman's `shot_builder.build()` logic but emits a protocol-neutral `ResolvedShot` keyed on **logical** field names. Reuses `_OPTIMAL_LAUNCH`, `SPIN_CONFIDENCE_HIGH` (both on main). Rules unchanged:

| Logical field | measured source | estimated fallback |
|---|---|---|
| ball_speed | `Shot.ball_speed_mph` (else **raise `IncompleteShotError`**) | — |
| vla | `launch_angle_vertical` | `_OPTIMAL_LAUNCH[club]` |
| hla | `launch_angle_horizontal` | `0.0` |
| total_spin | `spin_rpm` if `spin_confidence >= 0.7` | per-club spin table (temp; replace w/ ballistics module later) |
| spin_axis | `spin_axis_deg` | `0.0` |
| back_spin / side_spin | `total·cos(axis)` / `total·sin(axis)` | derived (estimated if either input estimated) |
| carry | `estimated_carry_yards` (already a model) | derived |
| club_speed | `club_speed_mph` | `None` (codec sets ContainsClubData=false / omits) |
| club_path | `club_path_deg` | `0.0` |

Codecs map logical→wire field names for provenance so the UI badge layer stays generic.

### Config (sim/config.py)

`config/sim.example.json` (checked in; `config/sim.json` gitignored):

```json
{
  "connectors": [
    {"type": "gspro",       "enabled": true,  "host": "127.0.0.1", "port": 921,  "device_id": "OpenFlight", "units": "Yards", "heartbeat_interval_s": 5},
    {"type": "opengolfsim", "enabled": false, "host": "127.0.0.1", "port": 3111, "units": "imperial"}
  ]
}
```

CLI (in `start-kiosk.sh` + server): `--gspro host[:port]` and `--opengolfsim host[:port]` enable+override the matching connector; `--no-sim` disables all. Precedence: `--no-sim` > per-sim flag > file > defaults. Missing file = all disabled.

### Server wiring (server.py)

- Global `sim_connectors: list[SimConnector] = []`, shared `sim_player_state = PlayerState()`.
- `main()`: `cfgs = load_sim_config(args)`; `sim_connectors = build_connectors(cfgs, on_status=…, on_inbound=…)`; `start()` each.
- [on_shot_detected()](https://github.com/jewbetcha/openflight/blob/main/src/openflight/server.py#L1488), after the existing `socketio.emit("shot", …)`:
  ```python
  try:
      resolved = resolve_shot(shot, sim_player_state)
  except IncompleteShotError as e:
      socketio.emit("sim_shot_dropped", {"reason": str(e)}); return-ish
  for c in sim_connectors:
      if c.is_connected():
          c.send_shot(resolved)                      # codec serializes per protocol
          socketio.emit("sim_shot", {"target": c.name, "payload": …, "provenance": …})
  ```
- Inbound (`on_inbound(target, event)`): `PlayerUpdate` → update shared `sim_player_state` + `monitor.set_club(...)` + emit `sim_player {target,…}` + `club_changed`. `SimError` → emit `sim_status` error. `ShotAck` → session log.
- Status callback → `sim_status {target, state, host, port, attempt, next_retry_in_s, message}` + session log.
- Shutdown handler stops every connector.
- Session logger: generic `log_sim_send/status/player` carrying `target` (replaces Coleman's `log_gspro_*`).

### UI (greenfield on main)

- One reusable `<SimStatusPill target=… state=… />`, rendered once per connector in the status bar. Colors: green=connected, amber=reconnecting/error, gray=disabled. Driven by `sim_status` events keyed by `target`.
- Per-shot "Sent to sim" section with `M`/`E` provenance badges, driven by `sim_shot` (provenance is logical-field based, so the badge component is sim-agnostic; `fields_for_target` decides which rows show).

## Phases (tracer-bullet vertical slices — each ends green)

- **Phase 1 — Port + extract core, prove with GSPro.** Branch off main. `git checkout origin/feat/gspro-integration -- src/openflight/gspro tests/test_gspro_*.py`. Extract `sim/transport.py` + `sim/resolver.py` + `sim/types.py` from `gspro/client.py` + `shot_builder.py` + `state.py`; refactor GSPro into `gspro/codec.py` against the core. **Acceptance:** all ported GSPro tests pass with identical wire output; the seam exists and carries one connector. *(This is the tracer bullet — transport→resolver→codec works end-to-end before a second protocol exists.)*
- **Phase 2 — Server registry + generic events.** Replace any single-client wiring with `list[SimConnector]`; `config/sim.json` list; generic `sim_*` WS events + `log_sim_*`. **Acceptance:** GSPro still works, now via the registry; `server` wiring test covers fan-out to ≥1 connector.
- **Phase 3 — OpenGolfSim codec.** Add `opengolfsim/codec.py` (envelope shape, camelCase, no heartbeat, device-ready on connect, two-letter club map, inbound player parse), config type, `--opengolfsim` flag, tests. **Acceptance:** mock-TCP integration test confirms a shot reaches an OGS mock with correct JSON; both connectors can run concurrently.
- **Phase 4 — UI.** Generic status pills + provenance badges from `sim_*`. **Acceptance:** component tests; manual check both pills render.
- **Phase 5 — Docs + setup.** `docs/sim-integration.md` (per-sim setup + manual hardware test), `config/sim.example.json`, raspberry-pi-setup mention, start-kiosk flags.

## Tests

- **Reuse:** Coleman's `test_gspro_messages` / `test_gspro_shot_builder` / `test_gspro_state` / framing / heartbeat / lifecycle tests, re-pointed at the new layout.
- **New:** resolver fallback-table tests (every rule + provenance dict + `IncompleteShotError`); transport tests against a shared mock-TCP-server harness (reconnect/backoff, brace framing across packet boundaries, heartbeat present-vs-absent, on-connect bytes); GSPro codec round-trip; OpenGolfSim codec round-trip + inbound player parse; server registry fan-out (2 connectors, one disconnected); config merge (list + CLI precedence, `--no-sim`).

## Open dependencies / deferred

- Per-club spin table stays inline in the resolver (marked temporary), to be swapped for the ballistics spin model when that lands — same as Coleman's note.
- OpenGolfSim has no documented ack codes or heartbeat; `ShotAck` parsing for OGS is best-effort and `heartbeat_bytes` returns None. Revisit if their API gains them.
- Putting (`PT` / OGS equivalent) out of scope; logged + mapped to `UNKNOWN`.
- Settings-panel UI deferred (config-file only in v1), matching Coleman's original scope.
