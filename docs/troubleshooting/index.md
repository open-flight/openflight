---
icon: lucide/wrench
---

# Troubleshooting

**Start here, then follow the link.** This page routes by *what you are seeing*.
The actual fixes live next to the guide they belong to, because the same symptom
means different things depending on which subsystem produced it.

!!! tip "Run the diagnostic first"

    ```bash
    uv run python scripts/hardware-test/diagnose.py
    ```

    Seven checks across the whole signal path, each naming the link that broke.
    It will usually answer the question faster than this page.
    See [hardware diagnostic](../setup/diagnostic.md).

## Nothing happens when I hit a ball

| What you see | Go to |
| --- | --- |
| No trigger at all; GATE LED does not flash | [Sound trigger § No trigger received](../build/sound-trigger.md#no-trigger-received) |
| GATE LED stays lit constantly | [Sound trigger § GATE LED stays on](../build/sound-trigger.md#gate-led-stays-on-stuck-high) |
| Triggers fire but no I/Q data arrives | [Sound trigger § Triggers but no I/Q data](../build/sound-trigger.md#triggers-but-no-iq-data) |
| Triggers constantly, even without a strike | [Sound trigger § Too sensitive](../build/sound-trigger.md#triggers-constantly-too-sensitive) |
| Diagnostic says "CW mode, not rolling buffer" | [Rolling buffer setup](../setup/rolling-buffer.md) — re-run and power-cycle |
| Radar not detected at all | [Pi setup § Radar not detected](../setup/raspberry-pi.md#radar-not-detected) |

## Speeds are wrong or missing

| What you see | Go to |
| --- | --- |
| Captures truncate or parse-fail intermittently | [UART § Troubleshooting](../build/ops243-uart.md#troubleshooting) — dropped bytes; try `--ops-baud 115200` |
| Connects, but at 19,200 baud | [UART § Troubleshooting](../build/ops243-uart.md#troubleshooting) — Pi pin 8 → J3 pin 6 |
| Ball speed reads low | [Radar positioning](../how-it-works/positioning.md#ops243-a) — cosine error from off-axis placement |
| Spin looks implausible | [Rolling buffer & spin](../how-it-works/rolling-buffer.md) — spin is experimental by design |

## Angles are wrong or missing

| What you see | Go to |
| --- | --- |
| Launch angle reads zero or absent | [IWR6843 § Verify the first capture](../iwr6843/verify.md#verify-the-first-capture) |
| Angles present but implausible | [IWR6843 § Measure the geometry](../iwr6843/mounting.md#measure-the-geometry) — wrong geometry fails silently |
| Low-confidence angles on many shots | [Low-confidence recovery](../iwr6843/low-confidence-recovery.md) |
| Club path looks wrong | [Club path § Set the target-line reference](../iwr6843/club-path.md#set-the-target-line-reference) |
| Radar does not enumerate or dump | [IWR6843 troubleshooting](../iwr6843/troubleshooting.md) |
| Estimator limits unclear | [Calibration § Estimator limitations](../iwr6843/calibration.md#launch-angle-estimator-limitations) |

## Tilt and inclinometer

| What you see | Go to |
| --- | --- |
| Green LED on, but `0x18` missing from `i2cdetect` | [Inclinometer § `0x18` is missing](../build/inclinometer.md#green-led-is-on-but-0x18-is-missing) |
| `WHO_AM_I expected 0x33` | [Inclinometer § WHO_AM_I](../build/inclinometer.md#who_am_i-expected-0x33) |
| Permission denied on `/dev/i2c-1` | [Inclinometer § Permission denied](../build/inclinometer.md#permission-denied-on-devi2c-1) |
| Pitch sign is backward | [Inclinometer § Pitch sign is backward](../build/inclinometer.md#pitch-sign-is-backward) |
| Shots report `moving` or `stale` | [Inclinometer § Noisy pitch](../build/inclinometer.md#pitch-is-noisy-or-shots-report-moving) |
| Pi reboots or shows a black screen | [Inclinometer § Pi reboots](../build/inclinometer.md#pi-reboots-shows-a-black-screen-or-runs-the-fan-at-full-speed) |

## Battery and power

| What you see | Go to |
| --- | --- |
| OpenFlight shows a red `--` | [Battery § Red `--`](../build/battery.md#openflight-shows-a-red-) |
| Pi taskbar says 0% | [Battery § Taskbar says 0%](../build/battery.md#raspberry-pis-taskbar-says-0) |
| LEDs and percentage disagree | [Battery § LEDs disagree](../build/battery.md#leds-and-percentage-disagree) |
| External power state backwards or stuck | [Battery § External power](../build/battery.md#external-power-state-is-backwards-or-stuck) |
| Want to undo the Pi changes | [Battery § Roll back](../build/battery.md#roll-back-pi-changes) |

## Software and UI

| What you see | Go to |
| --- | --- |
| Service will not start | [Pi setup § Service won't start](../setup/raspberry-pi.md#service-wont-start) |
| Slow UI updates | [Pi setup § Slow UI updates](../setup/raspberry-pi.md#slow-ui-updates) |
| Display issues over SSH | [Pi setup § Display over SSH](../setup/raspberry-pi.md#display-issues-over-ssh) |
| Shots not reaching the simulator | [Simulator connectors](../using/simulator/index.md) |
| Cloud uploads stuck or parked | [Cloud sync § Troubleshooting](../using/cloud-sync.md#troubleshooting) |
| Logs not arriving in Grafana | [Observability § Troubleshooting](../using/observability.md#troubleshooting) |

## Deprecated K-LD7 hardware

| What you see | Go to |
| --- | --- |
| Any K-LD7 issue | [Legacy K-LD7 troubleshooting](../legacy/troubleshooting.md) |
| K-LD7 not connecting | [Pi setup § K-LD7 not connecting](../setup/raspberry-pi.md#k-ld7-not-connecting) |

## Still stuck

Collect a session log and the diagnostic output, then open an issue at
[github.com/jewbetcha/openflight/issues](https://github.com/jewbetcha/openflight/issues).
