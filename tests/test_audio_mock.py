"""Tests for the audio bridge with mocked Unix socket IPC."""

import asyncio
import struct
import threading

import pytest

from src.audio_bridge import (
    AudioBridge,
    MSG_AUDIO_FRAME,
    MSG_VAD_START,
    MSG_VAD_END,
    MSG_AMPLITUDE,
    MSG_PIPELINE_READY,
    MSG_PIPELINE_ERROR,
    HEADER_SIZE,
)


def _make_msg(msg_type: int, payload: bytes = b"") -> bytes:
    """Build a raw IPC message: type(1B) + length(4B LE) + payload."""
    return struct.pack("<BL", msg_type, len(payload)) + payload


class TestMessageParsing:
    """Verify header/payload parsing without a real socket."""

    def test_header_size(self):
        assert HEADER_SIZE == 5

    def test_make_msg_audio_frame(self):
        pcm = b"\x00\x01" * 160  # 160 samples, 320 bytes
        msg = _make_msg(MSG_AUDIO_FRAME, pcm)
        assert msg[0] == MSG_AUDIO_FRAME
        assert struct.unpack_from("<L", msg, 1)[0] == len(pcm)
        assert msg[5:] == pcm

    def test_make_msg_vad_start(self):
        msg = _make_msg(MSG_VAD_START)
        assert msg[0] == MSG_VAD_START
        assert struct.unpack_from("<L", msg, 1)[0] == 0

    def test_make_msg_vad_end(self):
        msg = _make_msg(MSG_VAD_END)
        assert msg[0] == MSG_VAD_END
        assert struct.unpack_from("<L", msg, 1)[0] == 0

    def test_make_msg_amplitude(self):
        amp = struct.pack("<f", 0.72)
        msg = _make_msg(MSG_AMPLITUDE, amp)
        assert msg[0] == MSG_AMPLITUDE
        parsed_amp = struct.unpack_from("<f", msg, 5)[0]
        assert abs(parsed_amp - 0.72) < 1e-5

    def test_make_msg_pipeline_ready(self):
        msg = _make_msg(MSG_PIPELINE_READY)
        assert msg[0] == MSG_PIPELINE_READY

    def test_make_msg_pipeline_error(self):
        error_text = "device not found"
        payload = error_text.encode("utf-8")
        msg = _make_msg(MSG_PIPELINE_ERROR, payload)
        assert msg[0] == MSG_PIPELINE_ERROR
        assert msg[5:].decode("utf-8") == error_text


class TestMessageConstants:
    """Verify IPC message type constants match the C++ side."""

    def test_c_to_python_types(self):
        assert MSG_AUDIO_FRAME == 0x01
        assert MSG_VAD_START == 0x02
        assert MSG_VAD_END == 0x03
        assert MSG_AMPLITUDE == 0x04
        assert MSG_PIPELINE_READY == 0x05
        assert MSG_PIPELINE_ERROR == 0x06

    def test_command_values(self):
        from src.audio_bridge import CMD_START_CAPTURE, CMD_STOP_CAPTURE, CMD_SET_VAD_MODE
        assert CMD_START_CAPTURE == 0x80
        assert CMD_STOP_CAPTURE == 0x81
        assert CMD_SET_VAD_MODE == 0x82


