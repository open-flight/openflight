# `openflight-cloud` Uploader — Wire Contract

This is the wire contract implemented by the uploader in `src/openflight/cloud/`.
The FlightWeb service is a separate system; this contract is the boundary
between the two repositories.

---

## 0. Client surface

A small CLI, `openflight-cloud`, that lives on the Pi and pushes filtered
session logs to the FlightWeb cloud. Three subcommands, one spool-and-retry
mechanism:

```
openflight-cloud link              # one-time device pairing
openflight-cloud push [--dry-run]  # filter + upload anything unpushed
openflight-cloud status            # linked? queued? parked? last error?
```

**The single most important rule:** the server stores the device-uploaded blob
**verbatim** — it does *not* re-filter raw radar data out of a device upload.
Therefore **the uploader MUST apply the allowlist filter (§4) before upload.**
The product promise "raw radar data never leaves your Pi" is enforced *here*,
in this client. (The server does filter *manual web uploads* as defense in
depth, but that path is irrelevant to the Pi.)

---

## 1. Endpoint + transport

- Base URL: configurable; current deployment **`https://flightweb.fly.dev`**.
  Store it in config — the production domain may move (openflight vs. flightweb
  is undecided). All paths below are under `/v1`.
- TLS only. Bearer auth for session upload; link endpoints are unauthenticated.
- Request/response bodies are JSON except the session-upload body, which is
  gzipped NDJSON.
- The server returns `Retry-After` (seconds) on every `429`.

---

## 2. Device linking (RFC 8628-style)

The user never copies a long token onto the Pi. Flow:

### 2a. `POST /v1/device-link/start`

Unauthenticated. Rate limited **per IP: 10 requests / 15 min** (→ `429`).

Request:
```json
{ "device_name": "garage pi", "client_version": "0.3.0" }
```
- `device_name`: **required, 1–64 chars** (trimmed). Missing/blank/too-long → `422`.
- `client_version`: optional string.

Response `200`:
```json
{ "link_code": "ABCD-2345", "poll_token": "<opaque>", "interval_s": 5, "expires_s": 900 }
```
- `link_code` format: `^[ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[2-9]{4}$` (no I/O/0/1 —
  built to be read off a screen). Print it for the user:
  `Go to <endpoint>/link and enter code ABCD-2345`.
