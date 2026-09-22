---
icon: lucide/file-cog
---

# Configuration Files

Settings that live in files rather than on the command line.

## `config/sim.json`

Simulator connectors. Not tracked in git — copy the example and edit:

```bash
cp config/sim.example.json config/sim.json
```

```json
{
  "connectors": [
    {
      "type": "opengolfsim",
      "enabled": false,
      "host": "127.0.0.1",
      "port": 3111,
      "device_id": "OpenFlight"
    },
    {
      "type": "gspro",
      "enabled": false,
      "host": "127.0.0.1",
      "port": 921,
      "device_id": "OpenFlight",
      "units": "Yards",
      "heartbeat_interval_s": 5
    }
  ]
}
```

| Key | Meaning |
| --- | --- |
| `type` | Connector to use — `opengolfsim`, `gspro`, `partee` |
| `enabled` | Whether this connector is active |
| `host`, `port` | Where the simulator is listening |
| `device_id` | Identifier the simulator displays |
| `units` | `Yards` or `Meters` (GSPro) |
| `heartbeat_interval_s` | Keepalive cadence (GSPro) |

Enable connectors at runtime with `--sim`. See
[simulator connectors](../using/simulator/index.md).

## `~/.config/openflight/cloud.json`

Cloud sync credentials and endpoint. Mode `0600` — it holds a device token.

Created by the device-linking flow rather than by hand; see
[cloud sync](../using/cloud-sync.md#linking-a-device) and the
[wire contract](cloud-uploader-spec.md#6-config-configopenflightcloudjson-mode-0600).

## IWR6843 radar configs

`config/iwr6843_l3dump_*.cfg` — TI RF configuration matching the flashed L3
firmware. Selected with `--iwr6843-config`.

| File | Profile |
| --- | --- |
| `iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | Wide, 24 frames × 3 ms, 53 bins, IQ16. **Default.** |
| `iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` | Dense, 36 frames × 2 ms, 53 bins, IQ8 |
| `iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` | Experimental dense IQ8 profile that keeps the wide profile's late-flight window |

All three work with the same flashed image — the profile is selected at runtime, not
at flash time. See
[choosing a profile](../iwr6843/index.md#choose-a-profile).

## `config/iwr6843_calibration_reference.json`

Per-board complex array and range calibration. Selected with `--iwr6843-cal`.

| Key | Meaning |
| --- | --- |
| `range_scale`, `range_offset_m`, `range_bias_const_m` | Range correction |
| `elem_phase_rad` | Per-element phase correction, 8 elements |
| `elem_gain` | Per-element gain correction, 8 elements |
| `convention` | How the correction is applied to the array |

The shipped file is a **validated starting point**, not a guarantee — per-board
calibration may be required. Mount tilt and antenna height also live here and
can be overridden with `--iwr6843-tilt-deg` and `--iwr6843-radar-height-m`.

## `config/alloy.alloy`

Grafana Alloy pipeline for shipping session logs. See
[observability](../using/observability.md).

## `config/credentials.env.example`

Template for Grafana Cloud credentials. Copy to `credentials.env` — which is
gitignored — and fill in.

## Related

- [CLI flags](cli.md)
- [Constants](constants.md)
