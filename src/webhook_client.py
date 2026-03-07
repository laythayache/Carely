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
        logger.info(f"[WEBHOOK] Client ready: url={self.url}, timeout={self.timeout_s}s, max_retries={self.max_retries}")

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
        logger.info(f"[WEBHOOK] Preparing turn request: turn={turn_id}, lang={language}, transcript='{transcript[:80]}'")
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
            logger.debug(f"[WEBHOOK] Request validation passed")
        except jsonschema.ValidationError as e:
            logger.error(f"[WEBHOOK] Request validation FAILED: {e.message}")
            logger.error(f"[WEBHOOK] Request data: {request.to_dict()}")
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
            logger.error("[WEBHOOK] Cannot send - client not started!")
            raise RuntimeError("WebhookClient not started")

        retries = max_retries if max_retries is not None else self.max_retries
        last_error: Exception | None = None
        import time as _time
        total_start = _time.monotonic()

        for attempt in range(retries + 1):
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                logger.warning("[WEBHOOK] Cancelled before attempt")
                raise asyncio.CancelledError("Webhook cancelled")

            attempt_start = _time.monotonic()
            logger.info(f"[WEBHOOK] POST {self.url} (attempt {attempt + 1}/{retries + 1}, turn={request.turn_id})")

            try:
                async with self._session.post(
                    self.url, json=request.to_dict()
                ) as resp:
                    elapsed = int((_time.monotonic() - attempt_start) * 1000)
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info(f"[WEBHOOK] 200 OK in {elapsed}ms, parsing response...")
                        logger.debug(f"[WEBHOOK] Raw response: {str(data)[:500]}")
                        result = self._parse_response(data)
                        logger.info(f"[WEBHOOK] Parsed: spoken_text='{(result.spoken_text or '')[:80]}', lang={result.language}")
                        return result
                    elif resp.status >= 500:
                        body = await resp.text()
                        last_error = RuntimeError(
                            f"Webhook returned {resp.status}: {body[:200]}"
                        )
                        logger.error(
                            f"[WEBHOOK] SERVER ERROR {resp.status} in {elapsed}ms (attempt {attempt + 1}/{retries + 1}): {body[:200]}"
                        )
                    else:
                        body = await resp.text()
                        logger.error(
                            f"[WEBHOOK] HTTP {resp.status} in {elapsed}ms: {body[:200]}"
                        )
                        raise RuntimeError(
                            f"Webhook returned {resp.status}: {body[:200]}"
                        )

            except aiohttp.ClientError as e:
                elapsed = int((_time.monotonic() - attempt_start) * 1000)
                last_error = e
                logger.error(
                    f"[WEBHOOK] CONNECTION ERROR in {elapsed}ms (attempt {attempt + 1}/{retries + 1}): {type(e).__name__}: {e}"
                )
            except asyncio.TimeoutError:
                elapsed = int((_time.monotonic() - attempt_start) * 1000)
                last_error = asyncio.TimeoutError("Webhook timeout")
                logger.error(
                    f"[WEBHOOK] TIMEOUT after {elapsed}ms (attempt {attempt + 1}/{retries + 1}, limit={self.timeout_s}s)"
                )

            # Backoff before retry (if not last attempt)
            if attempt < retries:
                backoff_s = (self.retry_backoff_ms * (2 ** attempt)) / 1000.0
                logger.info(f"[WEBHOOK] Retrying in {backoff_s:.1f}s...")

                if cancel_event:
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=backoff_s)
                        logger.warning("[WEBHOOK] Cancelled during backoff")
                        raise asyncio.CancelledError("Webhook cancelled during backoff")
                    except asyncio.TimeoutError:
                        pass  # Backoff complete, retry
                else:
                    await asyncio.sleep(backoff_s)

        total_elapsed = int((_time.monotonic() - total_start) * 1000)
        logger.error(f"[WEBHOOK] ALL ATTEMPTS FAILED after {total_elapsed}ms ({retries + 1} attempts): {last_error}")
        raise WebhookUnavailableError(
            f"Webhook failed after {retries + 1} attempts: {last_error}"
        )

    def _parse_response(self, data: dict[str, Any]) -> WebhookResponse:
        """Validate and parse webhook response."""
        try:
            jsonschema.validate(data, RESPONSE_SCHEMA)
        except jsonschema.ValidationError as e:
            logger.error(f"[WEBHOOK] Response validation FAILED: {e.message}")
            logger.error(f"[WEBHOOK] Bad response data: {data}")
            raise

        return WebhookResponse(
            turn_id=data["turn_id"],
            spoken_text=data["spoken_text"],
            language=data.get("language", "en"),
            voice_id=data.get("voice_id", ""),
            control=data.get("control", {}),
        )
