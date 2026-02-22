"""Tests for the Carely Finite State Machine."""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from src.fsm import FSM, State, Event, TransitionResult


@pytest.fixture
def fsm():
    """Create an FSM instance with crash loop detection set to 3 in 10s for faster tests."""
    return FSM(crash_loop_threshold=3, crash_loop_window_s=10)


@pytest.fixture
def fsm_with_actions(fsm):
    """FSM with mock actions registered for all transition action names."""
    action_names = set()
    for (_, _), (_, action_name) in fsm._transitions.items():
        action_names.add(action_name)
    action_names.add("enter_safe_mode")

    mocks = {}
    for name in action_names:
        mock = AsyncMock()
        fsm.register_action(name, mock)
        mocks[name] = mock

    return fsm, mocks


class TestFSMInitialState:
    def test_starts_in_idle(self, fsm):
        assert fsm.state == State.IDLE

    def test_no_active_turn(self, fsm):
        assert fsm.current_turn_id is None

    def test_no_cancel_event(self, fsm):
        assert fsm.cancel_event is None

    def test_not_recording(self, fsm):
        assert fsm.is_recording is False


class TestHappyPath:
    """Test: IDLE → LISTENING → PROCESSING → SPEAKING → IDLE"""

    @pytest.mark.asyncio
    async def test_full_conversation_flow(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        # IDLE → LISTENING (button press)
        await fsm.handle_event(Event.BUTTON_PRESS)
        assert fsm.state == State.LISTENING
        mocks["start_listening"].assert_awaited_once()

        # LISTENING: VAD starts (stays in LISTENING)
        await fsm.handle_event(Event.VAD_START)
        assert fsm.state == State.LISTENING
        mocks["on_vad_start"].assert_awaited_once()

        # LISTENING → PROCESSING (VAD ends)
        await fsm.handle_event(Event.VAD_END)
        assert fsm.state == State.PROCESSING
        mocks["process_speech"].assert_awaited_once()

        # PROCESSING: STT completes (stays in PROCESSING, sends webhook)
        stt_result = TransitionResult(transcript="hello", language="en")
        await fsm.handle_event(Event.STT_COMPLETE, stt_result)
        assert fsm.state == State.PROCESSING
        mocks["send_webhook"].assert_awaited_once()

        # PROCESSING → SPEAKING (webhook responds)
        webhook_result = TransitionResult(spoken_text="Hi there!", voice_language="en")
        await fsm.handle_event(Event.WEBHOOK_RESPONSE, webhook_result)
        assert fsm.state == State.SPEAKING
        mocks["start_speaking"].assert_awaited_once()

        # SPEAKING → IDLE (TTS complete)
        await fsm.handle_event(Event.TTS_COMPLETE)
        assert fsm.state == State.IDLE
        mocks["on_tts_complete"].assert_awaited_once()


class TestBargeIn:
    """Test: pressing button during SPEAKING cancels TTS and returns to IDLE."""

    @pytest.mark.asyncio
    async def test_barge_in_during_speech(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        # Get to SPEAKING state
        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        await fsm.handle_event(Event.VAD_END)  # → PROCESSING
        await fsm.handle_event(Event.WEBHOOK_RESPONSE, TransitionResult(spoken_text="Hi"))
        assert fsm.state == State.SPEAKING

        # Barge-in
        await fsm.handle_event(Event.BUTTON_PRESS)
        assert fsm.state == State.IDLE
        mocks["barge_in"].assert_awaited_once()


class TestCancelDuringProcessing:
    """Test: pressing button during PROCESSING cancels and returns to IDLE."""

    @pytest.mark.asyncio
    async def test_cancel_processing(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        await fsm.handle_event(Event.VAD_END)  # → PROCESSING
        assert fsm.state == State.PROCESSING

        await fsm.handle_event(Event.BUTTON_PRESS)  # Cancel
        assert fsm.state == State.IDLE
        mocks["cancel_processing"].assert_awaited_once()


class TestCancelDuringListening:
    """Test: pressing button again during LISTENING cancels and returns to IDLE."""

    @pytest.mark.asyncio
    async def test_cancel_listening(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        assert fsm.state == State.LISTENING

        await fsm.handle_event(Event.BUTTON_PRESS)  # Cancel
        assert fsm.state == State.IDLE
        mocks["cancel_listening"].assert_awaited_once()


class TestLongPress:
    """Test: long press during LISTENING force-sends to STT."""

    @pytest.mark.asyncio
    async def test_long_press_force_send(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        await fsm.handle_event(Event.BUTTON_LONG_PRESS)  # Force process
        assert fsm.state == State.PROCESSING
        mocks["force_process_speech"].assert_awaited_once()


class TestListeningTimeout:
    """Test: LISTENING times out after 15s → IDLE."""

    @pytest.mark.asyncio
    async def test_listening_timeout(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        assert fsm.state == State.LISTENING

        # Simulate timeout event directly
        await fsm.handle_event(Event.TIMEOUT)
        assert fsm.state == State.IDLE
        mocks["cancel_listening"].assert_awaited()


class TestWebhookFailure:
    """Test: webhook timeout → ERROR state → try fallback."""

    @pytest.mark.asyncio
    async def test_webhook_timeout_to_error(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)  # → LISTENING
        await fsm.handle_event(Event.VAD_END)  # → PROCESSING

        await fsm.handle_event(Event.WEBHOOK_TIMEOUT)
        assert fsm.state == State.ERROR
        mocks["handle_webhook_failure"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_auto_dismiss(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)
        await fsm.handle_event(Event.VAD_END)
        await fsm.handle_event(Event.WEBHOOK_TIMEOUT)
        assert fsm.state == State.ERROR

        # Simulate timeout → auto dismiss
        await fsm.handle_event(Event.TIMEOUT)
        assert fsm.state == State.IDLE

    @pytest.mark.asyncio
    async def test_error_button_dismiss(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)
        await fsm.handle_event(Event.VAD_END)
        await fsm.handle_event(Event.WEBHOOK_TIMEOUT)
        assert fsm.state == State.ERROR

        await fsm.handle_event(Event.BUTTON_PRESS)
        assert fsm.state == State.IDLE
        mocks["dismiss_error"].assert_awaited()


class TestEmergency:
    """Test emergency keyword and key detection."""

    @pytest.mark.asyncio
    async def test_emergency_keyword_from_idle(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.EMERGENCY_KEYWORD, TransitionResult(is_emergency=True))
        assert fsm.state == State.EMERGENCY
        mocks["handle_emergency"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emergency_key_from_idle(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.EMERGENCY_KEY)
        assert fsm.state == State.EMERGENCY

    @pytest.mark.asyncio
    async def test_emergency_during_speech(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        # Get to SPEAKING
        await fsm.handle_event(Event.BUTTON_PRESS)
        await fsm.handle_event(Event.VAD_END)
        await fsm.handle_event(Event.WEBHOOK_RESPONSE, TransitionResult(spoken_text="Hi"))
        assert fsm.state == State.SPEAKING

        # Emergency overrides
        await fsm.handle_event(Event.EMERGENCY_KEYWORD)
        assert fsm.state == State.EMERGENCY
        mocks["emergency_during_speech"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emergency_webhook_response(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.EMERGENCY_KEYWORD)
        assert fsm.state == State.EMERGENCY

        await fsm.handle_event(
            Event.WEBHOOK_RESPONSE,
            TransitionResult(spoken_text="Help is on the way")
        )
        assert fsm.state == State.SPEAKING

    @pytest.mark.asyncio
    async def test_emergency_webhook_timeout_speaks_offline(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.EMERGENCY_KEYWORD)
        await fsm.handle_event(Event.WEBHOOK_TIMEOUT)
        assert fsm.state == State.SPEAKING
        mocks["speak_offline_emergency"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emergency_ignored_in_safe_mode(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        # Force into SAFE_MODE
        fsm._state = State.SAFE_MODE

        await fsm.handle_event(Event.EMERGENCY_KEYWORD)
        assert fsm.state == State.SAFE_MODE  # Should not change


class TestCrashLoop:
    """Test crash loop detection → SAFE_MODE."""

    @pytest.mark.asyncio
    async def test_crash_loop_triggers_safe_mode(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        # Record crashes within window
        assert fsm.record_crash() is False
        assert fsm.record_crash() is False
        assert fsm.record_crash() is True  # threshold=3

        # FSM should transition to SAFE_MODE
        await fsm.handle_event(Event.CRASH_LOOP)
        assert fsm.state == State.SAFE_MODE
        mocks["enter_safe_mode"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_safe_mode_exit_on_button(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        fsm._state = State.SAFE_MODE
        await fsm.handle_event(Event.BUTTON_PRESS)
        assert fsm.state == State.IDLE
        mocks["attempt_recovery"].assert_awaited_once()

    def test_crash_loop_not_triggered_outside_window(self):
        fsm = FSM(crash_loop_threshold=3, crash_loop_window_s=1)

        fsm.record_crash()
        # Simulate time passing by manipulating timestamps
        fsm._crash_timestamps[-1] -= 2  # Push 2s into the past
        fsm.record_crash()
        fsm._crash_timestamps[-1] -= 2
        result = fsm.record_crash()
        # Oldest crash is now >2s ago, window is 1s
        assert result is False


class TestTurnManagement:
    def test_new_turn_creates_id(self, fsm):
        turn_id = fsm.new_turn()
        assert turn_id is not None
        assert fsm.current_turn_id == turn_id
        assert fsm.cancel_event is not None
        assert not fsm.cancel_event.is_set()

    def test_cancel_turn_sets_event(self, fsm):
        fsm.new_turn()
        cancel = fsm.cancel_event
        fsm.cancel_current_turn()
        assert cancel.is_set()
        assert fsm.current_turn_id is None

    def test_turn_validity(self, fsm):
        turn_id = fsm.new_turn()
        assert fsm.is_turn_valid(turn_id)
        assert not fsm.is_turn_valid("fake-id")

    def test_stale_turn_after_cancel(self, fsm):
        old_turn = fsm.new_turn()
        fsm.cancel_current_turn()
        new_turn = fsm.new_turn()
        assert not fsm.is_turn_valid(old_turn)
        assert fsm.is_turn_valid(new_turn)


class TestStateListeners:
    @pytest.mark.asyncio
    async def test_listener_called_on_transition(self, fsm_with_actions):
        fsm, _ = fsm_with_actions
        listener = AsyncMock()
        fsm.add_state_listener(listener)

        await fsm.handle_event(Event.BUTTON_PRESS)

        listener.assert_awaited_once_with(State.IDLE, State.LISTENING)

    @pytest.mark.asyncio
    async def test_listener_exception_does_not_break_fsm(self, fsm_with_actions):
        fsm, _ = fsm_with_actions

        async def bad_listener(prev, next_state):
            raise RuntimeError("listener error")

        fsm.add_state_listener(bad_listener)

        # Should not raise, FSM continues
        await fsm.handle_event(Event.BUTTON_PRESS)
        assert fsm.state == State.LISTENING


class TestIgnoredEvents:
    @pytest.mark.asyncio
    async def test_vad_end_in_idle_ignored(self, fsm):
        await fsm.handle_event(Event.VAD_END)
        assert fsm.state == State.IDLE

    @pytest.mark.asyncio
    async def test_tts_complete_in_idle_ignored(self, fsm):
        await fsm.handle_event(Event.TTS_COMPLETE)
        assert fsm.state == State.IDLE

    @pytest.mark.asyncio
    async def test_webhook_response_in_idle_ignored(self, fsm):
        await fsm.handle_event(Event.WEBHOOK_RESPONSE)
        assert fsm.state == State.IDLE

    @pytest.mark.asyncio
    async def test_stt_complete_in_idle_ignored(self, fsm):
        await fsm.handle_event(Event.STT_COMPLETE)
        assert fsm.state == State.IDLE


class TestGetValidEvents:
    def test_idle_valid_events(self, fsm):
        valid = fsm.get_valid_events()
        assert Event.BUTTON_PRESS in valid
        assert Event.EMERGENCY_KEYWORD in valid
        assert Event.EMERGENCY_KEY in valid
        assert Event.VAD_END not in valid

    @pytest.mark.asyncio
    async def test_listening_valid_events(self, fsm_with_actions):
        fsm, _ = fsm_with_actions
        await fsm.handle_event(Event.BUTTON_PRESS)
        valid = fsm.get_valid_events()
        assert Event.VAD_START in valid
        assert Event.VAD_END in valid
        assert Event.BUTTON_PRESS in valid
        assert Event.BUTTON_LONG_PRESS in valid
        assert Event.TIMEOUT in valid


class TestProcessingTimeout:
    @pytest.mark.asyncio
    async def test_processing_timeout(self, fsm_with_actions):
        fsm, mocks = fsm_with_actions

        await fsm.handle_event(Event.BUTTON_PRESS)
        await fsm.handle_event(Event.VAD_END)
        assert fsm.state == State.PROCESSING

        await fsm.handle_event(Event.TIMEOUT)
        assert fsm.state == State.ERROR
        mocks["handle_processing_timeout"].assert_awaited_once()
