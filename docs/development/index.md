---
icon: lucide/code
---

# Development

Working on OpenFlight itself, plus the experimental and validation work that
is not part of the production path.

<div class="grid cards" markdown>

- :material-hand-heart: **[Help needed](help-needed.md)**

    Testing and development work that would help most right now.

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

!!! info "Help needed"

    Testing the IWR6843 self-trigger, TrackMan 4 comparison sessions, cheaper
    OPS243 firmware updates, newer OPS firmware features, the new radar front,
    radome tests, automatic setup measurement, camera sensor fusion and the
    mobile app: see
    **[Help Needed](help-needed.md)**.

Quick reference:

```bash
uv run pytest tests/ -v                        # tests
uv run pylint src/openflight/ --fail-under=9   # lint (must score 9.0+)
uv run ruff check src/openflight/              # format check
make docs-build                                # build these docs, strict
```

Always use `uv` for Python commands — never bare `python`, `pip`, or `pytest`.
