"""Command orchestration for the openflight-cloud CLI.

Each function takes its dependencies explicitly (config, client, output sink,
sleep) so the logic is testable without the network or real timers.
"""

import socket
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import filtering, spool
from .client import CloudNetworkError, RateLimited
from .config import CloudConfig, save_config

OutFn = Callable[[str], None]


def cmd_link(
    config: CloudConfig,
    config_path: Path,
    client,
    device_name: Optional[str] = None,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    out: OutFn = print,
) -> bool:
    """One-time device pairing (RFC 8628-style). Returns True on success."""
    device_name = (device_name or socket.gethostname() or "openflight pi").strip()[:64]
    try:
        start = client.device_link_start(device_name, filtering.CLIENT_VERSION)
    except RateLimited as exc:
        out(f"Rate limited starting link; try again in {exc.retry_after}s.")
        return False

    link_url = f"{config.endpoint.rstrip('/')}/link"
    out("")
    out(f"  Go to {link_url} and enter code:  {start.link_code}")
    out("")
    out(f"  (waiting up to {start.expires_s}s; sign in and enter the code)")

    deadline = now_fn() + start.expires_s
    interval = max(1, start.interval_s)
    while now_fn() < deadline:
        sleep(interval)
        try:
            poll = client.device_link_poll(start.poll_token)
        except RateLimited as exc:
            sleep(exc.retry_after or interval)
            continue

        if poll.status == "pending":
            continue
        if poll.status == "linked":
            # Persist token + id atomically on first receipt — the linked
            # response is returned exactly once.
            config.device_token = poll.device_token
            config.device_id = poll.device_id
            config.enabled = True
            save_config(config, config_path)
            out(f"Linked! device_id={poll.device_id}. Uploads are now enabled.")
            return True
        # expired or unknown (consumed/invalid token)
        out(f"Link {poll.status}. Re-run `openflight-cloud link`.")
        return False

    out("Link timed out. Re-run `openflight-cloud link`.")
    return False


def _describe_dry_run(filename: str, session_id: str, result: filtering.FilterResult, out: OutFn):
    raw = bool(result.manifest.get("raw"))
    out(f"\n{filename}  ->  session {session_id}{'  (raw)' if raw else ''}")
    if not result.kept_lines:
        out("  (nothing to upload — no allowlisted entries)")
    for entry_type in sorted(result.kept_type_counts):
        out(f"  keep  {result.kept_type_counts[entry_type]:>5} x {entry_type}")
    if result.dropped_oversize:
        limit = "1 MB" if raw else "32 KB"
        out(f"  drop  {result.dropped_oversize:>5} oversize line(s) (>{limit})")
    if result.captures:
        present = [Path(c.path) for c in result.captures if Path(c.path).is_file()]
        total_mb = sum(p.stat().st_size for p in present) / (1024 * 1024)
        out(f"  dump  {len(present):>5} x iwr6843 capture file(s), {total_mb:.1f} MB")
        missing = len(result.captures) - len(present)
        if missing:
            out(f"  skip  {missing:>5} capture file(s) not on disk")


def _push_captures(log_dir: Path, client, summary: Dict[str, Any], out: OutFn):
    """Upload queued raw capture files for sessions the server already has.

    Each capture is matched to its shot server-side by ``shot_number``. Stops
    on the same conditions as the session pass (relink, rate limit, offline).
    """
    for path in spool.session_files(log_dir):
        if not spool.is_pushed(path):
            continue
        pending = spool.pending_captures(path)
        if not pending:
            continue
        session_id = spool.read_pushed(path).get("session_id")
        remaining: List[Dict[str, Any]] = []
        parked: List[Dict[str, Any]] = []
        stop = False
        for index, capture in enumerate(pending):
            capture_path = Path(capture["path"])
            if not capture_path.is_file():
                parked.append(dict(capture, reason="missing_file"))
                out(
                    f"{path.name}: shot {capture['shot_number']} capture missing on disk — skipped."
                )
                continue
            try:
                upload = client.upload_capture(
                    session_id,
                    capture.get("kind", filtering.IWR6843_CAPTURE_KIND),
                    capture["shot_number"],
                    capture_path.read_bytes(),
                    filename=capture_path.name,
                )
            except CloudNetworkError as exc:
                out(f"{path.name}: network error uploading captures ({exc}); will retry later.")
                summary["offline"] = True
                stop = True
            else:
                if upload.action == "success":
                    summary["captures_uploaded"] += 1
                    continue
                if upload.action == "relink":
                    summary["needs_relink"] = True
                    out("Device token rejected. Stopping uploads — re-run `openflight-cloud link`.")
                    stop = True
                elif upload.action == "rate_limited":
                    out(f"Rate limited; backing off {upload.retry_after}s. Will retry later.")
                    summary["rate_limited"] = upload.retry_after
                    stop = True
                elif upload.action == "park":
                    parked.append(dict(capture, reason=upload.reason or str(upload.status_code)))
                    out(
                        f"{path.name}: shot {capture['shot_number']} capture rejected "
                        f"({upload.status_code} {upload.reason}) — parked."
                    )
                    continue
                else:  # retry: 404 (session not stored yet) / 5xx
                    attempts = int(capture.get("attempts", 0)) + 1
                    retried = dict(capture, attempts=attempts)
                    if attempts >= spool.MAX_ATTEMPTS:
                        parked.append(dict(retried, reason="max_attempts"))
                    else:
                        remaining.append(retried)
                    summary["captures_failed"] += 1
                    out(
                        f"{path.name}: capture upload failed ({upload.status_code}); "
                        f"attempt {attempts}."
                    )
                    # The rest of this session would fail the same way.
                    remaining.extend(pending[index + 1 :])
                    break
            # Stopping: keep this capture and everything after it queued.
            remaining.extend(pending[index:])
            break
        spool.save_capture_queue(path, remaining, parked)
        if stop:
            return


