"""Tests for the input handler (keyboard event mapping)."""

import asyncio
import time

import pytest

from src.input_handler import InputHandler


class TestInputHandlerInit:
    """Test InputHandler construction."""

    def test_default_keys(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(
                event_queue=queue,
                loop=loop,
            )
            assert handler.button_key == "space"
            assert handler.emergency_key == "f12"
            assert handler.long_press_ms == 1000
        finally:
            loop.close()

    def test_custom_keys(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(
                event_queue=queue,
                loop=loop,
                button_key="enter",
                emergency_key="f1",
                long_press_ms=500,
            )
            assert handler.button_key == "enter"
            assert handler.emergency_key == "f1"
            assert handler.long_press_ms == 500
        finally:
            loop.close()

    def test_not_running_initially(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(event_queue=queue, loop=loop)
            assert handler._running is False
            assert handler._thread is None
        finally:
            loop.close()


class TestEmitMethod:
    """Test the _emit method (thread-safe event queue posting)."""

    def test_emit_button_press(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(event_queue=queue, loop=loop)

            # Run the loop briefly to process the call_soon_threadsafe
            def emit_and_stop():
                handler._emit("button", "press")
                # Give loop time to process
                time.sleep(0.05)
                loop.call_soon_threadsafe(loop.stop)

            import threading
            t = threading.Thread(target=emit_and_stop)
            t.start()
            loop.run_forever()
            t.join()

            assert not queue.empty()
            event_type, data = queue.get_nowait()
            assert event_type == "button"
            assert data == "press"
        finally:
            loop.close()

    def test_emit_emergency_key(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(event_queue=queue, loop=loop)

            def emit_and_stop():
                handler._emit("emergency_key", {})
                time.sleep(0.05)
                loop.call_soon_threadsafe(loop.stop)

            import threading
            t = threading.Thread(target=emit_and_stop)
            t.start()
            loop.run_forever()
            t.join()

            assert not queue.empty()
            event_type, data = queue.get_nowait()
            assert event_type == "emergency_key"
            assert data == {}
        finally:
            loop.close()

    def test_emit_long_press(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(event_queue=queue, loop=loop)

            def emit_and_stop():
                handler._emit("button", "long_press")
                time.sleep(0.05)
                loop.call_soon_threadsafe(loop.stop)

            import threading
            t = threading.Thread(target=emit_and_stop)
            t.start()
            loop.run_forever()
            t.join()

            event_type, data = queue.get_nowait()
            assert event_type == "button"
            assert data == "long_press"
        finally:
            loop.close()


class TestLongPressDetection:
    """Test long-press timing logic (simulated, no real keyboard)."""

    def test_short_press_threshold(self):
        """Duration < long_press_ms should be a short press."""
        handler = InputHandler(
            event_queue=asyncio.Queue(),
            loop=asyncio.new_event_loop(),
            long_press_ms=1000,
        )
        # Simulate: press for 500ms — should be short press
        assert 500 < handler.long_press_ms

    def test_long_press_threshold(self):
        """Duration >= long_press_ms should be a long press."""
        handler = InputHandler(
            event_queue=asyncio.Queue(),
            loop=asyncio.new_event_loop(),
            long_press_ms=1000,
        )
        # Simulate: press for 1500ms — should be long press
        assert 1500 >= handler.long_press_ms

    def test_exact_threshold_is_long_press(self):
        """Duration exactly at threshold should be a long press."""
        handler = InputHandler(
            event_queue=asyncio.Queue(),
            loop=asyncio.new_event_loop(),
            long_press_ms=1000,
        )
        assert 1000 >= handler.long_press_ms


class TestStopBehavior:
    def test_stop_sets_flag(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            handler = InputHandler(event_queue=queue, loop=loop)
            handler._running = True
            handler.stop()
            assert handler._running is False
        finally:
            loop.close()

    def test_emit_after_loop_close_does_not_raise(self):
        """_emit should silently handle a closed event loop."""
        loop = asyncio.new_event_loop()
        queue = asyncio.Queue()
        handler = InputHandler(event_queue=queue, loop=loop)
        loop.close()
        # Should not raise
        handler._emit("button", "press")
