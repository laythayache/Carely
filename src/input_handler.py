"""
Carely keyboard input handler.
Reads keyboard events via pynput, maps configured keys to FSM events.
Detects short press vs long press (>1s hold) and emergency key.
"""

import asyncio
import logging
import time
import threading
from typing import Any

logger = logging.getLogger(__name__)


class InputHandler:
    """
    Listens for keyboard events and posts them to the event queue.

    Events emitted:
    - ("button", "press")       — short press of the listen key
    - ("button", "long_press")  — hold >LONG_PRESS_MS of the listen key
    - ("emergency_key", {})     — emergency key pressed
    """

    def __init__(
        self,
        event_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        button_key: str = "space",
        emergency_key: str = "f12",
        long_press_ms: int = 1000,
    ):
        self.event_queue = event_queue
        self.loop = loop
        self.button_key = button_key
        self.emergency_key = emergency_key
        self.long_press_ms = long_press_ms
        self._press_time: float | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start listening for keyboard events in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="input-handler")
        self._thread.start()
        logger.info(f"Input handler started (button={self.button_key}, emergency={self.emergency_key})")

    def stop(self) -> None:
        """Stop the keyboard listener."""
        self._running = False
        logger.info("Input handler stopped")

    def _run(self) -> None:
        """Thread target: run pynput keyboard listener."""
        try:
            from pynput import keyboard

            def _resolve_key(key_name: str):
                """Convert a key name string to a pynput Key or KeyCode."""
                # Check if it's a special key (space, f12, etc.)
                special = getattr(keyboard.Key, key_name, None)
                if special is not None:
                    return special
                # Single character key
                if len(key_name) == 1:
                    return keyboard.KeyCode.from_char(key_name)
                return None

            listen_key = _resolve_key(self.button_key)
            emerg_key = _resolve_key(self.emergency_key)

            if listen_key is None:
                logger.error(f"Cannot resolve button key: {self.button_key}")
                return
            if emerg_key is None:
                logger.error(f"Cannot resolve emergency key: {self.emergency_key}")
                return

            def on_press(key):
                if not self._running:
                    return False
                try:
                    if key == listen_key and self._press_time is None:
                        self._press_time = time.monotonic()
                    elif key == emerg_key:
                        self._emit("emergency_key", {})
                except Exception:
                    logger.exception("Error in on_press")

            def on_release(key):
                if not self._running:
                    return False
                try:
                    if key == listen_key and self._press_time is not None:
                        duration_ms = (time.monotonic() - self._press_time) * 1000
                        self._press_time = None

                        if duration_ms >= self.long_press_ms:
                            self._emit("button", "long_press")
                        else:
                            self._emit("button", "press")
                except Exception:
                    logger.exception("Error in on_release")

            with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()

        except ImportError:
            logger.error("pynput not installed. Keyboard input disabled.")
        except Exception:
            logger.exception("Input handler thread crashed")

    def _emit(self, event_type: str, data: Any) -> None:
        """Thread-safe emit to the asyncio event queue."""
        try:
            self.loop.call_soon_threadsafe(
                self.event_queue.put_nowait, (event_type, data)
            )
        except RuntimeError:
            pass  # Event loop closed
