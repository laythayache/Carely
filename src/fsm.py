"""
Carely Finite State Machine.

Manages all conversation states and transitions.
Single-button, VAD-assisted interaction model with emergency override.
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    SPEAKING = auto()
    ERROR = auto()
    EMERGENCY = auto()
    SAFE_MODE = auto()


class Event(Enum):
    BUTTON_PRESS = auto()
    BUTTON_LONG_PRESS = auto()
    VAD_START = auto()
    VAD_END = auto()
    STT_COMPLETE = auto()
    WEBHOOK_RESPONSE = auto()
    WEBHOOK_TIMEOUT = auto()
    TTS_COMPLETE = auto()
    EMERGENCY_KEYWORD = auto()
    EMERGENCY_KEY = auto()
    TIMEOUT = auto()
    CRASH_LOOP = auto()


@dataclass
class TransitionResult:
    """Result of an FSM transition, carrying data to the next handler."""
    turn_id: str | None = None
    pcm_data: bytes | None = None
    transcript: str | None = None
    language: str | None = None
    language_confidence: float = 0.0
    spoken_text: str | None = None
    voice_language: str | None = None
    error_message: str | None = None
    is_emergency: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# Type alias for action callbacks
ActionCallback = Callable[[TransitionResult | None], Coroutine[Any, Any, None]]


class FSM:
    """
    Finite State Machine for Carely conversation control.

    Invariants:
    - Only one turn is active at a time
    - LISTENING always has an active audio capture session
    - PROCESSING always has a valid cancel_event
    - SPEAKING always has an active TTS subprocess
    - EMERGENCY overrides any state except SAFE_MODE
    - SAFE_MODE can only be exited by explicit user action
    """

    def __init__(
        self,
        crash_loop_threshold: int = 5,
        crash_loop_window_s: int = 300,
    ):
        self._state = State.IDLE
        self._current_turn_id: str | None = None
        self._cancel_event: asyncio.Event | None = None
        self._timeout_task: asyncio.Task | None = None
        self._is_recording: bool = False

        # Crash loop tracking
        self._crash_loop_threshold = crash_loop_threshold
        self._crash_loop_window_s = crash_loop_window_s
        self._crash_timestamps: deque[float] = deque(maxlen=crash_loop_threshold)

        # State change listeners
        self._state_listeners: list[Callable[[State, State], Coroutine[Any, Any, None]]] = []

        # Action callbacks (set by the orchestrator)
        self._actions: dict[str, ActionCallback] = {}

        # Transition table: (current_state, event) -> (next_state, action_name)
        self._transitions: dict[tuple[State, Event], tuple[State, str]] = {
            # IDLE transitions
            (State.IDLE, Event.BUTTON_PRESS): (State.LISTENING, "start_listening"),
            (State.IDLE, Event.EMERGENCY_KEYWORD): (State.EMERGENCY, "handle_emergency"),
            (State.IDLE, Event.EMERGENCY_KEY): (State.EMERGENCY, "handle_emergency"),

            # LISTENING transitions
            (State.LISTENING, Event.VAD_START): (State.LISTENING, "on_vad_start"),
            (State.LISTENING, Event.VAD_END): (State.PROCESSING, "process_speech"),
            (State.LISTENING, Event.BUTTON_PRESS): (State.IDLE, "cancel_listening"),
            (State.LISTENING, Event.BUTTON_LONG_PRESS): (State.PROCESSING, "force_process_speech"),
            (State.LISTENING, Event.TIMEOUT): (State.IDLE, "cancel_listening"),

            # PROCESSING transitions
            (State.PROCESSING, Event.STT_COMPLETE): (State.PROCESSING, "send_webhook"),
            (State.PROCESSING, Event.WEBHOOK_RESPONSE): (State.SPEAKING, "start_speaking"),
            (State.PROCESSING, Event.WEBHOOK_TIMEOUT): (State.ERROR, "handle_webhook_failure"),
            (State.PROCESSING, Event.BUTTON_PRESS): (State.IDLE, "cancel_processing"),
            (State.PROCESSING, Event.TIMEOUT): (State.ERROR, "handle_processing_timeout"),

            # SPEAKING transitions
            (State.SPEAKING, Event.TTS_COMPLETE): (State.IDLE, "on_tts_complete"),
            (State.SPEAKING, Event.BUTTON_PRESS): (State.IDLE, "barge_in"),
            (State.SPEAKING, Event.EMERGENCY_KEYWORD): (State.EMERGENCY, "emergency_during_speech"),

            # ERROR transitions
            (State.ERROR, Event.BUTTON_PRESS): (State.IDLE, "dismiss_error"),
            (State.ERROR, Event.TIMEOUT): (State.IDLE, "dismiss_error"),

            # EMERGENCY transitions
            (State.EMERGENCY, Event.WEBHOOK_RESPONSE): (State.SPEAKING, "start_speaking"),
            (State.EMERGENCY, Event.WEBHOOK_TIMEOUT): (State.SPEAKING, "speak_offline_emergency"),
            (State.EMERGENCY, Event.TIMEOUT): (State.IDLE, "dismiss_error"),

            # SAFE_MODE transitions
            (State.SAFE_MODE, Event.BUTTON_PRESS): (State.IDLE, "attempt_recovery"),

            # Global crash loop transition (handled specially)
        }

    @property
    def state(self) -> State:
        return self._state

    @property
    def current_turn_id(self) -> str | None:
        return self._current_turn_id

    @property
    def cancel_event(self) -> asyncio.Event | None:
        return self._cancel_event

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def set_recording(self, recording: bool) -> None:
        self._is_recording = recording

    def register_action(self, name: str, callback: ActionCallback) -> None:
        """Register an action callback for a transition."""
        self._actions[name] = callback

    def add_state_listener(
        self, listener: Callable[[State, State], Coroutine[Any, Any, None]]
    ) -> None:
        """Add a listener that is called on every state change."""
        self._state_listeners.append(listener)

    def new_turn(self) -> str:
        """Create a new turn ID and cancel event. Returns the turn ID."""
        old_turn = self._current_turn_id
        self._current_turn_id = str(uuid.uuid4())
        self._cancel_event = asyncio.Event()
        logger.info(f"[FSM] New turn created: {self._current_turn_id} (previous: {old_turn})")
        return self._current_turn_id

    def cancel_current_turn(self) -> None:
        """Cancel the current turn by setting the cancel event."""
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            logger.info(f"[FSM] Turn cancelled: {self._current_turn_id}")
        else:
            logger.info(f"[FSM] Turn cleanup (no active cancel_event): {self._current_turn_id}")
        self._current_turn_id = None
        self._cancel_event = None

    def is_turn_valid(self, turn_id: str) -> bool:
        """Check if a turn ID matches the current active turn."""
        valid = turn_id == self._current_turn_id
        if not valid:
            logger.warning(f"[FSM] Turn validation FAILED: {turn_id} != current {self._current_turn_id}")
        return valid

    def _cancel_timeout(self) -> None:
        """Cancel any pending timeout task."""
        if self._timeout_task and not self._timeout_task.done():
            logger.debug(f"[FSM] Cancelling pending timeout task")
            self._timeout_task.cancel()
            self._timeout_task = None

    def _schedule_timeout(self, seconds: float) -> None:
        """Schedule a TIMEOUT event after the given delay."""
        self._cancel_timeout()
        logger.info(f"[FSM] Scheduling timeout in {seconds}s for state {self._state.name}")

        async def _timeout_fire():
            try:
                await asyncio.sleep(seconds)
                logger.warning(f"[FSM] TIMEOUT fired after {seconds}s in state {self._state.name}")
                await self.handle_event(Event.TIMEOUT)
            except asyncio.CancelledError:
                logger.debug(f"[FSM] Timeout task cancelled (was {seconds}s for {self._state.name})")

        self._timeout_task = asyncio.create_task(_timeout_fire())

    def record_crash(self) -> bool:
        """
        Record a crash timestamp. Returns True if crash loop detected.
        """
        now = time.monotonic()
        self._crash_timestamps.append(now)

        if len(self._crash_timestamps) >= self._crash_loop_threshold:
            oldest = self._crash_timestamps[0]
            if now - oldest <= self._crash_loop_window_s:
                return True
        return False

    async def handle_event(self, event: Event, data: TransitionResult | None = None) -> None:
        """
        Process an event through the FSM.
        Looks up the transition, changes state, notifies listeners, and runs the action.
        """
        logger.info(f"[FSM] handle_event: {event.name} in state {self._state.name} (turn={self._current_turn_id})")

        # Special case: CRASH_LOOP can happen from any state except SAFE_MODE
        if event == Event.CRASH_LOOP and self._state != State.SAFE_MODE:
            logger.error(f"[FSM] CRASH_LOOP detected in {self._state.name} -> SAFE_MODE")
            await self._transition_to(State.SAFE_MODE, "enter_safe_mode", data)
            return

        # Emergency events override any state except SAFE_MODE
        if event in (Event.EMERGENCY_KEYWORD, Event.EMERGENCY_KEY):
            if self._state == State.SAFE_MODE:
                logger.warning("[FSM] Ignoring emergency in SAFE_MODE")
                return
            if self._state not in (State.IDLE, State.SPEAKING):
                logger.warning(f"[FSM] Emergency override: cancelling current work in {self._state.name}")
                self.cancel_current_turn()
                self._cancel_timeout()

            key = (self._state, event)
            if key not in self._transitions:
                logger.warning(f"[FSM] Emergency from non-standard state {self._state.name}: forcing to EMERGENCY")
                await self._transition_to(State.EMERGENCY, "handle_emergency", data)
                return

        key = (self._state, event)
        if key not in self._transitions:
            logger.warning(f"[FSM] No transition for ({self._state.name}, {event.name}) - event IGNORED")
            return

        next_state, action_name = self._transitions[key]
        logger.info(f"[FSM] Transition found: {self._state.name} + {event.name} -> {next_state.name} [{action_name}]")
        await self._transition_to(next_state, action_name, data)

    async def _transition_to(
        self, next_state: State, action_name: str, data: TransitionResult | None
    ) -> None:
        """Execute a state transition."""
        prev_state = self._state
        self._state = next_state

        logger.info(f"[FSM] ===== {prev_state.name} --> {next_state.name} [{action_name}] (turn={self._current_turn_id}) =====")

        # Cancel timeout on any state change
        self._cancel_timeout()

        # Schedule timeouts for states that need them
        if next_state == State.LISTENING:
            self._schedule_timeout(15.0)
        elif next_state == State.ERROR:
            self._schedule_timeout(10.0)
        elif next_state == State.EMERGENCY:
            self._schedule_timeout(30.0)

        # Notify state listeners
        for listener in self._state_listeners:
            try:
                await listener(prev_state, next_state)
            except Exception:
                logger.exception(f"[FSM] State listener EXCEPTION during {prev_state.name} -> {next_state.name}")

        # Run action callback
        action = self._actions.get(action_name)
        if action:
            logger.info(f"[FSM] Running action '{action_name}'...")
            try:
                await action(data)
                logger.info(f"[FSM] Action '{action_name}' completed (now in state {self._state.name})")
            except Exception as e:
                logger.exception(f"[FSM] ACTION EXCEPTION: '{action_name}' raised {type(e).__name__}: {e}")
                if next_state not in (State.ERROR, State.SAFE_MODE):
                    error_data = TransitionResult(
                        error_message=f"Action '{action_name}' failed: {e}"
                    )
                    logger.error(f"[FSM] Recovering from action failure - firing TIMEOUT to go to ERROR")
                    await self.handle_event(Event.TIMEOUT, error_data)
        else:
            logger.warning(f"[FSM] No action registered for '{action_name}'")

    def get_valid_events(self) -> list[Event]:
        """Return the list of events valid in the current state."""
        return [event for (state, event) in self._transitions if state == self._state]

    def __repr__(self) -> str:
        return f"FSM(state={self._state.name}, turn={self._current_turn_id})"
