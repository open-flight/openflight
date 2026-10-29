"""Optional GPIO buzzer feedback for OpenFlight."""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class BuzzerController:
    """Control an active-low buzzer without blocking shot processing."""

    def __init__(self, pin=27):
        self.pin = pin
        self._device = None
        self._lock = threading.Lock()
        self._warning_stop = threading.Event()
        self._warning_thread = None

        try:
            from gpiozero import OutputDevice

            self._device = OutputDevice(
                pin,
                active_high=False,
                initial_value=False,
            )
            logger.info("Buzzer ready on GPIO%d", pin)
        except Exception as exc:
            logger.warning("Buzzer unavailable: %s", exc)

    def _beep(self, duration=0.1):
        if self._device is None:
            return

        with self._lock:
            try:
                self._device.on()
                time.sleep(duration)
            finally:
                self._device.off()

    def confirmed_shot(self):
        """Two short beeps after a shot is accepted."""
        if self._device is None:
            return

        def play():
            self._beep(0.1)
            time.sleep(0.1)
            self._beep(0.1)

        threading.Thread(target=play, daemon=True).start()

    def start_rolling_warning(self):
        """Start one beep every two seconds until cleared."""
        if self._device is None:
            return

        if self._warning_thread and self._warning_thread.is_alive():
            return

        self._warning_stop.clear()

        def warn():
            while not self._warning_stop.is_set():
                self._beep(0.1)
                if self._warning_stop.wait(1.9):
                    break

        self._warning_thread = threading.Thread(
            target=warn,
            daemon=True,
        )
        self._warning_thread.start()

    def stop_rolling_warning(self):
        """Silence the rolling-buffer warning."""
        self._warning_stop.set()

    def close(self):
        self.stop_rolling_warning()

        if self._warning_thread:
            self._warning_thread.join(timeout=1)

        if self._device:
            with self._lock:
                self._device.off()
                self._device.close()
            self._device = None
