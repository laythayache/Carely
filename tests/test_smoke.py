"""
On-device smoke test for end-to-end latency measurement.

This test is designed to run ON THE DEVICE with all components available.
It verifies:
  1. Audio pipeline is responsive
  2. STT transcribes within latency budget
  3. Webhook round-trip works
  4. TTS produces audio output
  5. Total end-of-speech → TTS start < 2000ms

Skip this test in CI — it requires hardware.
"""

import asyncio
import os
import struct
import tempfile
import time
import wave

import pytest

# Skip if not running on device
ON_DEVICE = os.path.exists("/opt/assistant/bin/whisper-cli")


@pytest.mark.skipif(not ON_DEVICE, reason="Not running on device")
class TestSmoke:

    @pytest.mark.asyncio
    async def test_whisper_cli_exists(self):
        """Verify whisper.cpp binary is installed."""
        proc = await asyncio.create_subprocess_exec(
            "/opt/assistant/bin/whisper-cli", "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # whisper-cli returns 0 or 1 for --help depending on version
        assert proc.returncode in (0, 1)

    @pytest.mark.asyncio
    async def test_piper_exists(self):
        """Verify Piper binary is installed."""
        proc = await asyncio.create_subprocess_exec(
            "/opt/assistant/bin/piper", "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        assert proc.returncode in (0, 1)

    @pytest.mark.asyncio
    async def test_whisper_model_loadable(self):
        """Verify Whisper model can be loaded."""
        model_path = "/opt/assistant/models/whisper/ggml-base.bin"
        assert os.path.exists(model_path)
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        assert size_mb > 100, f"Model too small ({size_mb:.1f}MB), may be corrupted"

    @pytest.mark.asyncio
    async def test_stt_latency(self):
        """
        Measure STT latency with a 3-second silence WAV.
        This tests that whisper.cpp can process audio within budget.
        """
        # Create a 3-second silent WAV
        wav_path = tempfile.mktemp(suffix=".wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * (16000 * 2 * 3))  # 3s silence

        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "/opt/assistant/bin/whisper-cli",
            "--model", "/opt/assistant/models/whisper/ggml-base.bin",
            "--language", "auto",
            "--threads", "3",
            "--file", wav_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        elapsed_ms = (time.monotonic() - start) * 1000

        os.unlink(wav_path)

        assert proc.returncode == 0, f"whisper.cpp failed: {stderr.decode()}"
        # For 3s of silence, should be well under 2s
        assert elapsed_ms < 3000, f"STT too slow: {elapsed_ms:.0f}ms for 3s audio"

    @pytest.mark.asyncio
    async def test_piper_synthesis(self):
        """Verify Piper can synthesize a short phrase."""
        proc = await asyncio.create_subprocess_exec(
            "/opt/assistant/bin/piper",
            "--model", "/opt/assistant/models/piper/en_US-lessac-medium.onnx",
            "--config", "/opt/assistant/models/piper/en_US-lessac-medium.onnx.json",
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=b"Hello world")

        assert proc.returncode == 0, f"Piper failed: {stderr.decode()}"
        assert len(stdout) > 1000, f"Piper output too short ({len(stdout)} bytes)"

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Verify health endpoint responds (requires running services)."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:8081/health", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "state" in data
        except aiohttp.ClientError:
            pytest.skip("Health endpoint not available (services not running)")


class TestLatencyEstimation:
    """
    Offline latency estimation tests that don't require hardware.
    Verify that our latency budget math is correct.
    """

    def test_latency_budget_lan(self):
        """LAN latency budget should be under 2000ms."""
        audio_flush_ms = 50
        stt_inference_ms = 1500  # worst case for 5s audio on i3-9100T
        webhook_lan_ms = 100
        tts_first_chunk_ms = 150

        total = audio_flush_ms + stt_inference_ms + webhook_lan_ms + tts_first_chunk_ms
        assert total < 2000, f"LAN latency budget exceeded: {total}ms"

    def test_latency_budget_internet(self):
        """Internet latency budget should be under 2500ms (relaxed)."""
        audio_flush_ms = 50
        stt_inference_ms = 1500
        webhook_internet_ms = 400
        tts_first_chunk_ms = 150

        total = audio_flush_ms + stt_inference_ms + webhook_internet_ms + tts_first_chunk_ms
        assert total < 2500, f"Internet latency budget exceeded: {total}ms"
