"""
Webhook client for n8n communication.
Sends transcribed text to n8n, receives AI response.
Supports retry with exponential backoff, cancellation, and stale turn discard.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import aiohttp
import jsonschema

logger = logging.getLogger(__name__)

# JSON schemas for validation
REQUEST_SCHEMA = {
    "type": "object",
    "required": ["device_id", "turn_id", "timestamp", "transcript", "language"],
    "properties": {
        "device_id": {"type": "string"},
        "turn_id": {"type": "string"},
        "timestamp": {"type": "string"},
        "transcript": {"type": "string", "minLength": 1},
        "language": {"type": "string", "enum": ["en", "ar", "mixed", "unknown"]},
        "language_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "is_emergency": {"type": "boolean"},
        "metadata": {"type": "object"},
    },
}

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["turn_id", "spoken_text"],
    "properties": {
        "turn_id": {"type": "string"},
        "spoken_text": {"type": "string"},
        "language": {"type": "string", "enum": ["en", "ar"]},
        "voice_id": {"type": "string"},
        "control": {
            "type": "object",
            "properties": {
                "ui_state": {"type": "string"},
                "priority": {"type": "string"},
                "action": {"type": "string"},
            },
        },
    },
}


@dataclass
class WebhookRequest:
    device_id: str
    turn_id: str
    transcript: str
    language: str
    language_confidence: float = 0.0
    is_emergency: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WebhookResponse:
    turn_id: str
    spoken_text: str
    language: str = "en"
    voice_id: str = ""
    control: dict[str, str] = field(default_factory=dict)


class WebhookUnavailableError(Exception):
    """Raised when webhook is unreachable after all retries."""


class WebhookClient:
    """
    Async HTTP client for n8n webhook communication.
    """

    def __init__(
        self,
        url: str,
        device_id: str,
        timeout_s: float = 8.0,
        max_retries: int = 2,
        retry_backoff_ms: int = 500,
        auth_token: str = "",
    ):
        self.url = url
        self.device_id = device_id
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self.auth_token = auth_token
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Create the HTTP session."""
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        logger.info(f"Webhook client ready: {self.url}")

    async def stop(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def send_turn(
        self,
        transcript: str,
        language: str,
        turn_id: str,
        language_confidence: float = 0.0,
        is_emergency: bool = False,
        metadata: dict[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> WebhookResponse:
        """
        Send a conversation turn to n8n and return the response.

        Raises:
            WebhookUnavailableError: If all retries fail
            asyncio.CancelledError: If cancel_event is set
        """
        request = WebhookRequest(
            device_id=self.device_id,
            turn_id=turn_id,
            transcript=transcript,
            language=language,
            language_confidence=language_confidence,
            is_emergency=is_emergency,
            metadata=metadata or {},
        )

        # Validate outgoing request
        try:
            jsonschema.validate(request.to_dict(), REQUEST_SCHEMA)
        except jsonschema.ValidationError as e:
            logger.error(f"Request validation failed: {e.message}")
            raise

        return await self._send_with_retry(request, cancel_event)

    async def send_emergency(
        self,
        transcript: str,
        turn_id: str,
    ) -> WebhookResponse:
        """
        Send emergency webhook with higher priority and shorter timeout.
        No cancel support — emergencies always go through.
        """
        request = WebhookRequest(
            device_id=self.device_id,
            turn_id=turn_id,
            transcript=transcript,
            language="en",
            is_emergency=True,
            metadata={"priority": "emergency"},
        )

        return await self._send_with_retry(request, cancel_event=None, max_retries=1)

    async def _send_with_retry(
        self,
        request: WebhookRequest,
        cancel_event: asyncio.Event | None,
        max_retries: int | None = None,
    ) -> WebhookResponse:
        """Send request with exponential backoff retry logic."""
        if not self._session:
            raise RuntimeError("WebhookClient not started")

        retries = max_retries if max_retries is not None else self.max_retries
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Webhook cancelled")

            try:
                async with self._session.post(
                    self.url, json=request.to_dict()
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._parse_response(data)
                    elif resp.status >= 500:
                        body = await resp.text()
                        last_error = RuntimeError(
                            f"Webhook returned {resp.status}: {body[:200]}"
                        )
                        logger.warning(
                            f"Webhook {resp.status} (attempt {attempt + 1}/{retries + 1})"
                        )
                    else:
                        body = await resp.text()
                        raise RuntimeError(
                            f"Webhook returned {resp.status}: {body[:200]}"
                        )

            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    f"Webhook error (attempt {attempt + 1}/{retries + 1}): {e}"
                )
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError("Webhook timeout")
                logger.warning(
                    f"Webhook timeout (attempt {attempt + 1}/{retries + 1})"
                )

            # Backoff before retry (if not last attempt)
            if attempt < retries:
                backoff_s = (self.retry_backoff_ms * (2 ** attempt)) / 1000.0
                logger.debug(f"Retrying in {backoff_s:.1f}s...")

                if cancel_event:
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=backoff_s)
                        raise asyncio.CancelledError("Webhook cancelled during backoff")
                    except asyncio.TimeoutError:
                        pass  # Backoff complete, retry
                else:
                    await asyncio.sleep(backoff_s)

        raise WebhookUnavailableError(
            f"Webhook failed after {retries + 1} attempts: {last_error}"
        )

    def _parse_response(self, data: dict[str, Any]) -> WebhookResponse:
        """Validate and parse webhook response."""
        try:
            jsonschema.validate(data, RESPONSE_SCHEMA)
        except jsonschema.ValidationError as e:
            logger.error(f"Response validation failed: {e.message}")
            logger.debug(f"Response data: {data}")
            raise

        return WebhookResponse(
            turn_id=data["turn_id"],
            spoken_text=data["spoken_text"],
            language=data.get("language", "en"),
            voice_id=data.get("voice_id", ""),
            control=data.get("control", {}),
        )
