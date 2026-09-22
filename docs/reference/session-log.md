---
icon: lucide/file-json
---

# Session Log Schema

Every session writes a JSON Lines file to
`~/openflight_sessions/session_<timestamp>.jsonl` (override with `--log-dir`,
disable with `--no-logging`).

One JSON object per line. Every entry carries at least:

```json
{"ts": "2026-08-23T14:31:07.882431", "type": "shot_detected", "...": "..."}
```

| Field | Meaning |
| --- | --- |
| `ts` | ISO 8601 local timestamp |
| `type` | Entry type, from the table below |

Everything else is type-specific and merged into the same object — entries are
flat, not nested under a payload key.

## Entry types

Written by `src/openflight/session_logger.py`.

### Session lifecycle

| Type | Written when |
| --- | --- |
| `session_start` | Session opens. Carries the session metadata block. |
| `session_end` | Session closes. Carries the summary. |
| `connection` | A radar connects — port, firmware, negotiated baud. |
| `ops_clock_sync` | OPS243 clock synchronisation summary. |
| `config_change` | Runtime configuration changed, with its source. |
| `error` | An error, with optional context. |

### Shots and readings

| Type | Written when |
| --- | --- |
| `shot_detected` | A shot is measured. Ball speed, club speed, spin, angles, carry. |

### Capture data

| Type | Written when |
| --- | --- |
| `trigger_event` | Trigger accepted or rejected, with latency. |
| `rolling_buffer_capture` | Raw OPS243 I/Q samples — 4,096 each. |
| `camera_capture` | High-speed camera clip metadata and its correlated shot. |
| `iwr6843_capture` | IWR6843 L3 dump for a shot. |
| `kld7_buffer` | K-LD7 frame timing and selected angle metadata (deprecated hardware). |

### Outbound and system

| Type | Written when |
| --- | --- |
| `sim_send` | A shot was forwarded to a simulator. |
| `sim_status` | Simulator connection status changed. |
| `sim_player` | Player state — target, handedness, club. |
| `power_status` | Battery and external-power status. |

## Size

Raw capture entries dominate. A session with a few hundred shots produces a
file in the tens of megabytes because `rolling_buffer_capture` stores 8,192
samples per shot.

This is deliberate — those captures are what make offline estimator work
possible without re-hitting balls. See
[spin replay](../development/spin-replay.md).

[Cloud sync](../using/cloud-sync.md) strips the raw ADC entries before upload;
that filtering is **required** by the wire contract, not optional — see the
[cloud uploader spec](cloud-uploader-spec.md).

## Querying

Locally, `jq` is usually enough:

```bash
# Every shot's ball speed
jq -r 'select(.type=="shot_detected") | .ball_speed' session_*.jsonl

# Trigger rejects and why
jq -r 'select(.type=="trigger_event" and .accepted==false)' session_*.jsonl
```

For LogQL queries against shipped logs, see
[observability](../using/observability.md#querying-session-data).

## Related

- [Observability & log shipping](../using/observability.md)
- [Cloud uploader contract](cloud-uploader-spec.md)
