---
icon: lucide/settings
---

# Setup

Software configuration, once the hardware is wired.

<div class="grid cards" markdown>

- :material-raspberry-pi: **[Raspberry Pi setup](raspberry-pi.md)**

    OS, dependencies, the setup script, and what it configures.

- :material-content-save-outline: **[Rolling buffer setup](rolling-buffer.md)**

    The one-time flash-persist step. Hardware triggers do not work without it.

- :material-power: **[Auto-start & kiosk mode](auto-start.md)**

    Run as a systemd service on boot; fullscreen, manual, and SSH variants.

- :material-stethoscope: **[Hardware diagnostic](diagnostic.md)**

    Seven checks over the whole signal path. Run this first when something
    breaks.

</div>

## Order

1. [Raspberry Pi setup](raspberry-pi.md) — get the software running
2. [Rolling buffer setup](rolling-buffer.md) — persist the radar mode
3. [Hardware diagnostic](diagnostic.md) — confirm the path end to end
4. [Auto-start & kiosk mode](auto-start.md) — make it survive a reboot

!!! tip "Run the diagnostic before anything else"

    ```bash
    uv run python scripts/hardware-test/diagnose.py
    ```

    It tells you which link in the chain is broken instead of leaving you to
    infer it from a UI that shows nothing.
