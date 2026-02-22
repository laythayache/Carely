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
        self.fsm.new_turn()
        self.audio_bridge.send_start_capture()
        self.keyword_spotter.pause()  # Save CPU during active listening

    async def _action_on_vad_start(self, data: TransitionResult | None) -> None:
        self.fsm.set_recording(True)

    async def _action_process_speech(self, data: TransitionResult | None) -> None:
        self.audio_bridge.send_stop_capture()
        self.fsm.set_recording(False)
        self.keyword_spotter.resume()

        # Get speech data and run STT
        speech_data = self.audio_bridge.get_speech_data()
        if not speech_data:
            logger.warning("No speech data available")
            return

        try:
            result = await self.stt.transcribe(speech_data, self.fsm.cancel_event)
            if not result.text.strip():
                logger.info("STT returned empty transcript, ignoring")
                await self.fsm.handle_event(Event.TIMEOUT)
                return

            await self.ui_server.broadcast_transcript(result.text, result.language)
            stt_data = TransitionResult(
                turn_id=self.fsm.current_turn_id,
                transcript=result.text,
                language=result.language,
                language_confidence=result.language_confidence,
                metadata={"stt_latency_ms": result.latency_ms},
            )
            await self.fsm.handle_event(Event.STT_COMPLETE, stt_data)

        except asyncio.CancelledError:
            logger.info("STT cancelled")

    async def _action_force_process_speech(self, data: TransitionResult | None) -> None:
        # Same as process_speech but triggered by long-press
        await self._action_process_speech(data)

    async def _action_cancel_listening(self, data: TransitionResult | None) -> None:
        self.audio_bridge.send_stop_capture()
        self.fsm.cancel_current_turn()
        self.fsm.set_recording(False)
        self.keyword_spotter.resume()

    async def _action_send_webhook(self, data: TransitionResult | None) -> None:
        if not data or not data.transcript:
            return

        try:
            response = await self.webhook.send_turn(
                transcript=data.transcript,
                language=data.language or "en",
                turn_id=self.fsm.current_turn_id or "",
                language_confidence=data.language_confidence,
                metadata=data.metadata,
                cancel_event=self.fsm.cancel_event,
            )

            # Check if turn is still valid (not cancelled by barge-in)
            if not self.fsm.is_turn_valid(response.turn_id):
                logger.info(f"Discarding stale webhook response for turn {response.turn_id}")
                return

            webhook_data = TransitionResult(
                turn_id=response.turn_id,
                spoken_text=response.spoken_text,
                voice_language=response.language,
            )
            await self.fsm.handle_event(Event.WEBHOOK_RESPONSE, webhook_data)

        except WebhookUnavailableError:
            await self.fsm.handle_event(
                Event.WEBHOOK_TIMEOUT,
                TransitionResult(transcript=data.transcript),
            )
        except asyncio.CancelledError:
            logger.info("Webhook cancelled")

    async def _action_start_speaking(self, data: TransitionResult | None) -> None:
        if not data or not data.spoken_text:
            await self.fsm.handle_event(Event.TTS_COMPLETE)
            return

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
            await self.fsm.handle_event(Event.TTS_COMPLETE)

        except asyncio.CancelledError:
            logger.info("TTS cancelled")

    async def _action_handle_webhook_failure(self, data: TransitionResult | None) -> None:
        # Try fallback agent
        if data and data.transcript:
            fallback_response = self.fallback.match(data.transcript)
            if fallback_response:
                logger.info(f"Fallback matched: {fallback_response.spoken_text[:50]}")
                speak_data = TransitionResult(
                    spoken_text=fallback_response.spoken_text,
                    voice_language=fallback_response.language,
                )
                # Go directly to speaking
                await self.fsm._transition_to(State.SPEAKING, "start_speaking", speak_data)
                return

        # No fallback match — show error
        error_msg = "I couldn't reach the server. Please try again."
        await self.ui_server.broadcast_error(error_msg, "WEBHOOK_TIMEOUT")

    async def _action_handle_processing_timeout(self, data: TransitionResult | None) -> None:
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_error("Processing took too long.", "PROCESSING_TIMEOUT")

    async def _action_cancel_processing(self, data: TransitionResult | None) -> None:
        self.fsm.cancel_current_turn()

    async def _action_on_tts_complete(self, data: TransitionResult | None) -> None:
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_transcript("")

    async def _action_barge_in(self, data: TransitionResult | None) -> None:
        await self.tts.stop()
        self.fsm.cancel_current_turn()
        await self.ui_server.broadcast_transcript("")

    async def _action_emergency_during_speech(self, data: TransitionResult | None) -> None:
        await self.tts.stop()
        self.fsm.cancel_current_turn()
        await self._action_handle_emergency(data)

    async def _action_handle_emergency(self, data: TransitionResult | None) -> None:
        turn_id = self.fsm.new_turn()
        transcript = "EMERGENCY: User triggered emergency"
        if data and isinstance(data, TransitionResult) and data.metadata:
            keyword = data.metadata.get("keyword", "unknown")
            transcript = f"EMERGENCY: User said '{keyword}'"

        try:
            response = await self.webhook.send_emergency(
                transcript=transcript,
                turn_id=turn_id,
            )
            speak_data = TransitionResult(
                turn_id=response.turn_id,
                spoken_text=response.spoken_text,
                voice_language=response.language,
            )
            await self.fsm.handle_event(Event.WEBHOOK_RESPONSE, speak_data)
        except (WebhookUnavailableError, Exception):
            await self.fsm.handle_event(Event.WEBHOOK_TIMEOUT)

    async def _action_speak_offline_emergency(self, data: TransitionResult | None) -> None:
        response = self.fallback.get_offline_emergency()
        speak_data = TransitionResult(
            spoken_text=response.spoken_text,
            voice_language=response.language,
        )
        # Bypass FSM transition — directly speak
        await self._action_start_speaking(speak_data)

    async def _action_dismiss_error(self, data: TransitionResult | None) -> None:
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
        while True:
            event_type, event_data = await self.event_queue.get()

            try:
                if event_type == "button":
                    if event_data == "press":
                        await self.fsm.handle_event(Event.BUTTON_PRESS)
                    elif event_data == "long_press":
                        await self.fsm.handle_event(Event.BUTTON_LONG_PRESS)

                elif event_type == "ui_button":
                    # Button press from web UI
                    if event_data == "press":
                        await self.fsm.handle_event(Event.BUTTON_PRESS)
                    elif event_data == "long_press":
                        await self.fsm.handle_event(Event.BUTTON_LONG_PRESS)

                elif event_type == "emergency_key":
                    await self.fsm.handle_event(Event.EMERGENCY_KEY)

                elif event_type == "emergency_keyword":
                    await self.fsm.handle_event(
                        Event.EMERGENCY_KEYWORD,
                        TransitionResult(is_emergency=True, metadata=event_data),
                    )

                elif event_type == "vad_start":
                    await self.fsm.handle_event(Event.VAD_START)

                elif event_type == "vad_end":
                    await self.fsm.handle_event(Event.VAD_END)

                elif event_type == "speech_data":
                    # Speech data received after VAD_END — trigger processing
                    pass  # Handled in _action_process_speech via audio_bridge.get_speech_data()

                elif event_type == "pipeline_ready":
                    logger.info("Audio pipeline ready")
                    self.health.update_status("idle", pipeline="ready")

                elif event_type == "pipeline_error":
                    logger.error(f"Pipeline error: {event_data}")
                    if self.fsm.record_crash():
                        await self.fsm.handle_event(Event.CRASH_LOOP)

                else:
                    logger.debug(f"Unknown event: {event_type}")

            except Exception:
                logger.exception(f"Error processing event {event_type}")
                if self.fsm.record_crash():
                    await self.fsm.handle_event(Event.CRASH_LOOP)

    async def run(self) -> None:
        """Start all components and run the main event loop."""
        logger.info("Starting Carely Voice Assistant...")

        # Start all services
        await self.ui_server.start()
        await self.health.start()
        await self.webhook.start()

        self.audio_bridge.start()
        self.input_handler.start()
        self.keyword_spotter.start()

        self.health.update_status("idle")
        await self.ui_server.broadcast_state("idle")

        logger.info("All components started. Ready for interaction.")

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
    config = load_config()
    setup_logging(config)

    logger.info(f"Carely device: {config.device_id}")
    logger.info(f"Webhook: {config.webhook_url}")

    orchestrator = Orchestrator(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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
