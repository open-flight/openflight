# Optional GPIO shot-confirmation buzzer

OpenFlight supports an optional active-low GPIO buzzer that sounds
two short beeps after an accepted, non-mock shot.

The feature is disabled by default and does not require a buzzer
for normal OpenFlight operation.

## Hardware

Tested hardware: HALJIA active-low trigger buzzer module with a
Raspberry Pi 4.

Example connections:

| Buzzer connection | Raspberry Pi |
| --- | --- |
| Signal / IN | GPIO27, physical pin 13 |
| GND | Ground, physical pin 14 |
| VCC | Compatible power supply pin |

Check the voltage requirements and pin labels of your specific
module before connecting it. Do not connect a bare high-current
buzzer directly to a GPIO pin.

GPIO numbering in the software uses BCM numbering.

## Enable

Set this environment variable in the OpenFlight server environment:

    OPENFLIGHT_BUZZER=1

For a manually launched server:

    OPENFLIGHT_BUZZER=1 openflight-server --web-port 8080

Without the environment variable, the GPIO buzzer is not initialised.

## Behaviour

- Two 100 ms beeps separated by 100 ms
- Triggered when an accepted non-mock shot reaches the server callback
- Runs on a daemon thread to avoid waiting during shot processing
- No beep merely because the sound detector was triggered
- GPIO failures are logged without preventing OpenFlight startup

This confirms shot acceptance, not completion of all optional
camera enrichment.

An unused rolling-buffer warning method is available in the
controller but is not enabled by this integration.

## Limitations

The active-low module switches on and off; software volume control
is not provided. Startup sounds occurring before OpenFlight initialises
GPIO may require separate boot-level configuration.
