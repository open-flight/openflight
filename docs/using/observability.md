# Observability & Log Shipping

OpenFlight can ship session logs to Grafana Cloud for long-term storage, querying, and dashboarding. This uses [Grafana Alloy](https://grafana.com/docs/alloy/) to tail JSONL session files and push them to Loki.

## Overview

```
Session Logs (JSONL)  →  Grafana Alloy  →  Grafana Cloud Loki  →  Grafana Dashboards
~/openflight_sessions/     (local agent)       (cloud storage)       (query & visualize)
```

- **What gets shipped**: Every JSONL entry — session start/end, shot data (ball speed, club speed, spin, launch angle, carry), trigger events, and raw I/Q captures
- **Labels**: `app=openflight`, `host=<hostname>`, `log_type` (shot_detected, session_start, etc.), `mode`, `club`
- **Extracted fields**: `ball_speed`, `club_speed`, `carry`, `spin_rpm`, `launch_v`, `launch_h`, `angle_source`, `club_aoa`, `club_path`, `shot_number`
- **Buffering**: Local WAL (write-ahead log) buffers during network outages

## Setup

### Prerequisites

- Raspberry Pi running OpenFlight
- A [Grafana Cloud](https://grafana.com/products/cloud/) account (free tier works)

### 1. Get Grafana Cloud Credentials

1. Log in to [Grafana Cloud](https://grafana.com/)
2. Go to your stack → **Connections** → **Loki**
3. Click **Details** to find:
   - **URL**: Loki push endpoint (e.g., `https://logs-prod-us-central1.grafana.net/loki/api/v1/push`)
   - **User**: Numeric instance ID
4. Generate an **API key** with `logs:write` scope

### 2. Install Alloy

```bash
sudo ./scripts/setup/setup_alloy.sh
```

This script:
- Adds the Grafana APT repository
- Installs Grafana Alloy
- Writes the Alloy config to `/etc/alloy/config.alloy` (from `config/alloy.alloy`)
- Creates a credentials template at `/etc/alloy/credentials.env`
- Configures the systemd service to run as your user (so it can read session logs)
- Enables the Alloy service

### 3. Configure Credentials

```bash
sudo vim /etc/alloy/credentials.env
```

Fill in your Grafana Cloud values:

```env
LOKI_URL=https://logs-prod-us-central1.grafana.net/loki/api/v1/push
LOKI_USER=123456
LOKI_API_KEY=glc_your_api_key_here
```

### 4. Start Alloy

```bash
sudo systemctl start alloy
```

Or just run `scripts/start-kiosk.sh` — it starts Alloy automatically if credentials are configured.

### 5. Verify

Check Alloy is running:

```bash
sudo systemctl status alloy
```

Check for errors:

```bash
sudo journalctl -u alloy -f
```

Then query in Grafana Cloud → Explore → Loki:

```logql
{app="openflight"}
```

## Querying Session Data

### Basic Queries (LogQL)

```logql
# All shot data
{app="openflight", log_type="shot_detected"}

# Shots with a specific club
{app="openflight", log_type="shot_detected", club="driver"}

# Session starts
{app="openflight", log_type="session_start"}

# Filter by ball speed (using JSON parsing)
{app="openflight", log_type="shot_detected"} | json | ball_speed_mph > 150
```

### Metric Queries

```logql
# Average ball speed over time
avg_over_time({app="openflight", log_type="shot_detected"} | json | unwrap ball_speed_mph [1h])

# Shot count per session
count_over_time({app="openflight", log_type="shot_detected"} [24h])

# Spin detection rate (shots with spin > 0)
count_over_time({app="openflight", log_type="shot_detected"} | json | spin_rpm > 0 [24h])

# Processing failures during a session
{app="openflight", log_type="error"}
```

## How It Works

### Session Log Format

OpenFlight writes JSONL files to `~/openflight_sessions/session_<timestamp>_<mode>.jsonl`. Each line is a JSON object with a `type` field:

| Entry Type | Description | Key Fields |
|---|---|---|
| `session_start` | Session began | `mode`, `camera_enabled`, `sample_rate` |
| `session_end` | Session ended | `duration_seconds`, `total_shots` |
| `shot_detected` | A shot was detected | `ball_speed_mph`, `club_speed_mph`, `spin_rpm`, `launch_angle_vertical`, `launch_angle_horizontal`, `angle_source`, `club_angle_deg`, `club_path_deg`, `estimated_carry_yards` |
| `connection` | Device connected | `device`, `port`, `baud`, `firmware`, `radc_available`, `base_freq` |
| `trigger_event` | Trigger accepted/rejected | `accepted`, `latency_ms`, `trigger_type` |
| `rolling_buffer_capture` | Raw I/Q capture | `num_samples`, `sample_rate` |
| `error` | Processing or hardware failure | `error` (message), `context` (`component`, `stage`, `exception_type`, `exception_message`, …) |

### Alloy Pipeline

The config at `config/alloy.alloy` defines a three-stage pipeline:

1. **File Discovery** (`local.file_match`): Watches for `session_*.jsonl` files
2. **JSON Processing** (`loki.process`): Extracts fields as labels and structured metadata, uses the `ts` field as the log timestamp
3. **Loki Push** (`loki.write`): Ships to Grafana Cloud with basic auth and retry on rate-limiting

### Auto-Start

When `scripts/start-kiosk.sh` runs, it checks:
1. Is Alloy installed?
2. Is `/etc/alloy/credentials.env` configured (not empty)?
3. Is the Alloy service already running?

If credentials are configured but Alloy isn't running, it starts the service automatically.

## Troubleshooting

### "Alloy installed but no credentials file found"

Run the setup script:

```bash
sudo ./scripts/setup/setup_alloy.sh
```

Then fill in `/etc/alloy/credentials.env`.

### "Alloy installed but credentials not configured"

The credentials file exists but has empty values. Edit it:

```bash
sudo vim /etc/alloy/credentials.env
```

### 401 Unauthorized

Your API key is invalid or missing the `logs:write` scope. Generate a new key in Grafana Cloud → Loki → Details.

### Permission Denied on Config

```bash
sudo chmod 755 /etc/alloy
sudo chmod 644 /etc/alloy/config.alloy
sudo chmod 600 /etc/alloy/credentials.env
```

### Alloy Won't Start (CHDIR Error)

The data directory may not exist:

```bash
sudo mkdir -p /var/lib/alloy/data
sudo chown -R $(whoami):$(whoami) /var/lib/alloy
```

### No Logs Appearing in Grafana

1. Verify Alloy is running: `sudo systemctl status alloy`
2. Check Alloy logs: `sudo journalctl -u alloy -f`
3. Verify session logs exist: `ls ~/openflight_sessions/`
4. Make sure session logging is enabled (it's on by default, including mock mode)

## Configuration Reference

### Files

| File | Purpose |
|---|---|
| `config/alloy.alloy` | Alloy pipeline config (source of truth) |
| `config/credentials.env.example` | Credentials template |
| `/etc/alloy/config.alloy` | Deployed config (written by setup script) |
| `/etc/alloy/credentials.env` | Deployed credentials (user fills in) |
| `scripts/setup/setup_alloy.sh` | Installation and setup script |

### Environment Variables

| Variable | Description |
|---|---|
| `LOKI_URL` | Grafana Cloud Loki push endpoint |
| `LOKI_USER` | Grafana Cloud instance ID (numeric) |
| `LOKI_API_KEY` | API key with `logs:write` scope |
