# IWR6843 Firmware

The firmware developer guide now lives in the documentation site:

**→ [docs/development/firmware.md](../docs/development/firmware.md)**
(published at <https://openflight.dev/docs/development/firmware/>)

It covers building the configurable image from source, the Docker toolchain,
the L3 raw-dump format, and the flashing procedure.

You do not need to build from source to use OpenFlight — a validated prebuilt
image ships in [`releases/`](releases/), and the
[IWR6843 Operator Guide](../docs/iwr6843/index.md) walks through flashing it.

## What's in this directory

| Path | Contents |
| --- | --- |
| `iwr6843/` | Firmware sources |
| `releases/` | Prebuilt, validated firmware images |
| `flash_iwr6843.py` | Flashing tool |
| `Dockerfile`, `Makefile` | Build toolchain |
