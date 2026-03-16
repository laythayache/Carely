"""
Carely Voice Assistant — Main Orchestrator.

Wires all components through the FSM:
  Audio Bridge ← C++ pipeline (IPC)
  Input Handler → FSM events
  FSM → STT → Webhook → TTS
  WebSocket UI ← state + amplitude updates
  Keyword Spotter → emergency events
  Fallback Agent → offline responses
"""

import asyncio
import logging
import signal
import sys
import time
from typing import Any

from src.config import load_config, setup_logging, Config
from src.fsm import FSM, State, Event, TransitionResult
from src.audio_bridge import AudioBridge
from src.stt_engine import STTEngine
from src.tts_engine import TTSEngine
from src.webhook_client import WebhookClient, WebhookUnavailableError
from src.fallback_agent import FallbackAgent
from src.keyword_spotter import KeywordSpotter
from src.input_handler import InputHandler
from src.ui_server import UIServer
from src.health import HealthServer

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main application orchestrator.
    Initializes all components, registers FSM actions, and runs the event loop.
    """

    def __init__(self, config: Config):
        self.config = config
        self.loop = asyncio.get_event_loop()
        self.event_queue: asyncio.Queue = asyncio.Queue()

        # Core components
        self.fsm = FSM(
            crash_loop_threshold=config.crash_loop_threshold,
            crash_loop_window_s=config.crash_loop_window_s,
        )
        self.audio_bridge = AudioBridge(
            socket_path=config.ipc_socket_path,
            event_queue=self.event_queue,
            loop=self.loop,
        )
        self.stt = STTEngine(
            binary_path=config.whisper_binary_path,
            model_path=config.whisper_model_path,
            language=config.whisper_language,
            threads=config.whisper_threads,
        )
        self.tts = TTSEngine(
            binary_path=config.piper_binary_path,
            voice_paths={
                "en": config.piper_voice_en,
                "ar": config.piper_voice_ar,
            },
            default_language=config.piper_default_voice,
        )
        self.webhook = WebhookClient(
            url=config.webhook_url,
            device_id=config.device_id,
            timeout_s=config.webhook_timeout_s,
            max_retries=config.webhook_max_retries,
            retry_backoff_ms=config.webhook_retry_backoff_ms,
            auth_token=config.webhook_auth_token,
        )
        self.fallback = FallbackAgent()
        self.keyword_spotter = KeywordSpotter(
            event_queue=self.event_queue,
            loop=self.loop,
            threshold=config.keyword_spotter_threshold,
            enabled=config.keyword_spotter_enabled,
        )
        self.input_handler = InputHandler(
            event_queue=self.event_queue,
            loop=self.loop,
            button_key=config.button_key,
            emergency_key=config.emergency_key,
            long_press_ms=config.long_press_ms,
        )
        self.ui_server = UIServer(
            host=config.ui_host,
            port=config.ui_port,
            event_queue=self.event_queue,
        )
        self.health = HealthServer(
            port=config.health_port,
            watchdog_interval_s=config.watchdog_interval_s,
        )

        self._register_fsm_actions()
        self._register_fsm_listeners()
        self._register_audio_callbacks()

    def _register_fsm_actions(self) -> None:
        """Register all FSM action callbacks."""
        actions = {
            "start_listening": self._action_start_listening,
            "on_vad_start": self._action_on_vad_start,
            "process_speech": self._action_process_speech,
            "force_process_speech": self._action_force_process_speech,
            "cancel_listening": self._action_cancel_listening,
            "send_webhook": self._action_send_webhook,
            "start_speaking": self._action_start_speaking,
            "handle_webhook_failure": self._action_handle_webhook_failure,
            "handle_processing_timeout": self._action_handle_processing_timeout,
            "cancel_processing": self._action_cancel_processing,
            "on_tts_complete": self._action_on_tts_complete,
            "barge_in": self._action_barge_in,
            "emergency_during_speech": self._action_emergency_during_speech,
            "handle_emergency": self._action_handle_emergency,
            "speak_offline_emergency": self._action_speak_offline_emergency,
            "dismiss_error": self._action_dismiss_error,
            "enter_safe_mode": self._action_enter_safe_mode,
            "attempt_recovery": self._action_attempt_recovery,
        }
        for name, callback in actions.items():
            self.fsm.register_action(name, callback)

    def _register_fsm_listeners(self) -> None:
        """Register state change listener for UI updates."""
        async def on_state_change(prev: State, next_state: State) -> None:
            state_name = next_state.name.lower()
            await self.ui_server.broadcast_state(state_name)
            self.health.update_status(state_name)

        self.fsm.add_state_listener(on_state_change)

    def _register_audio_callbacks(self) -> None:
        """Wire audio bridge callbacks."""
        def on_amplitude(value: float) -> None:
            # Forward amplitude to UI (throttled — don't await in thread)
            try:
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self.ui_server.broadcast_amplitude(value)
                    )
                )
            except RuntimeError:
                pass

        def on_audio_frame(pcm_bytes: bytes) -> None:
            # Feed to keyword spotter
            self.keyword_spotter.feed_audio(pcm_bytes)

        self.audio_bridge.set_amplitude_callback(on_amplitude)
        self.audio_bridge.set_audio_frame_callback(on_audio_frame)

    # ── FSM Actions ──────────────────────────────────────────────

    async def _action_start_listening(self, data: TransitionResult | None) -> None:
        turn_id = self.fsm.new_turn()
        logger.info(f"[LISTEN] Starting new turn {turn_id}, sending start_capture to audio bridge")
        await self.ui_server.broadcast_log(f"Starting turn {turn_id[:8]}...", "info")
        self.audio_bridge.send_start_capture()
        self.keyword_spotter.pause()
        logger.info(f"[LISTEN] Keyword spotter paused, waiting for speech...")

    async def _action_on_vad_start(self, data: TransitionResult | None) -> None:
        logger.info(f"[VAD] Speech detected! Recording started (turn={self.fsm.current_turn_id})")
        self.fsm.set_recording(True)

    async def _action_process_speech(self, data: TransitionResult | None) -> None:
        process_start = time.monotonic()
        logger.info(f"[PROCESS] Stopping capture, beginning speech processing (turn={self.fsm.current_turn_id})")
        await self.ui_server.broadcast_log("Processing speech...", "info")
        self.audio_bridge.send_stop_capture()
        self.fsm.set_recording(False)
        self.keyword_spotter.resume()

        logger.info("[PROCESS] Waiting for speech data from audio bridge...")
        await self.ui_server.broadcast_log("Waiting for audio data...", "info")
        speech_data = await asyncio.get_event_loop().run_in_executor(
            None, self.audio_bridge.get_speech_data
        )
        if not speech_data:
            logger.warning("[PROCESS] No speech data received from audio bridge - aborting")
            await self.ui_server.broadcast_log("No speech data received!", "warn")
            return
        audio_duration = len(speech_data) / 32000
        logger.info(f"[PROCESS] Got {len(speech_data)} bytes of speech data ({audio_duration:.1f}s of audio)")
        await self.ui_server.broadcast_log(f"Got {audio_duration:.1f}s of audio", "info")

        try:
            logger.info("[STT] Starting transcription...")
            await self.ui_server.broadcast_log("Running Whisper STT...", "info")
            stt_start = time.monotonic()
            result = await self.stt.transcribe(speech_data, self.fsm.cancel_event)
            stt_elapsed = int((time.monotonic() - stt_start) * 1000)
            logger.info(f"[STT] Completed in {stt_elapsed}ms: text='{result.text}', lang={result.language}, conf={result.language_confidence:.2f}")
            await self.ui_server.broadcast_log(f"STT done in {stt_elapsed}ms: '{result.text[:50]}...'", "info")

            if not result.text.strip():
                logger.warning("[STT] Empty transcript returned - firing TIMEOUT to return to idle")
                await self.ui_server.broadcast_log("Empty transcript - no speech detected", "warn")
                await self.fsm.handle_event(Event.TIMEOUT)
                return

            logger.info(f"[UI] Broadcasting transcript to {self.ui_server.client_count} WS clients")
            await self.ui_server.broadcast_transcript(result.text, result.language)
            stt_data = TransitionResult(
                turn_id=self.fsm.current_turn_id,
                transcript=result.text,
                language=result.language,
                language_confidence=result.language_confidence,
                metadata={"stt_latency_ms": result.latency_ms},
            )
            logger.info(f"[PROCESS] Firing STT_COMPLETE event (turn={self.fsm.current_turn_id}, elapsed={int((time.monotonic() - process_start) * 1000)}ms)")
            await self.fsm.handle_event(Event.STT_COMPLETE, stt_data)

        except asyncio.CancelledError:
            logger.warning(f"[STT] Cancelled after {int((time.monotonic() - stt_start) * 1000)}ms")

    async def _action_force_process_speech(self, data: TransitionResult | None) -> None:
        logger.info("[PROCESS] Force-processing speech (long-press triggered)")
        await self._action_process_speech(data)

    async def _action_cancel_listening(self, data: TransitionResult | None) -> None:
        logger.info(f"[CANCEL] Cancelling listening (turn={self.fsm.current_turn_id})")
        self.audio_bridge.send_stop_capture()
        self.fsm.cancel_current_turn()
        self.fsm.set_recording(False)
        self.keyword_spotter.resume()
        logger.info("[CANCEL] Listening cancelled, returned to idle")

    async def _action_send_webhook(self, data: TransitionResult | None) -> None:
        if not data or not data.transcript:
            logger.warning("[WEBHOOK] send_webhook called with no transcript data - skipping")
            await self.ui_server.broadcast_log("No transcript to send to webhook", "warn")
            return

        logger.info(f"[WEBHOOK] Sending to n8n: transcript='{data.transcript[:100]}', lang={data.language}, turn={self.fsm.current_turn_id}")
        await self.ui_server.broadcast_log(f"Sending to webhook: '{data.transcript[:40]}...'", "info")
        webhook_start = time.monotonic()

        try:
            response = await self.webhook.send_turn(
                transcript=data.transcript,
                language=data.language or "en",
                turn_id=self.fsm.current_turn_id or "",
                language_confidence=data.language_confidence,
                metadata=data.metadata,
                cancel_event=self.fsm.cancel_event,
            )
            webhook_elapsed = int((time.monotonic() - webhook_start) * 1000)
            logger.info(f"[WEBHOOK] Response received in {webhook_elapsed}ms: spoken_text='{(response.spoken_text or '')[:100]}', lang={response.language}, turn={response.turn_id}")
            await self.ui_server.broadcast_log(f"Webhook response in {webhook_elapsed}ms", "info")

            if not self.fsm.is_turn_valid(response.turn_id):
                logger.warning(f"[WEBHOOK] Discarding STALE response - turn {response.turn_id} no longer active (current={self.fsm.current_turn_id})")
                await self.ui_server.broadcast_log(f"Stale response discarded (wrong turn)", "warn")
                return

            webhook_data = TransitionResult(
                turn_id=response.turn_id,
                spoken_text=response.spoken_text,
                voice_language=response.language,
            )
            logger.info(f"[WEBHOOK] Firing WEBHOOK_RESPONSE event")
            await self.fsm.handle_event(Event.WEBHOOK_RESPONSE, webhook_data)

        except WebhookUnavailableError as e:
            webhook_elapsed = int((time.monotonic() - webhook_start) * 1000)
            logger.error(f"[WEBHOOK] FAILED after {webhook_elapsed}ms: {e}")
            await self.ui_server.broadcast_log(f"Webhook FAILED after {webhook_elapsed}ms: {e}", "error")
            await self.fsm.handle_event(
                Event.WEBHOOK_TIMEOUT,
                TransitionResult(transcript=data.transcript),
            )
        except asyncio.CancelledError:
            webhook_elapsed = int((time.monotonic() - webhook_start) * 1000)
            logger.warning(f"[WEBHOOK] Cancelled after {webhook_elapsed}ms")
            await self.ui_server.broadcast_log(f"Webhook cancelled after {webhook_elapsed}ms", "warn")

    async def _action_start_speaking(self, data: TransitionResult | None) -> None:
        if not data or not data.spoken_text:
            logger.warning("[TTS] No spoken_text in data - skipping TTS, firing TTS_COMPLETE")
            await self.fsm.handle_event(Event.TTS_COMPLETE)
            return

        logger.info(f"[TTS] Starting speech: text='{data.spoken_text[:100]}', lang={data.voice_language or 'en'}")
        logger.info(f"[UI] Broadcasting response to {self.ui_server.client_count} WS clients")
        await self.ui_server.broadcast_response(data.spoken_text, data.voice_language or "en")

        tts_start = time.monotonic()
        try:
            def amplitude_cb(val: float) -> None:
                try:
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self.ui_server.broadcast_amplitude(val)
                        )
                    )
                except RuntimeError:
                    pass

            await self.tts.speak(
                text=data.spoken_text,
                language=data.voice_language or "en",
                cancel_event=self.fsm.cancel_event,
                amplitude_callback=amplitude_cb,
            )
            tts_elapsed = int((time.monotonic() - tts_start) * 1000)
            logger.info(f"[TTS] Playback finished in {tts_elapsed}ms, firing TTS_COMPLETE")
            await self.fsm.handle_event(Event.TTS_COMPLETE)

        except asyncio.CancelledError:
            tts_elapsed = int((time.monotonic() - tts_start) * 1000)
            logger.warning(f"[TTS] Cancelled after {tts_elapsed}ms")

    async def _action_handle_webhook_failure(self, data: TransitionResult | None) -> None:
        logger.error(f"[FALLBACK] Webhook failed, trying fallback agent (transcript='{(data.transcript if data else 'None')}')")
        if data and data.transcript:
            fallback_response = self.fallback.match(data.transcript)
            if fallback_response:
                logger.info(f"[FALLBACK] Matched! Response: '{fallback_response.spoken_text[:80]}', transitioning to SPEAKING")
                speak_data = TransitionResult(
                    spoken_text=fallback_response.spoken_text,
                    voice_language=fallback_response.language,
                )
                await self.fsm._transition_to(State.SPEAKING, "start_speaking", speak_data)
                return

        logger.error("[FALLBACK] No fallback match - showing error to user")
        error_msg = "I couldn't reach the server. Please try again."
        await self.ui_server.broadcast_error(error_msg, "WEBHOOK_TIMEOUT")

    async def _action_handle_processing_timeout(self, data: TransitionResult | None) -> None:
        error_msg = "Processing took too long"
        if data and data.error_message:
            error_msg = f"Processing failed: {data.error_message}"
        logger.error(f"[TIMEOUT] {error_msg} (turn={self.fsm.current_turn_id})")
        await self.ui_server.broadcast_log(f"TIMEOUT: {error_msg}", "error")
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_error(error_msg, "PROCESSING_TIMEOUT")

    async def _action_cancel_processing(self, data: TransitionResult | None) -> None:
        logger.info(f"[CANCEL] User cancelled processing (turn={self.fsm.current_turn_id})")
        self.fsm.cancel_current_turn()

    async def _action_on_tts_complete(self, data: TransitionResult | None) -> None:
        logger.info(f"[COMPLETE] Turn complete (turn={self.fsm.current_turn_id}), clearing UI transcript")
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_transcript("")

    async def _action_barge_in(self, data: TransitionResult | None) -> None:
        logger.info(f"[BARGE-IN] User interrupted speech (turn={self.fsm.current_turn_id})")
        await self.tts.stop()
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_transcript("")
        logger.info("[BARGE-IN] TTS stopped, turn cancelled")

    async def _action_emergency_during_speech(self, data: TransitionResult | None) -> None:
        logger.warning("[EMERGENCY] Emergency triggered during speech - stopping TTS")
        await self.tts.stop()
        self.fsm.cancel_current_turn()
        await self._action_handle_emergency(data)

    async def _action_handle_emergency(self, data: TransitionResult | None) -> None:
        turn_id = self.fsm.new_turn()
        transcript = "EMERGENCY: User triggered emergency"
        if data and isinstance(data, TransitionResult) and data.metadata:
            keyword = data.metadata.get("keyword", "unknown")
            transcript = f"EMERGENCY: User said '{keyword}'"

        logger.warning(f"[EMERGENCY] Sending emergency webhook: '{transcript}' (turn={turn_id})")
        try:
            response = await self.webhook.send_emergency(
                transcript=transcript,
                turn_id=turn_id,
            )
            logger.info(f"[EMERGENCY] Webhook response: '{(response.spoken_text or '')[:80]}', lang={response.language}")
            speak_data = TransitionResult(
                turn_id=response.turn_id,
                spoken_text=response.spoken_text,
                voice_language=response.language,
            )
            await self.fsm.handle_event(Event.WEBHOOK_RESPONSE, speak_data)
        except (WebhookUnavailableError, Exception) as e:
            logger.error(f"[EMERGENCY] Webhook failed: {e}")
            await self.fsm.handle_event(Event.WEBHOOK_TIMEOUT)

    async def _action_speak_offline_emergency(self, data: TransitionResult | None) -> None:
        logger.warning("[EMERGENCY] Webhook unavailable - using offline emergency response")
        response = self.fallback.get_offline_emergency()
        logger.info(f"[EMERGENCY] Offline response: '{response.spoken_text[:80]}'")
        speak_data = TransitionResult(
            spoken_text=response.spoken_text,
            voice_language=response.language,
        )
        await self._action_start_speaking(speak_data)

    async def _action_dismiss_error(self, data: TransitionResult | None) -> None:
        logger.info("[ERROR] Dismissing error, returning to idle")
        self.fsm.cancel_current_turn()

    async def _action_enter_safe_mode(self, data: TransitionResult | None) -> None:
        logger.critical("Entering SAFE MODE due to crash loop")
        await self.ui_server.broadcast_error(
            "Device is in safe mode due to repeated errors. Press button to retry.",
            "SAFE_MODE",
        )

    async def _action_attempt_recovery(self, data: TransitionResult | None) -> None:
        logger.info("Attempting recovery from SAFE MODE")
        self.fsm._crash_timestamps.clear()

    # ── Event Loop ───────────────────────────────────────────────

    async def _process_events(self) -> None:
        """Main event processing loop. Routes events from all sources to FSM."""
        logger.info("[EVENTS] Event processing loop started")
        while True:
            event_type, event_data = await self.event_queue.get()
            queue_size = self.event_queue.qsize()
            logger.info(f"[EVENT] Received: type={event_type}, data={str(event_data)[:100]}, fsm_state={self.fsm.state.name}, queue_remaining={queue_size}")

            try:
                if event_type == "button":
                    if event_data == "press":
                        logger.info("[EVENT] Hardware button PRESS")
                        await self.fsm.handle_event(Event.BUTTON_PRESS)
                    elif event_data == "long_press":
                        logger.info("[EVENT] Hardware button LONG_PRESS")
                        await self.fsm.handle_event(Event.BUTTON_LONG_PRESS)

                elif event_type == "ui_button":
                    if event_data == "press":
                        logger.info("[EVENT] Web UI button PRESS")
                        await self.fsm.handle_event(Event.BUTTON_PRESS)
                    elif event_data == "long_press":
                        logger.info("[EVENT] Web UI button LONG_PRESS")
                        await self.fsm.handle_event(Event.BUTTON_LONG_PRESS)

                elif event_type == "emergency_key":
                    logger.warning("[EVENT] EMERGENCY KEY pressed!")
                    await self.fsm.handle_event(Event.EMERGENCY_KEY)

                elif event_type == "emergency_keyword":
                    logger.warning(f"[EVENT] EMERGENCY KEYWORD detected: {event_data}")
                    await self.fsm.handle_event(
                        Event.EMERGENCY_KEYWORD,
                        TransitionResult(is_emergency=True, metadata=event_data),
                    )

                elif event_type == "vad_start":
                    logger.info("[EVENT] VAD_START - voice activity detected")
                    await self.fsm.handle_event(Event.VAD_START)

                elif event_type == "vad_end":
                    logger.info("[EVENT] VAD_END - silence detected, speech segment complete")
                    await self.fsm.handle_event(Event.VAD_END)

                elif event_type == "speech_data":
                    data_len = len(event_data) if event_data else 0
                    logger.info(f"[EVENT] speech_data received ({data_len} bytes) - handled by process_speech")

                elif event_type == "pipeline_ready":
                    logger.info("[EVENT] Audio pipeline READY")
                    self.health.update_status("idle", pipeline="ready")

                elif event_type == "pipeline_error":
                    logger.error(f"[EVENT] PIPELINE ERROR: {event_data}")
                    if self.fsm.record_crash():
                        logger.error("[EVENT] Crash loop detected! Entering SAFE_MODE")
                        await self.fsm.handle_event(Event.CRASH_LOOP)

                else:
                    logger.warning(f"[EVENT] Unknown event type: {event_type}")

            except Exception:
                logger.exception(f"[EVENT] EXCEPTION processing event '{event_type}'")
                if self.fsm.record_crash():
                    logger.error("[EVENT] Crash loop detected after exception! Entering SAFE_MODE")
                    await self.fsm.handle_event(Event.CRASH_LOOP)

    async def run(self) -> None:
        """Start all components and run the main event loop."""
        logger.info("[BOOT] Starting Carely Voice Assistant...")

        logger.info("[BOOT] Starting UI server...")
        await self.ui_server.start()
        logger.info("[BOOT] Starting health server...")
        await self.health.start()
        logger.info("[BOOT] Starting webhook client...")
        await self.webhook.start()

        logger.info("[BOOT] Starting audio bridge...")
        self.audio_bridge.start()
        logger.info("[BOOT] Starting input handler...")
        self.input_handler.start()
        logger.info("[BOOT] Starting keyword spotter...")
        self.keyword_spotter.start()

        self.health.update_status("idle")
        await self.ui_server.broadcast_state("idle")

        logger.info("[BOOT] All components started. System READY.")

        # Run event loop
        try:
            await self._process_events()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully shutdown all components."""
        logger.info("Shutting down...")
        self.input_handler.stop()
        self.keyword_spotter.stop()
        self.audio_bridge.stop()
        await self.tts.stop()
        await self.webhook.stop()
        await self.ui_server.stop()
        await self.health.stop()
        logger.info("Shutdown complete")


def main() -> None:
    """Entry point."""
    # Create event loop FIRST — Orchestrator passes it to threads
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config = load_config()
    setup_logging(config)

    logger.info(f"Carely device: {config.device_id}")
    logger.info(f"Webhook: {config.webhook_url}")

    orchestrator = Orchestrator(config)

    # Handle shutdown signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: loop.stop())
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        loop.run_until_complete(orchestrator.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(orchestrator.shutdown())
        loop.close()


if __name__ == "__main__":
    main()