def _apply_retry(log_dir: Path, session: Optional[str], out: OutFn) -> int:
    """Clear markers so failed/parked (or named) sessions upload again.

    Bulk retry (no ``session``) un-parks and clears cooldowns but leaves
    already-pushed sessions alone. A named ``session`` (filename substring)
    force-clears even ``.pushed`` so a session the server already stored — e.g.
    one that landed with 0 shots — can be re-sent. Returns how many matched.
    """
    targeted = bool(session)
    matched = 0
    for path in spool.session_files(log_dir):
        if targeted:
            if session not in path.name:
                continue
        elif not (spool.is_parked(path) or spool.in_cooldown(path)):
            continue
        cleared = spool.clear_markers(path, include_pushed=targeted)
        matched += 1
        if cleared:
            out(f"{path.name}: reset {', '.join(cleared)} for retry.")
    if matched == 0:
        if targeted:
            out(f"No session matching '{session}'. Run `openflight-cloud status` to list them.")
        else:
            out("No parked or deferred sessions to retry.")
    return matched


def cmd_push(
    config: CloudConfig,
    log_dir: Path,
    client,
    dry_run: bool = False,
    retry: bool = False,
    session: Optional[str] = None,
    out: OutFn = print,
) -> Dict[str, Any]:
    """Filter and upload anything unpushed. Returns a summary dict.

    With ``retry``, first reset markers so previously parked/failed sessions
    (or a specific ``session`` by filename substring) are eligible again.
    """
    summary: Dict[str, Any] = {
        "uploaded": 0,
        "parked": 0,
        "deferred": 0,
        "failed": 0,
        "offline": False,
        "needs_relink": False,
        "dry_run": dry_run,
        "captures_uploaded": 0,
        "captures_failed": 0,
    }

    if not dry_run and not config.is_active():
        out("Uploader inactive (not linked or disabled). Run `openflight-cloud link`.")
        summary["skipped"] = "inactive"
        return summary

    if retry:
        _apply_retry(log_dir, session, out)

    # Cheap connectivity probe so we don't churn while offline.
    if not dry_run and not client.health():
        out("Cloud unreachable; will retry later.")
        summary["offline"] = True
        return summary

    pending = spool.pending_sessions(log_dir)
    has_captures = config.upload_raw and any(
        spool.pending_captures(p) for p in spool.session_files(log_dir) if spool.is_pushed(p)
    )
    if not pending and not (has_captures and not dry_run):
        out("Nothing to upload.")
        return summary

    for path in pending:
        if not dry_run and spool.in_cooldown(path):
            summary["deferred"] += 1
            continue

        # Stream the file: a raw-ADC session can be hundreds of MB, but we only
        # keep the (tiny) shot lines. Loading it whole would risk OOM on a Pi.
        result = filtering.filter_session_file(path, config.device_id, raw_mode=config.upload_raw)
        session_id = result.session_id

        if dry_run:
            _describe_dry_run(path.name, session_id, result, out)
            continue

        try:
            body = filtering.build_upload_body(result)
        except filtering.BodyTooLargeError as exc:
            spool.mark_parked(
                path,
                reason="body_too_large",
                attempts=spool.read_attempts(path),
                last_error=str(exc),
            )
            summary["parked"] += 1
            out(f"{path.name}: too large after filtering — parked ({exc}).")
            continue

        try:
            upload = client.upload_session(session_id, body)
        except CloudNetworkError as exc:
            out(f"{path.name}: network error ({exc}); will retry later.")
            summary["offline"] = True
            break

        action = upload.action
        if action == "success":
            spool.mark_pushed(
                path,
                session_id,
                upload.shot_count,
                captures=[c.to_dict() for c in result.captures],
            )
            summary["uploaded"] += 1
            out(f"{path.name}: uploaded ({upload.status_code}).")
        elif action == "relink":
            summary["needs_relink"] = True
            out("Device token rejected. Stopping uploads — re-run `openflight-cloud link`.")
            break
        elif action == "quota":
            spool.record_cooldown(path, "quota_exceeded", spool.QUOTA_COOLDOWN_S)
            summary["deferred"] += 1
            out(f"{path.name}: quota exceeded — deferring ~24h.")
        elif action == "park":
            spool.mark_parked(
                path,
                reason=upload.reason or "client_error",
                attempts=spool.read_attempts(path),
                last_error=str(upload.status_code),
            )
            summary["parked"] += 1
            out(f"{path.name}: rejected ({upload.status_code} {upload.reason}) — parked.")
        elif action == "rate_limited":
            out(f"Rate limited; backing off {upload.retry_after}s. Will retry later.")
            summary["rate_limited"] = upload.retry_after
            break
        else:  # retry (5xx / unexpected)
            attempts = spool.record_failure(
                path, f"{upload.status_code} {upload.reason or ''}".strip()
            )
            summary["failed"] += 1
            if spool.is_parked(path):
                summary["parked"] += 1
                out(f"{path.name}: failed {attempts}x — parked.")
            else:
                out(f"{path.name}: server error ({upload.status_code}); attempt {attempts}.")

    stopped = summary["needs_relink"] or summary["offline"] or "rate_limited" in summary
    if config.upload_raw and not dry_run and not stopped:
        _push_captures(log_dir, client, summary, out)
        if summary["captures_uploaded"]:
            out(f"Uploaded {summary['captures_uploaded']} raw capture file(s).")

    return summary


