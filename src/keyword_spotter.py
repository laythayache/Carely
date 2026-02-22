"""
Always-on emergency keyword spotter using openWakeWord.
Runs in a dedicated thread, processes audio from the C++ pipeline.
Detects emergency phrases and emits EMERGENCY_KEYWORD events.
"""

import asyncio
import logging
import threading
import time
import numpy as np
from typing import Any

logger = logging.getLogger(__name__)

# Minimum time between emergency triggers to avoid rapid re-triggering
DEBOUNCE_SECONDS = 5.0


class KeywordSpotter:
    """
    Continuously processes audio to detect emergency keywords.
    Uses openWakeWord for lightweight, always-on inference (~3-5% CPU).
    """

    def __init__(
        self,
        event_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        threshold: float = 0.7,
        enabled: bool = True,
    ):
        self.event_queue = event_queue
        self.loop = loop
        self.threshold = threshold
        self.enabled = enabled
        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False  # Pause during STT to save CPU
        self._model = None
        self._last_trigger_time = 0.0

        # Audio buffer for feeding to the model
        self._audio_buffer = bytearray()
        self._buffer_lock = threading.Lock()

    def start(self) -> None:
        """Initialize the model and start the detection thread."""
        if not self.enabled:
            logger.info("Keyword spotter disabled by config")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="keyword-spotter"
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the detection thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def pause(self) -> None:
        """Pause detection (e.g. during STT to free CPU)."""
        self._paused = True

    def resume(self) -> None:
        """Resume detection after pause."""
        self._paused = False

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Feed audio data from the audio bridge (thread-safe)."""
        if not self._running or self._paused:
            return
        with self._buffer_lock:
            self._audio_buffer.extend(pcm_bytes)
            # Cap buffer at 2 seconds (64KB)
            if len(self._audio_buffer) > 64000:
                self._audio_buffer = self._audio_buffer[-64000:]

    def _run(self) -> None:
        """Thread target: initialize model and run detection loop."""
        try:
            import openwakeword
            from openwakeword.model import Model

            # Initialize with available models
            # openWakeWord ships with several pre-trained models
            # We use generic ones and may train custom "help"/"emergency" later
            self._model = Model(inference_framework="onnx")
            logger.info(
                f"Keyword spotter started "
                f"(threshold={self.threshold}, models={list(self._model.models.keys())})"
            )

        except ImportError:
            logger.warning("openwakeword not installed, keyword spotter disabled")
            return
        except Exception:
            logger.exception("Failed to initialize keyword spotter")
            return

        # Detection loop: process 80ms chunks
        chunk_size = 1280 * 2  # 80ms at 16kHz, 2 bytes per sample

        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            # Get audio chunk
            chunk = None
            with self._buffer_lock:
                if len(self._audio_buffer) >= chunk_size:
                    chunk = bytes(self._audio_buffer[:chunk_size])
                    self._audio_buffer = self._audio_buffer[chunk_size:]

            if chunk is None:
                time.sleep(0.02)  # Wait for more audio
                continue

            try:
                # Convert bytes to numpy array
                audio_array = np.frombuffer(chunk, dtype=np.int16)

                # Run prediction
                predictions = self._model.predict(audio_array)

                # Check for triggers
                for keyword, score in predictions.items():
                    if score >= self.threshold:
                        now = time.monotonic()
                        if now - self._last_trigger_time >= DEBOUNCE_SECONDS:
                            self._last_trigger_time = now
                            logger.warning(
                                f"Emergency keyword detected: '{keyword}' "
                                f"(score={score:.3f})"
                            )
                            self._emit_event(keyword, score)

            except Exception:
                logger.exception("Keyword spotter inference error")
                time.sleep(0.1)

    def _emit_event(self, keyword: str, score: float) -> None:
        """Thread-safe emit to the asyncio event queue."""
        try:
            self.loop.call_soon_threadsafe(
                self.event_queue.put_nowait,
                ("emergency_keyword", {"keyword": keyword, "score": score}),
            )
        except RuntimeError:
            pass
