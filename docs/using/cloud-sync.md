# Cloud Sync

OpenFlight can push your session logs to the **FlightWeb** cloud so you can
review shots from any device. It's an opt-in `openflight-cloud` CLI that runs
on the Pi.

> **Privacy promise — raw radar data never leaves your Pi.** The uploader
> applies an allowlist *before* upload: only shot results and session metadata
> are sent. Raw I/Q captures, rolling-buffer dumps, and per-reading detections
> stay local. The server stores exactly what the Pi sends — so the filter is
> enforced here, on the device. Run `openflight-cloud push --dry-run` any time
> to see precisely what would be uploaded.

## Quick start

```bash
openflight-cloud link              # one-time: pair this Pi with your account
openflight-cloud status            # linked? queued? parked? last error?
openflight-cloud push --dry-run    # show exactly which entries would upload
openflight-cloud push              # filter + upload anything not yet pushed
openflight-cloud push --retry      # also re-attempt parked/failed sessions
```

`scripts/setup/setup.sh` offers to enable cloud sync and link the Pi for you
(on a Raspberry Pi). Everything below can also be done by hand.

> **Upgrading an existing install?** The `openflight-cloud` command is created
> at install time. If you added cloud sync by pulling new code into a venv that
> predates it, reinstall so the console script gets wired up:
>
> ```bash
> uv sync
> ```
>
> Until then you can run it as a module:
> `uv run python -m openflight.cloud.cli link`.

## Linking a device

You never copy a long token onto the Pi. Linking uses a short, screen-readable
code (RFC 8628 device flow):

1. Run `openflight-cloud link`. It prints something like:

   ```
     Go to https://flightweb.fly.dev/link and enter code:  ABCD-2345

     (waiting up to 900s; sign in and enter the code)
   ```

2. Open that URL in any browser, sign in, and enter the code.
3. The Pi detects the pairing, saves its device token, and enables uploads:

   ```
   Linked! device_id=… . Uploads are now enabled.
   ```

The code expires after ~15 minutes. If it expires or you mistype, just re-run
`openflight-cloud link`.

To name the device (defaults to the Pi's hostname):

```bash
openflight-cloud link --device-name "garage pi"
```

## How uploads happen

Two triggers, one mechanism — both are safe to run at any time and never block
shot processing:

- **systemd timer** (`openflight-cloud.timer`, ~every 10 min) runs `push`. This
  is the safety net that heals wifi outages.
- **On session end**, the server fires a non-blocking `push` for the fast happy
  path. If it fails (offline, etc.), the timer picks it up later.

**The session directory is the queue.** No database is involved — state lives
in sidecar files next to each `session_*.jsonl`, so it survives reboots and
crashes:

| Sidecar | Meaning |
|---|---|
| `<session>.jsonl.pushed` | Successfully uploaded (won't be sent again). |
| `<session>.jsonl.parked` | Given up on — see `reason`; reported by `status`. |
| `<session>.jsonl.state`  | In-flight retry counter / quota cooldown. |

Originals are never moved or modified. Uploads are idempotent: re-sending a
session that's already stored is safe and dedupes server-side.

### What gets retried, what gets parked

| Situation | Behavior |
|---|---|
| Network down / server 5xx | Retried on the next timer tick (exponential per-file backoff); parked after ~20 failures. |
| Rate limited (429) | Backs off for the server-provided interval, then retries. |
| Quota exceeded (402) | Deferred ~24h, then retried automatically. |
| Token rejected (401) | All uploads stop and `status` flags **needs re-link** — run `openflight-cloud link` again. |
| Rejected as malformed/too large (413/422) | Parked (this indicates a client bug and shouldn't happen with correct filtering). |

## Config

Stored at `~/.config/openflight/cloud.json`, mode `0600` (the `device_token` is
a bearer credential — keep it secret):

```json
{
  "endpoint": "https://flightweb.fly.dev",
  "device_token": "of_device_…",
  "device_id": "…",
  "enabled": true
}
```

- Written by `link`; read by `push` / `status`.
- Set `"enabled": false` (or delete the file) to turn the uploader into a no-op
  without unlinking.
- `endpoint` is configurable in case the production domain moves.

## systemd units

Installed by `setup.sh`, or by hand:

```bash
sudo cp scripts/setup/openflight-cloud.{service,timer} /etc/systemd/system/
# edit User= and the paths if your install isn't /home/coleman/openflight
sudo systemctl daemon-reload
sudo systemctl enable --now openflight-cloud.timer
```

Inspect it:

```bash
systemctl status openflight-cloud.timer     # next run time
journalctl -u openflight-cloud.service       # upload logs
```

The timer is harmless before you link — uploads stay off until a device token
exists.

## Troubleshooting

- **`status` says "not linked"** — run `openflight-cloud link`.
- **"needs re-link" / 401** — the token was revoked or rotated. Re-run
  `openflight-cloud link`; the new token replaces the old one in the config.
- **A session is parked** — `status` shows the reason and last error. Parked
  sessions are skipped on future runs. Re-attempt all parked/deferred sessions
  with `openflight-cloud push --retry`.
- **A session uploaded with 0 shots (or you want to re-send a stored one)** —
  `status` flags 0-shot uploads by name. Force a re-upload of a specific
  session (even one already marked pushed) with
  `openflight-cloud push --retry <session-name>` — `<session-name>` is the
  filename or any substring of it. Re-uploads are idempotent (the server
  dedupes), so this is always safe.
- **Nothing uploads / "Nothing to upload"** — everything is already pushed.
  Confirm `enabled: true` and that the Pi is online (`status` reports
  reachability). To re-send a session that was already pushed, use
  `push --retry <session-name>` as above.
- **"What is it sending?"** — `openflight-cloud push --dry-run` lists every
  entry type and count that would be uploaded, and what's dropped.

## See also

- [`docs/openflight-cloud-uploader-spec.md`](../reference/cloud-uploader-spec.md)
  — the wire contract this client implements (endpoints, status codes, caps).
