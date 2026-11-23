"""Tests for optional GPIO shot feedback."""

import sys
import threading
from types import SimpleNamespace

from openflight.buzzer import BuzzerController


def test_confirmed_shot_beeps_twice(monkeypatch):
    events = []
    finished = threading.Event()

    class FakeOutputDevice:
        def __init__(self, pin, active_high, initial_value):
            assert pin == 27
            assert active_high is False
            assert initial_value is False

        def on(self):
            events.append("on")

        def off(self):
            events.append("off")
            if len(events) == 4:
                finished.set()

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "gpiozero",
        SimpleNamespace(OutputDevice=FakeOutputDevice),
    )

    buzzer = BuzzerController(pin=27)
    buzzer.confirmed_shot()

    assert finished.wait(2), "Double beep did not complete"
    assert events == ["on", "off", "on", "off"]
    buzzer.close()


def test_unavailable_buzzer_does_not_block_shots(monkeypatch):
    class FailingOutputDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("GPIO unavailable")

    monkeypatch.setitem(
        sys.modules,
        "gpiozero",
        SimpleNamespace(OutputDevice=FailingOutputDevice),
    )

    buzzer = BuzzerController()
    assert buzzer._device is None
    buzzer.confirmed_shot()
    buzzer.close()
