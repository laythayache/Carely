"""
Speech-to-Text engine using whisper.cpp.
Runs whisper.cpp as a subprocess, parses JSON output.
Supports cancellation and language auto-detection.
"""

import asyncio
import json
import logging
import struct
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # int16


@dataclass
class STTResult:
    text: str
    language: str  # "en", "ar", etc.
    language_confidence: float  # 0.0-1.0
    latency_ms: int
    segments: list[dict]


class STTEngine:
    """
    Wraps whisper.cpp CLI for speech-to-text transcription.
    Uses 3 threads on i3-9100T (leaves 1 core for audio pipeline).
    """

    def __init__(
        self,
        binary_path: str,
        model_path: str,
        language: str = "auto",
        threads: int = 3,
    ):
        self.binary_path = binary_path
        self.model_path = model_path
        self.language = language
        self.threads = threads

    async def transcribe(
        self, pcm_data: bytes, cancel_event: asyncio.Event | None = None
    ) -> STTResult:
        """
        Transcribe PCM audio data (16kHz mono int16).

        Args:
            pcm_data: Raw PCM bytes (int16 LE samples)
            cancel_event: Set this to cancel transcription

        Returns:
            STTResult with transcription text, detected language, and latency.

        Raises:
            asyncio.CancelledError: If cancel_event is set
            RuntimeError: If whisper.cpp fails
        """
        start_time = time.monotonic()

        # Write PCM data to a temporary WAV file
        wav_path = await self._write_wav(pcm_data)

        try:
            # Build whisper.cpp command
            cmd = [
                self.binary_path,
                "--model", self.model_path,
                "--language", self.language,
                "--threads", str(self.threads),
                "--print-progress", "false",
                "--output-json",
                "--file", str(wav_path),
            ]

            logger.debug(f"Running whisper.cpp: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion with cancellation support
            if cancel_event:
                done_task = asyncio.create_task(proc.communicate())
                cancel_task = asyncio.create_task(cancel_event.wait())

                done, pending = await asyncio.wait(
                    {done_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if cancel_task in done:
                    # Cancelled — kill the process
                    proc.kill()
                    await proc.wait()
                    raise asyncio.CancelledError("STT cancelled")

                stdout, stderr = done_task.result()
            else:
                stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"whisper.cpp failed (code {proc.returncode}): {error_msg}")

            # Parse JSON output
            result = self._parse_output(stdout.decode("utf-8", errors="replace"), wav_path)

            latency_ms = int((time.monotonic() - start_time) * 1000)
            result.latency_ms = latency_ms
            logger.info(
                f"STT: '{result.text[:80]}' "
                f"[lang={result.language}, conf={result.language_confidence:.2f}, "
                f"latency={latency_ms}ms]"
            )
            return result

        finally:
            # Cleanup temp file
            try:
                wav_path.unlink(missing_ok=True)
                # Also clean up the .json output file whisper.cpp creates
                json_path = wav_path.with_suffix(".wav.json")
                json_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _write_wav(self, pcm_data: bytes) -> Path:
        """Write PCM data to a temporary WAV file."""
        def _write():
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, prefix="carely_")
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_data)
            return Path(tmp.name)

        return await asyncio.get_event_loop().run_in_executor(None, _write)

    def _parse_output(self, output: str, wav_path: Path) -> STTResult:
        """Parse whisper.cpp output. Tries JSON first, falls back to text parsing."""
        # Try reading the .json output file
        json_path = wav_path.with_suffix(".wav.json")
        try:
            if json_path.exists():
                with open(json_path, "r") as f:
                    data = json.load(f)

                text = ""
                segments = []
                for seg in data.get("transcription", []):
                    text += seg.get("text", "")
                    segments.append({
                        "start": seg.get("timestamps", {}).get("from", ""),
                        "end": seg.get("timestamps", {}).get("to", ""),
                        "text": seg.get("text", ""),
                    })

                # whisper.cpp reports language in result
                lang = data.get("result", {}).get("language", "en")

                return STTResult(
                    text=text.strip(),
                    language=lang,
                    language_confidence=0.9,  # whisper.cpp doesn't always report confidence
                    latency_ms=0,
                    segments=segments,
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse JSON output: {e}")

        # Fallback: parse stdout text
        text = output.strip()
        # whisper.cpp stdout format: [timestamp] text
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            # Remove timestamp prefix like "[00:00.000 --> 00:02.000]"
            if "]" in line:
                line = line.split("]", 1)[1]
            cleaned.append(line.strip())

        return STTResult(
            text=" ".join(cleaned).strip(),
            language="en",
            language_confidence=0.5,
            latency_ms=0,
            segments=[],
        )
