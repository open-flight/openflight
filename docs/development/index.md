---
icon: lucide/code
---

# Development

Working on OpenFlight itself, plus the experimental and validation work that
is not part of the production path.

<div class="grid cards" markdown>

- :material-chip: **[Firmware build](firmware.md)**

    Build the IWR6843 configurable image from source. Not needed to flash the
    prebuilt release.

- :material-sine-wave: **[Spin replay bench](spin-replay.md)**

    The dechirped-sideband spin estimator test bench.

- :material-camera-outline: **[Camera & YOLO](camera-yolo.md)**

    Experimental vision work. Disabled in the production kiosk.

</div>

## Contributing

See [`CONTRIBUTING.md`](https://github.com/open-flight/openflight/blob/main/CONTRIBUTING.md)
in the repository for development setup, code quality standards, and the pull
request process.

!!! info "Testers wanted: IWR6843 self-trigger firmware"

    Experimental IWR6843 firmware that triggers on the shot by itself needs
    testing on real OpenFlight rigs. If you have a working build with an
    IWR6843, say so on the [OpenFlight Discord](https://discord.gg/w8hhG4WVMN).

Quick reference:

```bash
uv run pytest tests/ -v                        # tests
uv run pylint src/openflight/ --fail-under=9   # lint (must score 9.0+)
uv run ruff check src/openflight/              # format check
make docs-build                                # build these docs, strict
```

Always use `uv` for Python commands — never bare `python`, `pip`, or `pytest`.
