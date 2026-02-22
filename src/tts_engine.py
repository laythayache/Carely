"""
Text-to-Speech engine using Piper.
Runs Piper as a subprocess, streams PCM output to PipeWire,
extracts amplitude for UI sync, supports barge-in cancellation.
"""

import asyncio
import logging
import struct
import time
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# Piper outputs 22050Hz mono S16LE by default
PIPER_SAMPLE_RATE = 22050
PIPER_CHANNELS = 1
AMPLITUDE_CHUNK_SAMPLES = 441  # ~20ms at 22050Hz


class TTSEngine:
    """
    Synthesizes speech using Piper and plays it through PipeWire.
    """

    def __init__(
        self,
        binary_path: str,
        voice_paths: dict[str, str],
        default_language: str = "en",
    ):
        self.binary_path = binary_path
        self.voice_paths = voice_paths  # {"en": "/path/to.onnx", "ar": "/path/to.onnx"}
        self.default_language = default_language
        self._current_process: asyncio.subprocess.Process | None = None
        self._playback_process: asyncio.subprocess.Process | None = None

    async def speak(
        self,
        text: str,
        language: str = "",
        cancel_event: asyncio.Event | None = None,
        amplitude_callback: Callable[[float], None] | None = None,
    ) -> None:
        """
        Synthesize and play text.

        Args:
            text: Text to speak
            language: "en" or "ar". Falls back to default.
            cancel_event: Set to stop playback (barge-in)
            amplitude_callback: Called with RMS amplitude per ~20ms chunk
        """
        lang = language if language in self.voice_paths else self.default_language
        voice_path = self.voice_paths[lang]
        config_path = voice_path + ".json"

        logger.info(f"TTS: Speaking [{lang}] '{text[:80]}...' " if len(text) > 80 else f"TTS: Speaking [{lang}] '{text}'")

        start_time = time.monotonic()

        # Build Piper command: outputs raw PCM to stdout
        piper_cmd = [
            self.binary_path,
            "--model", voice_path,
            "--config", config_path,
            "--output-raw",
        ]

        # Build playback command: pw-play reads raw PCM from stdin
        play_cmd = [
            "pw-play",
            "--rate", str(PIPER_SAMPLE_RATE),
            "--channels", str(PIPER_CHANNELS),
            "--format", "s16",
            "-",
        ]

        try:
            # Start Piper (text → raw PCM)
            self._current_process = await asyncio.create_subprocess_exec(
                *piper_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Start pw-play (raw PCM → speaker)
            self._playback_process = await asyncio.create_subprocess_exec(
                *play_cmd,
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Feed text to Piper
            self._current_process.stdin.write(text.encode("utf-8"))
            self._current_process.stdin.close()

            # Stream PCM from Piper stdout → pw-play stdin + amplitude extraction
            chunk_size = AMPLITUDE_CHUNK_SAMPLES * 2  # 2 bytes per int16 sample

            while True:
                # Check cancellation
                if cancel_event and cancel_event.is_set():
                    logger.info("TTS: Barge-in, stopping playback")
                    break

                chunk = await self._current_process.stdout.read(chunk_size)
                if not chunk:
                    break

                # Send to playback
                try:
                    self._playback_process.stdin.write(chunk)
                    await self._playback_process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    break

                # Extract amplitude
                if amplitude_callback and len(chunk) >= 4:
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2)) / 32768.0
                    amplitude_callback(float(rms))

            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(f"TTS: Playback complete ({latency_ms}ms)")

        except asyncio.CancelledError:
            logger.info("TTS: Cancelled")
            raise

        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Immediately stop TTS playback (barge-in)."""
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Kill Piper and playback processes."""
        for proc_name, proc in [
            ("piper", self._current_process),
            ("pw-play", self._playback_process),
        ]:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass

        self._current_process = None
        self._playback_process = None