- `poll_token`: high-entropy opaque string; **persist it** for polling. Treat as
  a secret (don't log).
- `interval_s` (5): minimum seconds between polls — **honor it**.
- `expires_s` (900): code/poll lifetime (~15 min).

### 2b. User action (out of band)

The user opens `<endpoint>/link` in a browser, signs in, and enters the code.

### 2c. `POST /v1/device-link/poll`

Rate limited **per poll_token: 30 / 60s** (→ `429`). Poll no faster than
`interval_s`.

Request:
```json
{ "poll_token": "<opaque>" }
```

Responses (all `200` unless noted):
| Body | Meaning | Client action |
|---|---|---|
| `{"status":"pending"}` | not entered yet | keep polling at `interval_s` |
| `{"status":"expired"}` | code lifetime passed | stop; tell user to re-run `link` |
| `{"status":"linked","device_token":"of_device_…","device_id":"<uuid>"}` | paired | **save token + id, stop polling** |
| `404 {"reason":"unknown_poll_token"}` | unknown OR already consumed | stop; re-run `link` |
| `429` + `Retry-After` | polling too fast | back off |

**The `linked` response is returned exactly once.** The first successful poll
after the user enters the code issues the token; any subsequent poll with the
same `poll_token` gets `404`. So persist `device_token` + `device_id`
**atomically on first receipt** — if you crash between receiving and saving,
the user must re-link.

- `device_token` format: `^of_device_[0-9A-Za-z]{32}$`. The server stores only a
  SHA-256 hash; this plaintext is shown **once**.

---

## 3. Session upload

### `PUT /v1/sessions/{session_id}`

```
PUT /v1/sessions/1f0e9c2a-7b3d-4e5f-8a9b-0c1d2e3f4a5b
Authorization: Bearer of_device_<…>
Content-Type: application/x-ndjson
Content-Encoding: gzip

<gzipped, filtered NDJSON>
```

Rate limited **per device: 120 / hour** (→ `429`). A full backlog flush stays
well under this; if you somehow hit it, honor `Retry-After`.

**`{session_id}`** must be a lowercase UUID (server lowercases anyway, but send
lowercase). It is:
- the **`session_uuid`** from the `session_start` entry (UUID4, present since
  openflight 0.2.0 / format_version 1) — **preferred**; or
- for older sessions lacking it, a deterministic **UUIDv5 of
  `(device_id, session_filename)`** so the same file always maps to the same id
  (free dedupe + safe retries).

If the body contains a `session_start` with a `session_uuid`, it **must equal**
the URL id (case-insensitive) or you get `422 session_uuid_mismatch`. Simplest:
always use the embedded `session_uuid` as the URL id when present.

**Body:** gzipped NDJSON, filtered per §4, with a manifest first line (§4).
PUT is idempotent — re-uploading the same session is safe and dedupes.

### Responses → client behavior

| Code | Body | Meaning | Client action |
|---|---|---|---|
| `201` | `{session_id, shot_count}` | accepted, stored | mark `.pushed` |
| `200` | `{session_id, shot_count}` | duplicate (already stored) | mark `.pushed` |
| `401` | `{reason}` | bad/revoked/missing token (`missing_or_malformed_token` \| `invalid_or_revoked_token`) | stop all uploads; flag "needs re-link" |
| `402` | `{reason:"quota_exceeded"}` | over entitlement | park; retry daily |
| `413` | `{reason}` | too large (`body_too_large` gz>20 MB, or `inflated_too_large` >64 MB) | log + park (should never happen if §4 filtering works) |
| `422` | `{reason}` | unparseable (`invalid_session_id`, `session_uuid_mismatch`, `invalid_gzip`, `no_valid_jsonl`) | log + park; this is a client bug — surface it |
| `429` | `{reason:"rate_limited"}` + `Retry-After` | rate limited | back off `Retry-After` seconds |
| `5xx` | — | server trouble | retry with exponential backoff |

`200` and `201` are both success — treat identically (mark pushed). Don't
distinguish them for retry purposes.

### `GET /v1/health`

`200 {"status":"ok"}`, unauthenticated, no side effects. Use it to
short-circuit `push` when offline (cheap connectivity probe before doing work).

---

## 4. Client-side filtering (the raw-ADC strip) — REQUIRED

Build the upload body by transforming the session `.jsonl`:

1. **Prepend one manifest line** as the first line:
   ```json
   {"type":"upload_manifest","format_version":1,"client_version":"0.3.0",
    "device_id":"<id>","filtered":true,"kept_entry_types":[...]}
   ```
2. **Keep only allowlisted entry types** (filter by each line's `type`):
   ```
   KEEP:  session_start, session_end, shot_detected, trigger_event, session_error
   DROP:  rolling_buffer_capture, iq_blocks, iq_reading, reading_accepted,
          ops_clock_sync, shot_camera, config_change, and ANYTHING not on the keep list
   ```
   Use an **allowlist, not a blocklist** — a future heavy entry type the session
   logger gains must never leak by default.
3. **Drop any kept line > 32 KB** (and count it) — belt-and-suspenders guard
   matching the server's per-line cap.
4. **gzip** the result. Keep it under **20 MB gzipped / 64 MB inflated** (a
   filtered session is normally tens of KB, so this is just a safety check —
   if a session somehow exceeds it, park the file and report it rather than
   uploading raw).

`--dry-run` should print exactly which entry lines *would* upload (the privacy
answer to "what are you sending?"). The server matches this allowlist exactly,
so anything you keep here is what gets stored.

> Why this is load-bearing: the server stores the device blob **as received**.
> Whatever you upload is what lives in the cloud. Filtering is not optional.

---

## 5. The uploader mechanism (spool-and-retry)

- **The session directory is the queue.** A session counts as pushed when a
  sidecar marker `"<session>.jsonl.pushed"` exists. Never move or modify
  originals — state survives crashes, no database needed.
- **Triggers:** a **systemd timer** (~every 10 min) runs `push` (heals wifi
  outages); the server process also fires a non-blocking `push` on session end
  (fast happy path). Neither may ever block/delay shot processing.
- **Per-file attempt counter** in the sidecar; after ~20 failures, **park** the
  file (`"<session>.jsonl.parked"`) and report via `status` instead of
  retrying forever.
- **Terminal vs. retryable** (see §3 table): `401` stops everything and flags
  re-link; `402` parks for daily retry; `413`/`422` park (client bug — these
  shouldn't happen with correct filtering); `429`/`5xx` back off and retry.
- Push is **opt-in**: nothing leaves the Pi until the user runs
  `openflight-cloud link`.

---

## 6. Config — `~/.config/openflight/cloud.json`, mode `0600`

```json
{ "endpoint": "https://flightweb.fly.dev",
  "device_token": "of_device_…",
  "device_id": "…",
  "enabled": true }
```
- `0600`, never logged. `device_token` is a bearer credential.
- `enabled:false` (or file absent) → uploader is a no-op.
- Written by `link` on success; read by `push`/`status`.

---

## 7. Evolution rules (so old Pis keep working forever)

- The server **accepts and stores unknown entry types** in the blob (it skips
  parsing them, doesn't reject) — but you should still filter to the allowlist
  so raw data doesn't leak.
- The server **accepts unknown fields** on known entry types.
- `/v1` is append-only and tolerates old clients indefinitely. Breaking changes
  would ship as `/v2`; `/v1` keeps working. So a fielded Pi that never updates
  keeps uploading forever.
- Send a real `client_version` in `device-link/start` and the manifest — it's
  stored per-device and helps the server team support old clients.

---

## 8. Reference values (verified against deployed server)

| Thing | Value |
|---|---|
| Link code regex | `^[ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[2-9]{4}$` |
| Device token regex | `^of_device_[0-9A-Za-z]{32}$` |
| Poll interval / code TTL | 5 s / 900 s |
| Rate: link-start | 10 / 15 min per IP |
| Rate: poll | 30 / 60 s per poll_token |
| Rate: upload | 120 / hour per device |
| Caps | 20 MB gzipped, 64 MB inflated, 32 KB per line |
| Allowlist (keep) | session_start, session_end, shot_detected, trigger_event, session_error (+ upload_manifest) |
| Health | `GET /v1/health` → `200 {"status":"ok"}` |

---

## 9. Suggested build order

1. `link` against `/v1/device-link/{start,poll}` → write config. Verify a real
   device row appears (the user will see it on the FlightWeb Devices page).
2. Filtering + manifest (§4) with `--dry-run` first — get the body exactly right
   before uploading anything.
3. `push` for a single session → confirm `201`, then re-run → confirm `200`
   duplicate.
4. Spool/sidecar/park mechanics + systemd timer.
5. `status` reporting.

**Exit test (shared with the server team):** a real Pi runs `link`, the user
enters the code, a session uploads, and it appears on FlightWeb. That closes
the Phase 1 loop.
