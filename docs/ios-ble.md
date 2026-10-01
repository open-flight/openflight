# Phone app connection (Bluetooth LE and network)

> **BLE blocker — check the Raspberry Pi kernel first:** Raspberry Pi kernel
> `6.18.34+rpt-rpi-2712` has a confirmed regression that rejects every BLE
> advertisement. Run `uname -r` on the Pi. If it reports that version, use the
> network transport or boot a working kernel such as 6.12.x; there is no userspace
> workaround. See the [full diagnosis](#known-bad-raspberry-pi-kernel-61834rpt-rpi-2712).

OpenFlight sends each completed shot from a Raspberry Pi to a phone app over one
of two local transports. Both carry the identical versioned payload described
below, so an app behaves the same either way. Two apps speak this protocol:

- jake-fishtech's SwiftUI app, on the
  [`feat/iOS-ble` branch of his fork](https://github.com/jake-fishtech/openflight/tree/feat/iOS-ble/ios)
  (schema version 1).
- The Kotlin Multiplatform companion for Android and iOS,
  [`btripp/openflight-mobile-kmp`](https://github.com/btripp/openflight-mobile-kmp)
  (schema version 1, and [schema v2](#schema-v2) where the Pi offers it).

| Transport | Pi setup | Use it when |
|---|---|---|
| **Bluetooth** | start with `--ble` | The phone cannot reach the Pi over a network |
| **Network** | always on | The phone can reach the Pi over IP (Wi-Fi, Ethernet or any other link), or Bluetooth advertising is unavailable |

The network transport needs no flag: it streams from the same HTTP server that serves the
browser UI, and exposes nothing the browser UI does not already broadcast.

## Requirements

- Raspberry Pi running Raspberry Pi OS (working Bluetooth only for the BLE transport)
- iPhone running iOS 17 or newer
- Mac with Xcode 16 or newer to build the app
- The normal OpenFlight radar setup

The Pi uses [Bless](https://github.com/kevincar/bless) to expose a small GATT server
through BlueZ.

## Run the Pi

The interactive setup script installs the optional BLE dependency on new
installations:

```bash
./scripts/setup/setup.sh
```

For an existing checkout, install it, configure BlueZ once so iOS does not keep
prompting to pair (see [Troubleshooting](#troubleshooting)), and start
OpenFlight with BLE enabled:

```bash
uv sync --extra ble
./scripts/setup/configure_bluetooth.sh
scripts/start-kiosk.sh --ble
```

BLE startup and delivery errors are isolated from shot recording. If Bluetooth
is unavailable, the browser UI and session logger continue to work.

## Network transport

The server streams shots as [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events)
at `/api/shots/stream`. Check it from any machine on the network before
involving a phone:

```bash
curl -N http://raspberrypi.local:8080/api/shots/stream
```

A connection opens with a `: ping` comment, replays the most recent shot if
there is one, then emits one `event: shot` message per shot with a heartbeat
every 15 seconds while idle:

```text
: ping

event: shot
data: {"ball_speed_mph":151.4,"club":"driver",...,"schema_version":1}
```

In the app, pick **Wi-Fi** (the app's label; it works over any IP network, so
the Pi can be on Ethernet) and enter the Pi's address. `raspberrypi.local:8080`
is the default and works on a stock Raspberry Pi OS install, which publishes its
hostname over mDNS; if you renamed the Pi, use `<hostname>.local:8080` or its IP.
The port defaults to 8080 when you leave it off. The app reconnects on its own
with backoff, and iOS asks once for permission to talk to devices on the local
network.

The server accepts up to eight simultaneous stream clients and answers `503`
beyond that, so a forgotten `curl` cannot crowd out a phone.

## Build and run the iOS app

The SwiftUI app is not part of this repository. For complete Xcode, signing,
physical-device, simulator, testing, and troubleshooting instructions, see
[`ios/README.md` in jake-fishtech's fork](https://github.com/jake-fishtech/openflight/blob/feat/iOS-ble/ios/README.md).

1. In a checkout of that branch, open `ios/OpenFlight.xcodeproj` in Xcode.
2. Select the `OpenFlight` target, choose your development team, and use a
   unique bundle identifier if Xcode requests one.
3. Connect an iPhone, select it as the run destination, and press Run.
4. Accept the Bluetooth permission prompt.
5. Start OpenFlight on the Pi, adding `--ble` if you want the Bluetooth
   transport.

Over Bluetooth the app scans only for the OpenFlight service, connects
automatically, and subscribes to shot and control notifications. Over the network it
opens the shot stream and keeps it open. Either way, hit a shot and its metrics
should replace the empty dashboard. The most recent shot is replayed when a
phone connects, so a newly connected phone does not have to wait for another
shot.

## Select the club from the iPhone

Use **Club for next shot** on the dashboard to select any supported wood,
hybrid, iron, or wedge. OpenFlight applies the club to subsequent shots and
confirms the change before the app updates its saved selection. The app sends
the change over the currently selected transport:

- Bluetooth uses the framed control characteristic described below.
- The network transport sends `POST /api/club` with `{"club":"7-iron"}`.

The browser UI and simulator integrations use the same server operation, so a
phone club change affects the same launch, spin, and carry processing state.

> The iOS Simulator can run the automated tests, but CoreBluetooth does not
> provide a useful end-to-end BLE hardware test there. Use a physical iPhone
> and Raspberry Pi for manual connection testing.

## Calibrate TI radar tilt with the iPhone

The dashboard's **Calibrate TI Radar** button opens a guided mount-angle tool.
It sends the measurement over whichever transport is selected on the dashboard.

1. Start OpenFlight with the IWR6843 enabled. Add `--ble` for Bluetooth, or
   make sure the phone can reach the Pi over the network.
2. Remove the phone case. Hold the phone upright in portrait with its back flat
   against a straight reference surface parallel to the TI antenna face. Keep
   the screen facing the target and avoid resting on the camera bump.
3. Keep the radar and phone still while the app averages 120 gravity samples
   over about two seconds.
4. Confirm left/right roll is within 3 degrees, then tap **Apply Calibration**.

While the phone is moving, the angle cards are labeled **Live sensor reading**
and show the latest Core Motion gravity angles without the two-second averaging
lag. Once the sample window passes the stability and roll checks, the cards turn
green and switch to the **Stable 2-second average** that will actually be sent
to OpenFlight.

The app sends the averaged gravity vector, calculated mount tilt and roll,
sample count, and stability statistics through the BLE control characteristic
or `POST /api/calibration/iwr6843/orientation`. Both paths call the same server
operation. The Pi independently recomputes the angles from gravity and rejects
inconsistent or unstable measurements. If the optional enclosure LIS3DH is
active, OpenFlight subtracts its current calibrated enclosure pitch so the
saved value remains the TI antenna's angle relative to the enclosure. Otherwise,
the measured phone tilt is used directly.

The applied value takes effect immediately and is saved at
`~/.config/openflight/iwr6843_phone_orientation.json`. It is restored on startup
unless an explicit `--iwr6843-tilt-deg` value is supplied, which always wins.
Session logs record the change with source `ios_companion`.

This process measures pitch and verifies roll. It deliberately does not change
`--iwr6843-azimuth-offset-deg`: an accelerometer cannot establish yaw relative
to the target line, and phone compass readings near radar electronics are not a
precision substitute for target-line alignment.

## Wire protocol

Both transports carry the same JSON event. Only the framing differs: the network transport sends
it whole in one SSE `data:` line, while BLE splits it across notifications.

Over BLE, OpenFlight advertises one service with a shot notification and a
bidirectional control characteristic:

| Attribute | UUID |
|---|---|
| Shot service | `B6F633F2-E6E3-45AE-84B4-968ECCA2D9C7` |
| Shot notification | `2B28F67E-9011-41D2-98ED-562B47D7A5E4` |
| Control write + notification | `7E3B5D6C-7F10-4D4A-9C39-25E2B77F4A11` |

Each event is compact UTF-8 JSON with `schema_version: 1`. Optional
measurements are present as `null` when the active hardware could not produce
them. The version-one fields are:

```text
schema_version, event_id, timestamp, club, ball_speed_mph,
club_speed_mph, smash_factor, estimated_carry_yards,
launch_angle_vertical, launch_angle_horizontal, spin_rpm,
club_path_deg, spin_axis_deg
```

Over BLE the JSON is split into conservative 20-byte notifications. Every notification
has a five-byte, big-endian header followed by up to 15 payload bytes:

| Byte(s) | Meaning |
|---|---|
| 0 | Frame version (`1`) |
| 1–2 | Unsigned 16-bit message sequence |
| 3 | Zero-based fragment index |
| 4 | Total fragment count |
| 5–19 | JSON payload fragment |

Consumers should group frames by sequence, ignore duplicate fragment indexes,
order fragments by index, and decode only after all fragments arrive. The
shared contract fixture is `tests/fixtures/shot_v1.json` in this repository;
the Python tests and the app tests decode that file. Framed byte-level goldens
for every message type live in `tests/fixtures/ble_goldens/` (see
[Testing without hardware](#testing-without-hardware)).

Control writes and responses use the same framing. A command contains
`schema_version`, a unique `request_id`, a `type`, and a JSON `payload`. The Pi
notifies a response with the matching `request_id`, `ok`, and either `result` or
`error`:

```json
{"payload":{"club":"7-iron"},"request_id":"9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d","schema_version":1,"type":"set_club"}
{"ok":true,"request_id":"9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d","result":{"club":"7-iron","status":"applied"},"schema_version":1}
```

Responses and unsolicited events share the control characteristic, so match
responses by `request_id` and treat messages with a `type` as events. Version
one supports these commands:

| Command | Payload | Result |
|---|---|---|
| `set_club` | `{"club":"7-iron"}` | `{"status":"applied","club":"7-iron"}` |
| `get_club` | `{}` | `{"status":"current","club":"7-iron"}`: the Pi-owned club, unchanged |
| `iwr6843_orientation_calibration` | phone gravity measurement | `status, persistent, measured_mount_tilt_deg, enclosure_pitch_deg, configured_iwr_tilt_deg, roll_deg, azimuth_offset_deg` |
| `hello` | `{"client_schema_max":2}` | negotiation, see [schema v2](#schema-v2) |

Every club change, from any client, is notified on the control characteristic
as `{"club":"7-iron","schema_version":1,"type":"club_changed"}`. Unknown
commands fail with `"error":"Unsupported phone command: <type>"`.

## Schema v2

Schema v2 adds what the browser UI already gets over Socket.IO: profiles, shot
numbering, the shot-processing state, battery status, and provisional shots
that are replaced by their final version. Version one stays exactly as it was,
byte for byte, so the version-one iOS app (whose decoder rejects any
`schema_version` other than `1`) keeps working next to a v2 phone.

### Schema v2 design decision

*Status: accepted for review, 2026-09-25.*

**Question.** Can the Pi send v2 notifications only to centrals that negotiated
v2, on the existing characteristics?

**Finding.** No, not with Bless 0.3.0 on BlueZ, and not with BlueZ's GATT D-Bus
API at all:

- A notification is a write of the characteristic's `Value` property
  (`BlessServerBlueZDBus.update_value` sets `gatt.Value`, which emits
  `PropertiesChanged`). `bluetoothd` then notifies **every** central whose CCCD
  is enabled on that characteristic. The D-Bus API has no per-device notify.
- Bless drops the `options` argument of `WriteValue`, which is where BlueZ puts
  the writing device's object path, so the server cannot even tell which
  central sent `hello`.
- `StartNotify`/`StopNotify` carry no device either. BlueZ calls them once per
  characteristic (first subscriber in, last subscriber out).

CoreBluetooth could target centrals (`updateValue:forCharacteristic:onSubscribedCentrals:`),
but Bless passes `nil` (all centrals), and macOS is not a deployment target.

**Decision.** Schema v2 gets its own shot and control characteristics in the
same service. A v1 central never subscribes to them, so BlueZ never delivers a
v2 frame to it, and the v1 characteristics carry exactly the v1 traffic they
always did. `hello` works on both control characteristics, so a client can
negotiate before it commits to a pair, and an older Pi answers it with
`Unsupported phone command: hello`.

**Consequences.**

- One GATT service now has four characteristics. Discovery of the v2 pair is
  itself a capability signal.
- A final shot is sent twice over the air when a v1 and a v2 phone are
  connected at once (once per pair). The Pi's radio time is not the bottleneck
  at golf-shot rates.
- A v2 phone that subscribes to both pairs gets both copies; clients subscribe
  to one pair only.
- Per-characteristic subscription state now drives delivery. The publisher
  reads Bless's `app.subscribed_characteristics` after each `StartNotify` /
  `StopNotify`, so v2 frames are only pushed while a v2 central is subscribed
  and a v2 phone unsubscribing no longer stops v1 delivery.

**Rejected.**

- *Per-central state on shared characteristics:* impossible here (above).
- *A v2 flag inside v1 messages:* the v1 decoder rejects unknown
  `schema_version` values, and new fields would still reach v1 phones.
- *A second GATT service:* works, but an extra service UUID in the
  advertisement costs scarce advertising bytes and buys nothing over two more
  characteristics.

### Characteristics

| Attribute | UUID | Properties |
|---|---|---|
| Shot notification, v2 | `ED365FE6-3ABF-4FC3-8E44-D9525A22DABD` | notify |
| Control, v2 | `7BA96E63-12C2-4CE0-BB84-3513C7FD1474` | write with response, notify |

Both live in the same service, `B6F633F2-E6E3-45AE-84B4-968ECCA2D9C7`, and use
the same 20-byte framing as version one. Sequence numbers are counted
separately per characteristic.

**Encoding.** v2 messages are compact JSON with sorted keys, like version one,
but text is **UTF-8** instead of `\uXXXX` escapes (that is what lets twelve
40-character profile names fit in one message). A fragment boundary can split a
multi-byte character, so decode UTF-8 only after reassembling the whole
message.

### Negotiation

1. Discover the service. If the v2 characteristics are missing, the Pi is
   version one: use the v1 pair.
2. Subscribe to the v2 control characteristic and write `hello`. Add
   `last_event_id`, the `event_id` of the newest shot the app already has, to
   [catch up](#catch-up-after-a-reconnect) on shots missed while disconnected;
   leave it out on a first connection:
   ```json
   {"payload":{"client_schema_max":2,"last_event_id":"05dd37ec-49ed-596b-b1a4-953d54e4f239"},"request_id":"<uuid>","schema_version":2,"type":"hello"}
   ```
   The result names the negotiated schema, the features and the v2 pair:
   ```json
   {"ok":true,"request_id":"<uuid>","result":{"characteristics":{"control":"7BA96E63-12C2-4CE0-BB84-3513C7FD1474","shot":"ED365FE6-3ABF-4FC3-8E44-D9525A22DABD"},"features":["provisional_shots","shot_processing","profiles","power_status","shot_deleted","club","shot_catch_up"],"schema_version":2},"schema_version":2}
   ```
3. Subscribe to the v2 shot characteristic. The catch-up shots arrive. A client
   that skipped `hello` gets only the latest v2 shot, as before catch-up
   existed.
4. Ask for state: `get_club`, `get_profiles` and, if wanted, `get_power_status`.

`hello` also works on the v1 control characteristic, in a v1 envelope
(`"schema_version":1`); the response envelope is then v1 while the `result` is
the same. An older Pi answers `ok:false` with
`Unsupported phone command: hello`. Treat that, or no answer within 10 seconds,
as version one. `client_schema_max: 1` returns `{"schema_version":1,"features":[]}`.
The v2 control characteristic accepts envelopes with `schema_version` 1 or 2
and always answers with `schema_version: 2`.

### Catch-up after a reconnect

A phone that leaves the app, walks out of range or loses the network misses the
shots taken meanwhile. Schema v2 catches it up on reconnect, with one rule for
both transports:

| | BLE | Network |
|---|---|---|
| Name the newest shot you have | `last_event_id` in the `hello` payload | `Last-Event-ID` request header, or `?last_event_id=` (the header wins) |
| Catch-up arrives | On the v2 shot characteristic, after the `hello` response; held until the phone subscribes to it | Seeded after the state events, before live events |
| Without catch-up | A client that skips `hello` gets the latest v2 shot | — (every v2 connection gets catch-up) |

- The Pi sends the named shot again, then every current-session shot after
  it, oldest first. Resending the named shot means a phone that only had its
  provisional version (it disconnected before the final arrived) ends up with
  the final.
- No `last_event_id`, or one the session no longer holds (the session was
  cleared, that shot was deleted, or the Pi restarted), means the whole
  current session.
- At most the 20 most recent shots are sent. Use `shot_number` gaps to tell
  that older shots were not synced (deleted shots also leave gaps).
- Replayed shots are the exact bytes last sent live, including `final` and
  `enrichment`. A shot no longer cached is rebuilt as a final shot with
  `enrichment: null`.
- Cleared and deleted shots are never replayed: the Pi's session decides what
  exists.
- Upsert by `event_id` as for live shots; replays of shots the app already has
  are harmless. Invalid `last_event_id` values are treated as absent and never
  fail `hello`.
- Every network v2 `shot` frame carries `id: <event_id>`, so an `EventSource`
  resends `Last-Event-ID` on its own when it reconnects. Other events carry no
  `id`, which leaves the last shot id in place. Version-one streams and the v1
  characteristics have no catch-up: their `event_id` is not stable.
- Over BLE a notification reaches every subscribed phone, so another connected
  phone receives the catch-up too and upserts it.
- A shot taken while a BLE catch-up is still being sent can push the oldest
  queued catch-up shot out of the eight-message delivery queue. The phone then
  lacks that one shot until its session is replayed in full (for example after
  it reconnects without `last_event_id`).

The Pi advertises support with the `shot_catch_up` feature in the `hello`
result. An older Pi ignores `last_event_id` and only replays the latest shot.

### v2 shot

Sent on the v2 shot characteristic. It carries every version-one field, plus:

| Field | Type | Meaning |
|---|---|---|
| `type` | `"shot"` | |
| `final` | bool | `false`: OPS-only provisional shot, sent while optional hardware (IWR6843, camera) is still working. `true`: the final shot |
| `event_id` | UUID string | Stable per shot: the provisional and final versions of one shot share it. **Upsert by `event_id`** |
| `shot_number` | int or null | Per-monitor-run sequence; not reused after a delete |
| `profile_id`, `profile_name` | string or null | Profile the shot was attributed to at detection |
| `carry_range` | `[low, high]` or null | Carry range in yards |
| `spin_source` | string or null | Where `spin_rpm` came from |
| `launch_angle_confidence` | number or null | 0–1 |
| `enrichment` | object or null | `{"status":"pending"}` on a provisional shot; `{"status":"complete"}` or `{"status":"skipped","reason":"deadline"|"capacity"|"queue_full"|"worker_unavailable"}` on a final shot that had a provisional; `null` when the shot never waited for optional hardware |

Every key is always present; unknown values are `null`. A shot with no optional
hardware configured is sent once, final. The provisional shot is not sent at all
to v1 phones, which only ever receive final shots. The contract fixture is
`tests/fixtures/shot_v2.json`, built from a real mock shot:

```json
{"ball_speed_mph":106.1,"carry_range":[144,160],"club":"7-iron","club_path_deg":2.5,"club_speed_mph":83.5,"enrichment":{"status":"complete"},"estimated_carry_yards":152,"event_id":"05dd37ec-49ed-596b-b1a4-953d54e4f239","final":true,"launch_angle_confidence":0.6,"launch_angle_horizontal":-0.7,"launch_angle_vertical":21.2,"profile_id":"0f8e4b2a9c7d4e1f8a6b3c5d7e9f1a2b","profile_name":"Zoë","schema_version":2,"shot_number":7,"smash_factor":1.27,"spin_axis_deg":-1.6,"spin_rpm":6482,"spin_source":null,"timestamp":"2026-09-25T14:03:07.412345","type":"shot"}
```

### v2 events

Notified on the v2 control characteristic. Each has `schema_version: 2` and a
`type`, and never a `request_id`:

| `type` | Fields | When |
|---|---|---|
| `club_changed` | `club` | Any club change (kiosk, phone, simulator) |
| `profiles` | `profiles: [{id, name}]`, `active_profile_id` | After every profile request or mutation from any client, including rejected ones |
| `session_cleared` | `profile_id` | After a profile's shots are cleared (kiosk or network clients) |
| `shot_deleted` | `timestamp` (the shot's delete key) | After a shot is deleted (kiosk or network clients) |
| `shot_processing` | `state`: `capturing`, `calculating` or `failed` | Rolling-buffer monitor progress; the next shot ends it |
| `power_status` | the Socket.IO `power_status` payload: `available, provider, state, battery_percent, battery_voltage_v, external_power, updated_at, error` | Every 5 s with `--battery geekworm` |

```json
{"club":"7-iron","schema_version":2,"type":"club_changed"}
{"active_profile_id":"0f8e…","profiles":[{"id":"0f8e…","name":"Zoë ⛳"},{"id":"7c1d…","name":"Sam"}],"schema_version":2,"type":"profiles"}
{"profile_id":"0f8e…","schema_version":2,"type":"session_cleared"}
{"schema_version":2,"timestamp":"2026-09-25T14:03:07.412345","type":"shot_deleted"}
{"schema_version":2,"state":"calculating","type":"shot_processing"}
{"available":true,"battery_percent":76.5,"battery_voltage_v":3.98,"error":null,"external_power":false,"provider":"geekworm","schema_version":2,"state":"on_battery","type":"power_status","updated_at":"2026-09-25T14:03:05.000000+00:00"}
```

Profiles over BLE carry only `id` and `name`. `created_at` and the open-ended
`settings` stay on Socket.IO, because the phone only selects profiles here and
an unbounded `settings` object could not be guaranteed to fit in one message.

### v2 commands

Each v2 command calls the same server function as its Socket.IO counterpart, so
the kiosk and every other client see the same broadcasts.

| Command | Payload | Result | Also broadcasts |
|---|---|---|---|
| `hello` | `{"client_schema_max":2}` | see [Negotiation](#negotiation) | |
| `get_club` | `{}` | `{"status":"current","club":…}` | |
| `set_club` | `{"club":"7-iron"}` | `{"status":"applied","club":…}` | `club_changed` |
| `iwr6843_orientation_calibration` | as version one | as version one | |
| `get_profiles` | `{}` | `{"status":"sent"}` | `profiles`: the roster arrives as the event, not in the result |
| `set_active_profile` | `{"profile_id":…}` | `{"status":"applied","active_profile_id":…}`, or `ok:false` `Unknown profile` | `profiles` (also when rejected) |
| `get_power_status` | `{}` | the `power_status` payload, or `ok:false` `Battery monitoring is not enabled` / `No battery reading yet` | |

Over BLE, v2 is read-and-select only (see [Security](#security-and-scope)).
Adding, renaming and removing profiles, `clear_session` and `delete_shot` stay
on Socket.IO and the kiosk; sent over BLE they fail with
`Unsupported phone command: <type>` on either control characteristic. Phones
still learn about those changes from the `profiles`, `session_cleared` and
`shot_deleted` events. An event
triggered by a command is normally notified before the command's response, but
clients must accept either order. The Pi processes commands as they arrive and
enforces no busy state or timeout of its own; clients own their timeouts.
Version-one commands keep working on the v1 control characteristic, and v2-only
commands sent there fail with `Unsupported phone command`.

### Network: `?schema=2`

`GET /api/shots/stream?schema=2` opts a Server-Sent Events client into schema
v2. The default (no parameter, or `schema=1`) is the unchanged version-one
stream; any other value returns `400`. A v2 stream opens with `: ping`, then
the current state as `club_changed`, `profiles` and (with a battery monitor)
`power_status`, then the [catch-up](#catch-up-after-a-reconnect) shots
(the whole session unless `Last-Event-ID` names a shot). Each `shot` frame
carries `id: <event_id>`. Event names match the `type` of the
payload: `shot`, `shot_processing`, `profiles`, `power_status`,
`session_cleared`, `shot_deleted` and `club_changed`. Commands stay on `/api/club`, the
calibration route and Socket.IO.

```bash
curl -N 'http://raspberrypi.local:8080/api/shots/stream?schema=2'
```

### Size budget

A BLE message is at most 255 fragments × 15 bytes = 3,825 bytes. Tests encode
a worst-case v2 shot (longest float representations everywhere, a 40-character
profile name of six-byte escapes) and a `profiles` event with twelve such names
(3,610 bytes) and require both to fit. The publisher refuses, and logs, any
message that would not fit instead of sending a truncated one.

## Delivery behavior

- Shot processing never waits for either transport, and a failure in one cannot
  affect the other, the browser UI, or session logging.
- Each connected client gets a bounded queue of eight unsent events; the oldest
  queued event is dropped if that client cannot keep up. One stalled phone
  cannot slow down another.
- Disconnecting clears that client's queue. On the next connection a v2 client
  is [caught up](#catch-up-after-a-reconnect) on the session shots it missed
  (up to 20); a version-one client gets the latest completed shot replayed.
- The iOS app ignores a replayed event when its `event_id` is already visible.
  v2 clients upsert by `event_id`, which also merges a provisional shot with
  its final version.

## Security and scope

Version one intentionally has no application authentication or encryption layer
on either transport. Enable BLE only where nearby Bluetooth devices receiving
shots and issuing club or calibration commands is acceptable, and treat the
network API as accessible to anything on the same network — the same assumption
the browser UI already makes. Phone-assisted calibration can update and persist
TI mount tilt, so use either transport only in a trusted environment.

BLE is unauthenticated: any nearby device can connect and write the control
characteristics. Schema v2 therefore exposes only reading state and selecting
(club, active profile) over Bluetooth, plus the calibration version one already
had. Actions that delete data, clearing a session or deleting a shot, and
profile add, rename and remove require the network (the kiosk or Socket.IO), where
they have the same exposure the browser UI already has. Revisit this only with
authenticated pairing.

## Testing without hardware

Everything above the radio is covered by the normal test suite, with no Pi,
Bluetooth adapter or `bless` install:

- `tests/ble_harness.py` runs the real `BleShotPublisher` on its own thread and
  event loop against a fake Bless server that behaves like Bless 0.3.0 on
  BlueZ (subscription hooks called before `app.subscribed_characteristics`
  changes, notifications delivered only to centrals subscribed to that
  characteristic, writes through `write_request_func`). `VirtualCentral`s play
  the phones: they subscribe, write framed commands and reassemble
  notifications with the real reassembler.
- `tests/test_ble_loopback.py` uses it end to end against the real server
  dispatch: latest-shot replay, `hello` on both control characteristics, a
  provisional-then-final shot (v2 phone gets both with one `event_id`, a v1
  phone next to it gets only the v1 final shot), club and profile commands,
  the calibration `409` path, unknown commands and pair-by-pair unsubscribe.
- `tests/fixtures/ble_goldens/*.json` hold framed hex for every message type.
  `server_to_client` files are generated by
  `uv run python scripts/ble/generate_goldens.py` and checked by
  `tests/test_ble_goldens.py`; client test suites decode them.
  `client_to_server` files are the reverse: a client commits the frames its
  own encoder produces (`name`, `characteristic`, `sequence`, `message`,
  `payload_hex`, `frames_hex`, and an `expect` block with `ok`,
  `schema_version` and expected `result` fields), and the Python tests
  reassemble them, dispatch them through the loopback server and check the
  answer.

```bash
uv run pytest tests/test_ble_protocol.py tests/test_ble_protocol_v2.py \
  tests/test_ble_publisher.py tests/test_ble_loopback.py tests/test_ble_goldens.py \
  tests/test_shot_stream.py tests/test_phone_transport_server.py \
  tests/test_phone_transport_v2.py tests/test_control_commands.py \
  tests/test_phone_catch_up.py tests/test_ble_catch_up.py \
  tests/test_shot_stream_catch_up.py -v
uv run python scripts/ble/generate_goldens.py --check
```

What still needs a Pi and phones: BlueZ advertising, discovery and
connection from iOS and Android, pairing and permission prompts, fragment
pacing over a real link, reconnects after a Pi restart, background behaviour,
and coexistence with SSE and Socket.IO clients.

### Simulating hardware on a Pi without it

Mock mode can produce every phone event, so a Pi with no radar, UPS or camera
can still exercise the app end to end over the real radio:

```bash
scripts/start-kiosk.sh --mock --ble --battery mock --mock-enrichment-ms 1500
```

| What | How | The phone sees |
|---|---|---|
| Shots | Tap **Simulate shot** on the kiosk, or emit `simulate_shot` over Socket.IO | `shot_processing` `capturing` then `calculating`, then the shot |
| A failed capture | Emit `simulate_shot` with `{"fail": true}` | `shot_processing` `capturing`, `calculating`, `failed`, and no shot |
| Provisional then final | `--mock-enrichment-ms MS` | A provisional shot (`enrichment: pending`, no horizontal launch, club path or spin axis), then the final one with them, sharing one `event_id` |
| Skipped enrichment | `--mock-enrichment-ms` above the 20 s deadline, e.g. `25000` | The final shot with `enrichment: {"status":"skipped","reason":"deadline"}` |
| Battery | `--battery mock` | `power_status` cycling from 100% on battery through `low` and `critical`, then `plugged_in` back to 100%, about every 100 s; `get_power_status` answers with the latest reading |

Without `--battery`, `get_power_status` fails with `Battery monitoring is not
enabled`. Without `--iwr6843`, the calibration command fails with `409` `TI
IWR6843 radar is not enabled`; both are the expected error paths for a phone to
show.

To fire shots from another machine without the kiosk:

```bash
ssh <pi> 'cd /tmp && ~/.local/bin/uv run -q --no-project --with "python-socketio[client]" python -c "
import socketio, time
sio = socketio.Client(); sio.connect(\"http://localhost:8080\")
sio.emit(\"simulate_shot\")                  # or: sio.emit(\"simulate_shot\", {\"fail\": True})
time.sleep(1); sio.disconnect()"'
```

## Troubleshooting

**The network transport will not connect.**

- Confirm the address with `curl -N http://<host>:8080/api/shots/stream` from a
  computer on the same network. If curl works and the app does not, the problem
  is on the phone, not the Pi.
- If `<hostname>.local` does not resolve, try the Pi's IP address; some networks
  block mDNS.
- Accept the iOS local network permission prompt. Deny it once and the app
  cannot reach the Pi until you re-enable it in Settings, Privacy & Security,
  Local Network.
- Keep the phone on the same network as the Pi; this transport does not traverse
  routers or VPNs.

**The app stays on “Looking for OpenFlight.”**

- Confirm OpenFlight was started with `--ble`.
- Run `bluetoothctl show` on the Pi and confirm `Powered: yes`.
- Keep the app in the foreground for the initial connection.
- Restart OpenFlight after changing the Pi Bluetooth configuration.

**The iPhone shows a pairing prompt every 30 seconds.**

Symptom: the phone connects, then disconnects about every 33 seconds and iOS
asks to pair again. The OpenFlight log shows `[BLE] Client subscribed (schema
v2)` followed by `unsubscribed` exactly 30 seconds later, and `bluetoothctl info
<phone>` shows `Paired: no`.

OpenFlight never asks for pairing. BlueZ does: by default `bluetoothd` also acts
as a GATT client and reads the phone's own GATT database. The iPhone answers
`Insufficient Authentication`, BlueZ sends an SMP Security Request (the iOS
prompt), and nothing on a headless or kiosk Pi confirms the pairing. After the
30-second SMP timeout BlueZ disconnects with `Authentication Failure (0x05)`,
the phone reconnects, and the loop repeats.

Turn off BlueZ's GATT client role (OpenFlight only needs to be a peripheral):

```bash
./scripts/setup/configure_bluetooth.sh          # or --check to only report
```

The script backs up `/etc/bluetooth/main.conf`, sets `Client = false` under
`[GATT]`, and restarts bluetooth. Restart OpenFlight afterwards so its BLE
server re-registers, and on the iPhone tap Forget This Device if iOS remembered
a half-finished pairing. `setup.sh` offers this step on a Pi.

Set the key under `[GATT]`. Stock `main.conf` on Raspberry Pi OS lists the
commented `#Client = true` under `[CSIS]`, where `bluetoothd` ignores it.

To confirm the fix, capture with `sudo btmon` while the phone connects: there
should be no `SMP: Security Request` and no `Disconnect … Authentication
Failure`, and the subscription should stay up past 30 seconds.

**The Pi logs `DBusError: Failed to register advertisement`.**

BlueZ returns that one message for every advertising failure, so check what
bluetoothd actually rejected:

```bash
journalctl -u bluetooth -n 20 --no-pager
```

`Failed to add advertisement: Invalid Parameters (0x0d)` means the kernel
refused the advertisement. Register the advertisement one property group at a
time to find out which part it objects to:

```bash
uv run python scripts/hardware-test/test_ble_advertise.py
```

The probe prints which layer is implicated. For the parameter-level detail,
capture the management interface while it runs:

```bash
sudo btmon -w /tmp/ble-adv.btsnoop
```

### Known bad: Raspberry Pi kernel 6.18.34+rpt-rpi-2712

On this kernel every advertisement is rejected, including one carrying no data
at all. `Add Extended Advertising Parameters (0x0054)` succeeds and reports 31
bytes available for both advertising and scan response data, and then `Add
Extended Advertising Data (0x0055)` fails with `Invalid Parameters (0x0d)` for
a zero-byte payload:

```text
@ MGMT Event: Command Complete   Add Extended Advertising Parameters (0x0054)
        Status: Success (0x00)
        Available adv data len: 31
        Available scan rsp data len: 31
@ MGMT Command:                  Add Extended Advertising Data (0x0055)
        Advertising data length: 0
        Scan response length: 0
@ MGMT Event: Command Status     Add Extended Advertising Data (0x0055)
        Status: Invalid Parameters (0x0d)
```

Nothing in userspace can shrink a zero-byte payload, so no OpenFlight or Bless
setting works around this. Boot a kernel without the regression (6.12.x is
reported to work) and rerun the probe. Until then, use the network transport
above: it needs no Bluetooth and delivers the identical payload.

**The Pi logs that Bluetooth is unavailable.**

- Run `uv sync --extra ble`.
- Confirm the BlueZ service is running with `systemctl status bluetooth`.
- Confirm the user running OpenFlight can access the system D-Bus and Bluetooth
  adapter.

**The app connects but no new shot appears.**

- Confirm the browser UI received the shot; BLE publishes only completed shot
  events.
- Look for `[BLE]` warnings in the OpenFlight terminal.
- Tap Retry in the app to disconnect, scan, and subscribe again.

## Automated tests

The Python side is covered in [Testing without hardware](#testing-without-hardware).
The SwiftUI app's tests run from a checkout of jake-fishtech's `feat/iOS-ble`
branch:

```bash
xcodebuild test \
  -project ios/OpenFlight.xcodeproj \
  -scheme OpenFlight \
  -destination "platform=iOS Simulator,id=PASTE-SIMULATOR-UUID-HERE" \
  CODE_SIGNING_ALLOWED=NO
```

List valid simulator UUIDs first with `xcrun simctl list devices available`.
