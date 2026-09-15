---
icon: lucide/circle-play
---

# Using OpenFlight

Everything after the hardware works.

<div class="grid cards" markdown>

- :material-golf: **[Simulator connectors](simulator/index.md)**

    Stream shots to [GSPro](simulator/gspro.md),
    [OpenGolfSim](simulator/opengolfsim.md), [PAR-TEE](simulator/partee.md),
    E6, and Garmin.

- :material-speedometer: **[Swing speed training](swing-speed.md)**

    Club-only mode for air swings and speed sticks. No ball strike, no sound
    trigger.

- :material-cloud-upload-outline: **[Cloud sync](cloud-sync.md)**

    Push filtered sessions to FlightWeb, with spool-and-retry over flaky wifi.

- :material-chart-line: **[Observability](observability.md)**

    Ship session logs to Grafana Cloud and query them with LogQL.

- :material-battery-charging: **[Battery monitoring](battery.md)**

    Provider architecture, indicator states, and warning dialogs.

</div>

## Starting the system

The default entry point handles venv activation, the UI build, and the server
launch:

```bash
scripts/start-kiosk.sh
```

See [auto-start & kiosk mode](../setup/auto-start.md) for running it as a
systemd service on boot, and for the manual and over-SSH variants.