def cmd_status(
    config: CloudConfig,
    log_dir: Path,
    client=None,
    out: OutFn = print,
) -> Dict[str, Any]:
    """Report link state, queue counts, and parked sessions."""
    out(f"Endpoint:   {config.endpoint}")
    if config.is_linked():
        out(f"Linked:     yes (device_id={config.device_id})")
        out(f"Enabled:    {'yes' if config.enabled else 'no (uploads paused)'}")
        out(
            "Raw upload: "
            + ("on (raw radar data + IWR6843 L3 dumps)" if config.upload_raw else "off")
        )
    else:
        out("Linked:     no — this device is not linked. Run `openflight-cloud link` to pair it.")

    online = None
    if client is not None and config.is_active():
        online = client.health()
        out(f"Reachable:  {'yes' if online else 'no (cloud unreachable)'}")

    counts = spool.summarize(log_dir)
    out(
        f"Sessions:   {counts['total']} total | {counts['pushed']} pushed | "
        f"{counts['pending']} pending | {counts['parked']} parked"
    )
    if counts["captures_pending"]:
        out(f"Captures:   {counts['captures_pending']} raw capture file(s) waiting to upload")

    parked = _parked_details(log_dir)
    if parked:
        out("Parked:")
        for name, info in parked:
            out(f"  {name}: {info.get('reason')} (last_error={info.get('last_error')})")

    zero_shot = _zero_shot_uploads(log_dir)
    if zero_shot:
        out("Uploaded with 0 shots (re-upload with `openflight-cloud push --retry <name>`):")
        for name in zero_shot:
            out(f"  {name}")

    return {
        "counts": counts,
        "parked": [name for name, _ in parked],
        "zero_shot": zero_shot,
        "linked": config.is_linked(),
        "online": online,
    }


def _read_marker(path: Path, suffix: str) -> dict:
    import json

    marker = path.with_name(path.name + suffix)
    try:
        return json.loads(marker.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def _parked_details(log_dir: Path) -> List:
    return [
        (path.name, _read_marker(path, spool.PARKED_SUFFIX))
        for path in spool.session_files(log_dir)
        if spool.is_parked(path)
    ]


def _zero_shot_uploads(log_dir: Path) -> List[str]:
    """Pushed sessions the server stored with no shots — likely worth re-sending."""
    names = []
    for path in spool.session_files(log_dir):
        if spool.is_pushed(path) and not _read_marker(path, spool.PUSHED_SUFFIX).get("shot_count"):
            names.append(path.name)
    return names