class TestAudioBridgeInit:
    """Test AudioBridge construction (no socket needed)."""

    def test_construction(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            bridge = AudioBridge(
                socket_path="/tmp/test-audio.sock",
                event_queue=queue,
                loop=loop,
            )
            assert bridge.socket_path == "/tmp/test-audio.sock"
            assert bridge._collecting_speech is False
        finally:
            loop.close()

    def test_speech_data_empty_initially(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            bridge = AudioBridge(
                socket_path="/tmp/test.sock",
                event_queue=queue,
                loop=loop,
            )
            # get_speech_data returns immediately with empty bytes if no data
            # (speech_ready event not set, but timeout is short)
            data = bridge.get_speech_data(timeout=0.01)
            assert data == b""
        finally:
            loop.close()

    def test_send_start_capture_clears_state(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            bridge = AudioBridge(
                socket_path="/tmp/test.sock",
                event_queue=queue,
                loop=loop,
            )
            # Simulate prior speech data
            bridge._speech_buffer.extend(b"\x01\x02\x03")
            bridge._collecting_speech = True
            bridge._speech_ready.set()

            bridge.send_start_capture()

            assert len(bridge._speech_buffer) == 0
            assert bridge._collecting_speech is False
            assert not bridge._speech_ready.is_set()
        finally:
            loop.close()


class TestHandleMessage:
    """Test the _handle_message dispatch (without socket I/O)."""

    def _make_bridge(self):
        loop = asyncio.new_event_loop()
        queue = asyncio.Queue()
        bridge = AudioBridge(
            socket_path="/tmp/test.sock",
            event_queue=queue,
            loop=loop,
        )
        return bridge, queue, loop

    def test_vad_start_clears_speech_buffer(self):
        bridge, queue, loop = self._make_bridge()
        try:
            bridge._speech_buffer.extend(b"\xff" * 100)
            bridge._handle_message(MSG_VAD_START, b"")
            assert len(bridge._speech_buffer) == 0
        finally:
            loop.close()

    def test_vad_end_sets_collecting_flag(self):
        bridge, queue, loop = self._make_bridge()
        try:
            assert bridge._collecting_speech is False
            bridge._handle_message(MSG_VAD_END, b"")
            assert bridge._collecting_speech is True
        finally:
            loop.close()

    def test_audio_frame_during_collection_signals_ready(self):
        bridge, queue, loop = self._make_bridge()
        try:
            bridge._collecting_speech = True
            pcm = b"\x00\x01" * 160
            bridge._handle_message(MSG_AUDIO_FRAME, pcm)
            assert bridge._speech_ready.is_set()
            assert bridge._collecting_speech is False
            assert bytes(bridge._speech_buffer) == pcm
        finally:
            loop.close()

    def test_amplitude_callback(self):
        bridge, queue, loop = self._make_bridge()
        try:
            received = []
            bridge.set_amplitude_callback(lambda v: received.append(v))
            payload = struct.pack("<f", 0.55)
            bridge._handle_message(MSG_AMPLITUDE, payload)
            assert len(received) == 1
            assert abs(received[0] - 0.55) < 1e-5
        finally:
            loop.close()

    def test_audio_frame_callback_when_not_collecting(self):
        bridge, queue, loop = self._make_bridge()
        try:
            frames = []
            bridge.set_audio_frame_callback(lambda f: frames.append(f))
            pcm = b"\x42" * 320
            bridge._handle_message(MSG_AUDIO_FRAME, pcm)
            assert len(frames) == 1
            assert frames[0] == pcm
        finally:
            loop.close()

    def test_pipeline_error_message(self):
        bridge, queue, loop = self._make_bridge()
        try:
            error_payload = "ALSA device not found".encode("utf-8")
            bridge._handle_message(MSG_PIPELINE_ERROR, error_payload)
            # Event should have been queued (via call_soon_threadsafe)
            # Since we're not running the loop, we can't dequeue, but
            # we verify no crash occurred
        finally:
            loop.close()


class TestSpeechDataFlow:
    """Test the full VAD_START → collect frames → VAD_END → speech data flow."""

    def test_complete_speech_collection(self):
        loop = asyncio.new_event_loop()
        try:
            queue = asyncio.Queue()
            bridge = AudioBridge(
                socket_path="/tmp/test.sock",
                event_queue=queue,
                loop=loop,
            )

            # 1. Start capture
            bridge.send_start_capture()
            assert not bridge._speech_ready.is_set()

            # 2. VAD_START arrives
            bridge._handle_message(MSG_VAD_START, b"")
            assert len(bridge._speech_buffer) == 0

            # 3. VAD_END arrives
            bridge._handle_message(MSG_VAD_END, b"")
            assert bridge._collecting_speech is True

            # 4. Speech data arrives as AUDIO_FRAME
            speech_pcm = b"\x10\x20" * 8000  # ~1 second
            bridge._handle_message(MSG_AUDIO_FRAME, speech_pcm)

            # 5. Verify speech data
            assert bridge._speech_ready.is_set()
            data = bridge.get_speech_data(timeout=0.1)
            assert data == speech_pcm
        finally:
            loop.close()
