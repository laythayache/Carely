"""
Local fallback agent for offline intent handling.
Handles limited intents when the webhook is unavailable:
- Time/date queries
- Device status
- Help instructions
- Emergency guidance
- Reminder playback (optional)

No LLM — pure regex pattern matching.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class FallbackResponse:
    spoken_text: str
    language: str = "en"
    priority: str = "normal"


# Intent definitions: name → (patterns, handler_method_name)
INTENTS: dict[str, dict] = {
    "time": {
        "patterns": [
            r"what time",
            r"current time",
            r"tell me the time",
            r"what's the time",
            # Arabizi
            r"shu lwa2t",
            r"2addesh lsa3a",
            r"addesh elsa3a",
            r"aya se3a",
        ],
    },
    "date": {
        "patterns": [
            r"what day",
            r"what date",
            r"what is today",
            r"today's date",
            r"which day",
            # Arabizi
            r"shu lyom",
            r"aya yom",
        ],
    },
    "device_status": {
        "patterns": [
            r"are you (working|ok|online|there)",
            r"(device|system) (status|health)",
            r"how are you",
            r"can you hear me",
            r"hello",
            r"test",
        ],
    },
    "help": {
        "patterns": [
            r"^help$",
            r"what can you do",
            r"how (do|does) this work",
            r"how to use",
            r"instructions",
        ],
    },
    "emergency_guidance": {
        "patterns": [
            r"chest pain",
            r"can't breathe",
            r"cannot breathe",
            r"heart attack",
            r"i fell",
            r"i'm (hurt|bleeding|dizzy)",
            r"call (ambulance|doctor|hospital|help)",
            r"i need help",
            r"emergency",
        ],
    },
}


class FallbackAgent:
    """
    Pattern-matching intent handler for offline operation.
    """

    def match(self, transcript: str) -> FallbackResponse | None:
        """
        Try to match transcript against known patterns.
        Returns FallbackResponse if matched, None if no match.
        """
        text = transcript.lower().strip()
        if not text:
            return None

        for intent_name, intent in INTENTS.items():
            for pattern in intent["patterns"]:
                if re.search(pattern, text):
                    handler = getattr(self, f"_handle_{intent_name}", None)
                    if handler:
                        return handler(text)

        return None

    def get_generic_error(self) -> FallbackResponse:
        """Return a generic error message when no intent matches."""
        return FallbackResponse(
            spoken_text=(
                "I'm sorry, I cannot reach the server right now. "
                "Please try again in a moment, or press the button and ask again."
            ),
            language="en",
        )

    def get_offline_emergency(self) -> FallbackResponse:
        """Return hardcoded emergency guidance when webhook is unavailable."""
        return FallbackResponse(
            spoken_text=(
                "I cannot reach emergency services through the internet right now. "
                "If this is a medical emergency, please call 1 1 2 immediately. "
                "Stay calm. If you can, sit or lie down in a safe position. "
                "Do not try to move if you have fallen and feel pain."
            ),
            language="en",
            priority="urgent",
        )

    def _handle_time(self, transcript: str) -> FallbackResponse:
        now = datetime.now()
        time_str = now.strftime("%I:%M %p")
        return FallbackResponse(
            spoken_text=f"The current time is {time_str}.",
            language="en",
        )

    def _handle_date(self, transcript: str) -> FallbackResponse:
        now = datetime.now()
        date_str = now.strftime("%A, %B %d, %Y")
        return FallbackResponse(
            spoken_text=f"Today is {date_str}.",
            language="en",
        )

    def _handle_device_status(self, transcript: str) -> FallbackResponse:
        return FallbackResponse(
            spoken_text=(
                "I am working, but I cannot connect to the server right now. "
                "I can still tell you the time or date."
            ),
            language="en",
        )

    def _handle_help(self, transcript: str) -> FallbackResponse:
        return FallbackResponse(
            spoken_text=(
                "To use this device, press the button and speak your question. "
                "I will listen, understand, and respond. "
                "You can ask me anything, or say help if you need assistance. "
                "In an emergency, say the word help or press the emergency button."
            ),
            language="en",
        )

    def _handle_emergency_guidance(self, transcript: str) -> FallbackResponse:
        return self.get_offline_emergency()
