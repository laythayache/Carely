"""
Python-side IPC bridge to the C++ audio pipeline.
Connects via Unix domain socket, receives audio frames, VAD events,
and amplitude data. Sends control commands back to C++.

Protocol matches ipc_server.h:
  Message = type(1B) + length(4B LE) + payload(length bytes)
"""

import asyncio
import logging
import socket
import struct
import threading
from typing import Callable, Any

logger = logging.getLogger(__name__)

# Message types from C++ → Python
MSG_AUDIO_FRAME = 0x01
MSG_VAD_START = 0x02
MSG_VAD_END = 0x03
MSG_AMPLITUDE = 0x04
MSG_PIPELINE_READY = 0x05
MSG_PIPELINE_ERROR = 0x06

# Commands Python → C++
CMD_START_CAPTURE = 0x80
CMD_STOP_CAPTURE = 0x81
CMD_SET_VAD_MODE = 0x82

HEADER_SIZE = 5  # 1 byte type + 4 bytes length


class AudioBridge:
    """
    Reads messages from the C++ audio pipeline via Unix socket.
    Runs in a dedicated thread, posts events to the asyncio event queue.
    """

    def __init__(
        self,
        socket_path: str,
        event_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ):
        self.socket_path = socket_path
        self.event_queue = event_queue
        self.loop = loop
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # Callbacks for high-frequency data (amplitude, audio frames)
        self._amplitude_callback: Callable[[float], Any] | None = None
        self._audio_frame_callback: Callable[[bytes], Any] | None = None

        # Accumulated speech data after VAD_END
        self._speech_buffer = bytearray()
        self._collecting_speech = False

    def set_amplitude_callback(self, cb: Callable[[float], Any]) -> None:
        """Set callback for amplitude values (called from reader thread)."""
        self._amplitude_callback = cb

    def set_audio_frame_callback(self, cb: Callable[[bytes], Any]) -> None:
        """Set callback for live audio frames (called from reader thread)."""
        self._audio_frame_callback = cb

    def start(self) -> None:
        """Connect to the C++ pipeline and start reading."""
        self._running = True
        self._thread = threading.Thread(
            target=self._connect_and_read, daemon=True, name="audio-bridge"
        )
        self._thread.start()

    def stop(self) -> None:
        """Disconnect and stop the reader thread."""
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
        logger.info("Audio bridge stopped")

    def send_start_capture(self) -> None:
        """Tell C++ to start VAD-monitored capture."""
        self._speech_buffer.clear()
        self._collecting_speech = False
        self._send_command(CMD_START_CAPTURE)

    def send_stop_capture(self) -> None:
        """Tell C++ to stop capture."""
        self._collecting_speech = False
        self._send_command(CMD_STOP_CAPTURE)

    def send_set_vad_mode(self, aggressiveness: int) -> None:
        """Change VAD aggressiveness (0-3)."""
        self._send_command(CMD_SET_VAD_MODE, bytes([aggressiveness]))

    def get_speech_data(self) -> bytes:
        """Return the accumulated speech PCM data from last VAD session."""
        return bytes(self._speech_buffer)

    def _send_command(self, cmd: int, payload: bytes = b"") -> None:
        """Send a command to the C++ pipeline."""
        if not self._sock:
            return
        try:
            header = struct.pack("<BL", cmd, len(payload))
            self._sock.sendall(header + payload)
        except OSError as e:
            logger.error(f"Failed to send command 0x{cmd:02x}: {e}")

    def _connect_and_read(self) -> None:
        """Thread target: connect to socket and read messages."""
        while self._running:
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.settimeout(2.0)
                logger.info(f"Connecting to audio pipeline at {self.socket_path}")
                self._sock.connect(self.socket_path)
                self._sock.settimeout(None)
                logger.info("Connected to audio pipeline")
                self._read_loop()
            except (ConnectionRefusedError, FileNotFoundError):
                if self._running:
                    logger.debug("Audio pipeline not ready, retrying in 1s...")
                    import time
                    time.sleep(1.0)
            except OSError as e:
                if self._running:
                    logger.error(f"Audio bridge error: {e}, reconnecting in 1s...")
                    import time
                    time.sleep(1.0)
            finally:
                if self._sock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None

    def _read_loop(self) -> None:
        """Read messages from the socket until disconnected."""
        while self._running:
            # Read header
            header = self._recv_exact(HEADER_SIZE)
            if header is None:
                break

            msg_type = header[0]
            length = struct.unpack_from("<L", header, 1)[0]

            # Read payload
            payload = b""
            if length > 0:
                payload = self._recv_exact(length)
                if payload is None:
                    break

            self._handle_message(msg_type, payload)

    def _recv_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes from the socket."""
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self._sock.recv(n - len(data))
                if not chunk:
                    return None  # Connection closed
                data.extend(chunk)
            except OSError:
                return None
        return bytes(data)

    def _handle_message(self, msg_type: int, payload: bytes) -> None:
        """Dispatch received messages."""
        if msg_type == MSG_AUDIO_FRAME:
            # During speech collection, this is the full speech data after VAD_END
            if self._collecting_speech:
                self._speech_buffer.extend(payload)
                self._collecting_speech = False
                # Emit event with speech data
                self._emit_event("speech_data", bytes(self._speech_buffer))
            elif self._audio_frame_callback:
                self._audio_frame_callback(payload)

        elif msg_type == MSG_VAD_START:
            self._speech_buffer.clear()
            self._emit_event("vad_start", None)

        elif msg_type == MSG_VAD_END:
            # Next AUDIO_FRAME message will contain the full speech data
            self._collecting_speech = True
            self._emit_event("vad_end", None)

        elif msg_type == MSG_AMPLITUDE:
            if len(payload) >= 4:
                amplitude = struct.unpack("<f", payload[:4])[0]
                if self._amplitude_callback:
                    self._amplitude_callback(amplitude)

        elif msg_type == MSG_PIPELINE_READY:
            logger.info("Audio pipeline reports READY")
            self._emit_event("pipeline_ready", None)

        elif msg_type == MSG_PIPELINE_ERROR:
            error_msg = payload.decode("utf-8", errors="replace")
            logger.error(f"Audio pipeline error: {error_msg}")
            self._emit_event("pipeline_error", error_msg)

    def _emit_event(self, event_type: str, data: Any) -> None:
        """Thread-safe emit to the asyncio event queue."""
        try:
            self.loop.call_soon_threadsafe(
                self.event_queue.put_nowait, (event_type, data)
            )
        except RuntimeError:
            pass  # Event loop closed
