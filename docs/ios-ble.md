# Phone app connection (Bluetooth LE and network)

> **BLE blocker — check the Raspberry Pi kernel first:** Raspberry Pi kernel
> `6.18.34+rpt-rpi-2712` has a confirmed regression that rejects every BLE
> advertisement. Run `uname -r` on the Pi. If it reports that version, use the
> network transport or boot a working kernel such as 6.12.x; there is no userspace workaround. See the [full diagnosis](#known-bad-raspberry-pi-kernel-61834rpt-rpi-2712).

OpenFlight sends each completed shot from a Raspberry Pi to a phone app over
one of two local transports. Both carry the identical versioned payload
described below (schema version 2), so an app behaves the same either way.
The Kotlin Multiplatform companion for Android and iOS,
[`btripp/openflight-mobile-kmp`](https://github.com/btripp/openflight-mobile-kmp),
speaks it. jake-fishtech's SwiftUI app on the
[`feat/iOS-ble` branch of his fork](https://github.com/jake-fishtech/openflight/tree/feat/iOS-ble/ios)
speaks an earlier, unreleased version one that the Pi does not serve; it needs
updating to schema 2.

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

A connection opens with a `: ping` comment, the current state, and the
session's shots (see [Network stream](#network-stream)), then emits one message
per shot or event with a heartbeat every 15 seconds while idle:

```text
: ping

event: shot
id: 05dd37ec-49ed-596b-b1a4-953d54e4f239
data: {"ball_speed_mph":151.4,"club":"driver",...,"schema_version":2,"type":"shot"}
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

## Connect the phone app

The phone apps are not part of this repository; build and install one from its
own repository. Start OpenFlight on the Pi, adding `--ble` for the Bluetooth
transport. Over Bluetooth the app scans only for the OpenFlight service,
connects automatically, and subscribes to shot and control notifications.
Over the network it opens the shot stream and keeps it open. Either way, hit a
shot and its metrics should replace the empty dashboard. The most recent shot is replayed when a phone connects, so a newly
connected phone does not have to wait for another shot.

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

## Wire protocol

Phones speak JSON messages, schema version 2, split across BLE notifications.
There is no version one on the wire: no app was released against it.

### Characteristics

| Attribute | UUID | Properties |
|---|---|---|
| Service | `B6F633F2-E6E3-45AE-84B4-968ECCA2D9C7` | |
| Shot notification | `ED365FE6-3ABF-4FC3-8E44-D9525A22DABD` | notify |
| Control | `7BA96E63-12C2-4CE0-BB84-3513C7FD1474` | write with response, notify |

BlueZ notifies every central subscribed to a characteristic from that
characteristic's single value: the GATT D-Bus API has no per-device notify, and
Bless drops the writing device's path. So every connected phone receives every
shot, event and command response; match responses to your own requests by
`request_id`.

### Encoding and framing

Messages are compact JSON with sorted keys, and text is **UTF-8** rather than
`\uXXXX` escapes (that is what lets twelve 40-character profile names fit in one
message). Over BLE each message is split into conservative 20-byte
notifications, each a five-byte big-endian header followed by up to 15 payload
bytes:

| Byte(s) | Meaning |
|---|---|
| 0 | Frame version (`1`) |
| 1–2 | Unsigned 16-bit message sequence, counted per characteristic |
| 3 | Zero-based fragment index |
| 4 | Total fragment count |
| 5–19 | JSON payload fragment |

Group frames by sequence, ignore duplicate fragment indexes, order fragments by
index, and decode UTF-8 only after all fragments arrive: a fragment boundary can
split a multi-byte character. The shot contract fixture is
`tests/fixtures/shot_v2.json`, and framed byte-level goldens for every message
type live in `tests/fixtures/ble_goldens/` (see
[Testing without hardware](#testing-without-hardware)).

### Commands and responses

Commands are written to the control characteristic in the same framing. A
command contains `schema_version`, a unique `request_id`, a `type` and a JSON
`payload`; the Pi notifies a response with the matching `request_id`, `ok`, and
either `result` or `error`:

```json
{"payload":{"club":"7-iron"},"request_id":"9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d","schema_version":2,"type":"set_club"}
{"ok":true,"request_id":"9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d","result":{"club":"7-iron","status":"applied"},"schema_version":2}
```

Responses and events share the control characteristic: messages with a `type`
are events, the rest are responses. A request envelope may carry
`schema_version` 1 or 2 (so a client can reuse one encoder for `hello`);
responses always carry `schema_version: 2`. Each command calls the same server
function as its Socket.IO counterpart, so the kiosk and every other client see
the same broadcasts.

| Command | Payload | Result | Also broadcasts |
|---|---|---|---|
| `hello` | `{"client_schema_max":2}`, optional `last_event_id` | see [Negotiation](#negotiation) | |
| `get_club` | `{}` | `{"status":"current","club":"7-iron"}`: the Pi-owned club, unchanged | |
| `set_club` | `{"club":"7-iron"}` | `{"status":"applied","club":"7-iron"}` | `club_changed` |
| `get_profiles` | `{}` | `{"status":"sent"}` | `profiles`: the roster arrives as the event, not in the result |
| `set_active_profile` | `{"profile_id":…}` | `{"status":"applied","active_profile_id":…}`, or `ok:false` `Unknown profile` | `profiles` (also when rejected) |
| `get_power_status` | `{}` | the `power_status` payload, or `ok:false` `Battery monitoring is not enabled` / `No battery reading yet` | |

Over BLE the phone is read-and-select only (see [Security](#security-and-scope)).
Adding, renaming and removing profiles, `clear_session` and `delete_shot` stay
on Socket.IO and the kiosk; sent over BLE, they and any unknown command fail
with `Unsupported phone command: <type>`. Phones still learn about those
changes from the `profiles`, `session_cleared` and `shot_deleted` events. An
event triggered by a command is normally notified before the command's
response, but clients must accept either order. The Pi processes commands as
they arrive and enforces no busy state or timeout of its own; clients own their
timeouts.

### Negotiation

1. Discover the service and subscribe to the control characteristic.
2. Write `hello`. Add `last_event_id`, the `event_id` of the newest shot the app
   already has, to [catch up](#catch-up-after-a-reconnect) on shots missed while
   disconnected; leave it out on a first connection:
   ```json
   {"payload":{"client_schema_max":2,"last_event_id":"05dd37ec-49ed-596b-b1a4-953d54e4f239"},"request_id":"<uuid>","schema_version":2,"type":"hello"}
   ```
   The result names the schema, the features and the characteristics:
   ```json
   {"ok":true,"request_id":"<uuid>","result":{"characteristics":{"control":"7BA96E63-12C2-4CE0-BB84-3513C7FD1474","shot":"ED365FE6-3ABF-4FC3-8E44-D9525A22DABD"},"features":["provisional_shots","shot_processing","profiles","power_status","shot_deleted","club","shot_catch_up"],"schema_version":2},"schema_version":2}
   ```
3. Subscribe to the shot characteristic. The catch-up shots arrive. A client
   that skipped `hello` gets only the latest shot.
4. Ask for state: `get_club`, `get_profiles` and, if wanted, `get_power_status`.

A `client_schema_max` below 2 fails with `ok:false`: the Pi speaks schema 2 only.

### Catch-up after a reconnect

A phone that leaves the app, walks out of range or loses the network misses the
shots taken meanwhile. It is caught up on reconnect, with one rule for both
transports:

| | BLE | Network |
|---|---|---|
| Name the newest shot you have | `last_event_id` in the `hello` payload | `Last-Event-ID` request header, or `?last_event_id=` (the header wins) |
| Catch-up arrives | On the shot characteristic, after the `hello` response; held until the phone subscribes to it | Seeded after the state events, before live events |
| Without catch-up | A client that skips `hello` gets the latest shot | — (every connection gets catch-up) |

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
- Every network `shot` frame carries `id: <event_id>`, so an `EventSource`
  resends `Last-Event-ID` on its own when it reconnects. Other events carry no
  `id`, which leaves the last shot id in place.
- Over BLE a notification reaches every subscribed phone, so another connected
  phone receives the catch-up too and upserts it.
- A shot taken while a BLE catch-up is still being sent can push the oldest
  queued catch-up shot out of the eight-message delivery queue. The phone then
  lacks that one shot until its session is replayed in full (for example after
  it reconnects without `last_event_id`).

The Pi advertises support with the `shot_catch_up` feature in the `hello`
result.

### Shot

Sent on the shot characteristic, and as `event: shot` on the network stream:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `2` | |
| `type` | `"shot"` | |
| `event_id` | UUID string | Stable per shot: the provisional and final versions of one shot share it. **Upsert by `event_id`** |
| `final` | bool | `false`: OPS-only provisional shot, sent while optional hardware (IWR6843, camera) is still working. `true`: the final shot |
| `timestamp`, `club` | string | Detection time and the club the shot was hit with |
| `ball_speed_mph`, `estimated_carry_yards` | number | |
| `club_speed_mph`, `smash_factor`, `launch_angle_vertical`, `launch_angle_horizontal`, `spin_rpm`, `club_path_deg`, `spin_axis_deg` | number or null | `null` when the active hardware could not produce it |
| `shot_number` | int or null | Per-monitor-run sequence; not reused after a delete |
| `profile_id`, `profile_name` | string or null | Profile the shot was attributed to at detection |
| `carry_range` | `[low, high]` or null | Carry range in yards |
| `spin_source` | string or null | Where `spin_rpm` came from |
| `launch_angle_confidence` | number or null | 0–1 |
| `enrichment` | object or null | `{"status":"pending"}` on a provisional shot; `{"status":"complete"}` or `{"status":"skipped","reason":"deadline"|"capacity"|"queue_full"|"worker_unavailable"}` on a final shot that had a provisional; `null` when the shot never waited for optional hardware |

Every key is always present; unknown values are `null`. A shot with no optional
hardware configured is sent once, final. The contract fixture is
`tests/fixtures/shot_v2.json`, built from a real mock shot:

```json
{"ball_speed_mph":106.1,"carry_range":[144,160],"club":"7-iron","club_path_deg":2.5,"club_speed_mph":83.5,"enrichment":{"status":"complete"},"estimated_carry_yards":152,"event_id":"05dd37ec-49ed-596b-b1a4-953d54e4f239","final":true,"launch_angle_confidence":0.6,"launch_angle_horizontal":-0.7,"launch_angle_vertical":21.2,"profile_id":"0f8e4b2a9c7d4e1f8a6b3c5d7e9f1a2b","profile_name":"Zoë","schema_version":2,"shot_number":7,"smash_factor":1.27,"spin_axis_deg":-1.6,"spin_rpm":6482,"spin_source":null,"timestamp":"2026-09-25T14:03:07.412345","type":"shot"}
```

### Events

Notified on the control characteristic, and as named events on the network
stream. Each has `schema_version: 2` and a `type`, and never a `request_id`:

| `type` | Fields | When |
|---|---|---|
| `club_changed` | `club` | Any club change (kiosk, phone, simulator) |
| `profiles` | `profiles: [{id, name}]`, `active_profile_id` | After every profile request or mutation from any client, including rejected ones |
| `session_cleared` | `profile_id` | After a profile's shots are cleared on the kiosk |
| `shot_deleted` | `timestamp` (the shot's delete key) | After a shot is deleted on the kiosk |
| `shot_processing` | `state`: `capturing`, `calculating` or `failed` | Rolling-buffer monitor progress; the next shot ends it |
| `power_status` | the Socket.IO `power_status` payload: `available, provider, state, battery_percent, battery_voltage_v, external_power, updated_at, error` | Every 5 s with `--battery` |

```json
{"club":"7-iron","schema_version":2,"type":"club_changed"}
{"active_profile_id":"0f8e…","profiles":[{"id":"0f8e…","name":"Zoë ⛳"},{"id":"7c1d…","name":"Sam"}],"schema_version":2,"type":"profiles"}
{"profile_id":"0f8e…","schema_version":2,"type":"session_cleared"}
{"schema_version":2,"timestamp":"2026-09-25T14:03:07.412345","type":"shot_deleted"}
{"schema_version":2,"state":"calculating","type":"shot_processing"}
{"available":true,"battery_percent":76.5,"battery_voltage_v":3.98,"error":null,"external_power":false,"provider":"geekworm","schema_version":2,"state":"on_battery","type":"power_status","updated_at":"2026-09-25T14:03:05.000000+00:00"}
```

Profiles carry only `id` and `name`. `created_at` and the open-ended `settings`
stay on Socket.IO, because the phone only selects profiles here and an
unbounded `settings` object could not be guaranteed to fit in one BLE message.

### Network stream

`GET /api/shots/stream` streams schema 2 as Server-Sent Events. `?schema=2` is
accepted for clients that name it; any other `schema` value returns `400`. A
stream opens with `: ping`, then the current state as `club_changed`,
`profiles` and (with a battery monitor) `power_status`, then the
[catch-up](#catch-up-after-a-reconnect) shots (the whole session unless
`Last-Event-ID` names a shot). Each `shot` frame carries `id: <event_id>`.
Event names match the `type` of the payload. Commands stay on `/api/club` and
Socket.IO.

### Size budget

A BLE message is at most 255 fragments × 15 bytes = 3,825 bytes. Tests encode
a worst-case shot (longest float representations everywhere, a 40-character
profile name of six-byte escapes) and a `profiles` event with twelve such names
(3,610 bytes) and require both to fit. The publisher refuses, and logs, any
message that would not fit instead of sending a truncated one.

## Delivery behavior

- Shot processing never waits for either transport, and a failure in one cannot
  affect the other, the browser UI, or session logging.
- Each connected client gets a bounded queue of eight unsent events; the oldest
  queued event is dropped if that client cannot keep up. One stalled phone
  cannot slow down another.
- Disconnecting clears that client's queue. On the next connection the client
  is [caught up](#catch-up-after-a-reconnect) on the session shots it missed
  (up to 20).
- Clients upsert shots by `event_id`, which ignores replays of shots they
  already have and merges a provisional shot with its final version.

## Security and scope

The phone protocol intentionally has no application authentication or
encryption layer on either transport. Enable BLE only where nearby Bluetooth
devices receiving shots and issuing club and profile selections is acceptable,
and treat the network API as accessible to anything on the same network — the
same assumption the browser UI already makes.

BLE is unauthenticated: any nearby device can connect and write the control
characteristic. The protocol therefore exposes only reading state, selecting
(club, active profile) over Bluetooth. Actions that delete data, clearing a session or deleting a shot, and
profile add, rename and remove stay on the kiosk (Socket.IO), where
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
  dispatch: latest-shot replay, `hello` in either request envelope, a
  provisional-then-final shot reaching every phone with one `event_id`, club
  and profile commands, unknown commands, and one phone leaving while another
  keeps receiving.
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
uv run pytest tests/test_ble_protocol.py \
  tests/test_ble_publisher.py tests/test_ble_loopback.py tests/test_ble_goldens.py \
  tests/test_shot_stream.py tests/test_phone_transport_server.py \
  tests/test_phone_transport.py tests/test_control_commands.py \
  tests/test_phone_catch_up.py tests/test_ble_catch_up.py \
  tests/test_shot_stream_catch_up.py tests/test_configure_bluetooth.py -v
uv run python scripts/ble/generate_goldens.py --check
```

What still needs a Pi and phones: BlueZ advertising, discovery and
connection from iOS and Android, pairing and permission prompts, fragment
pacing over a real link, reconnects after a Pi restart, background behaviour,
and coexistence with SSE and Socket.IO clients.

## Troubleshooting

**The app stays on “Looking for OpenFlight.”**

- Confirm OpenFlight was started with `--ble`.
- Run `bluetoothctl show` on the Pi and confirm `Powered: yes`.
- Keep the app in the foreground for the initial connection.
- Restart OpenFlight after changing the Pi Bluetooth configuration.

**The iPhone shows a pairing prompt every 30 seconds.**

Symptom: the phone connects, then disconnects about every 33 seconds and iOS
asks to pair again. The OpenFlight log shows `[BLE] Client subscribed` followed
by `[BLE] Client unsubscribed` exactly 30 seconds later, and `bluetoothctl info
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
reported to work) and rerun the probe.

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
Phone apps run their own test suites and decode the committed goldens in
`tests/fixtures/ble_goldens/`.
