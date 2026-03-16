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
    # No required fields - we'll extract what we can
    "properties": {
        "turn_id": {"type": "string"},
        "spoken_text": {"type": "string"},
        "text": {"type": "string"},  # n8n AI Agent output field
        "output": {"type": "string"},  # Alternative n8n field
        "response": {"type": "string"},  # Another common field
        "message": {"type": "string"},  # Another common field
        "language": {"type": "string"},
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
        timeout = aiohttp.ClientTimeout(total=self.timeout_s if self.timeout_s > 0 else None)
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
        # Map unsupported languages to 'unknown' (keep original in metadata)
        supported_languages = {'en', 'ar', 'mixed', 'unknown'}
        original_language = language
        if language not in supported_languages:
            logger.info(f"[WEBHOOK] Detected language '{language}' not in supported set, mapping to 'unknown'")
            language = 'unknown'

        logger.info(f"[WEBHOOK] Preparing turn request: turn={turn_id}, lang={language} (detected={original_language}), transcript='{transcript[:80]}'")
        
        # Include original detected language in metadata
        meta = metadata or {}
        if original_language != language:
            meta['detected_language'] = original_language
        
        request = WebhookRequest(
            device_id=self.device_id,
            turn_id=turn_id,
            transcript=transcript,
            language=language,
            language_confidence=language_confidence,
            is_emergency=is_emergency,
            metadata=meta,
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
                        raw_text = await resp.text()
                        elapsed = int((_time.monotonic() - attempt_start) * 1000)
                        logger.info(f"[WEBHOOK] 200 OK in {elapsed}ms, raw body ({len(raw_text)} bytes): {raw_text[:500]}")
                        
                        # Handle empty body
                        if not raw_text or raw_text.strip() == "":
                            logger.error(f"[WEBHOOK] Empty response body from webhook")
                            raise RuntimeError("Webhook returned empty response body")
                        
                        # Parse JSON
                        import json
                        try:
                            data = json.loads(raw_text)
                        except json.JSONDecodeError as e:
                            logger.error(f"[WEBHOOK] Invalid JSON: {e}")
                            raise RuntimeError(f"Webhook returned invalid JSON: {raw_text[:200]}")
                        
                        # Handle null JSON value
                        if data is None:
                            logger.error(f"[WEBHOOK] JSON parsed to null: {raw_text[:200]}")
                            raise RuntimeError("Webhook returned JSON null")
                        
                        result = self._parse_response(data, fallback_turn_id=request.turn_id)
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

    def _parse_response(self, data: dict[str, Any], fallback_turn_id: str = "") -> WebhookResponse:
        """Parse webhook response flexibly to handle various n8n output formats."""
        
        # Handle n8n array wrapper - take first element if array
        if isinstance(data, list):
            if len(data) == 0:
                raise ValueError("Webhook returned empty array")
            data = data[0]
            logger.debug(f"[WEBHOOK] Unwrapped array response, using first element")
        
        if not isinstance(data, dict):
            # If it's just a string, treat as spoken_text
            if isinstance(data, str):
                logger.info(f"[WEBHOOK] Response is plain string, using as spoken_text")
                return WebhookResponse(
                    turn_id=fallback_turn_id,
                    spoken_text=data,
                    language="en",
                )
            raise ValueError(f"Unexpected response type: {type(data).__name__}")
        
        # Extract spoken_text from various n8n output field names
        spoken_text = None
        for field in ["spoken_text", "text", "output", "response", "message", "content", "answer"]:
            if field in data and data[field]:
                spoken_text = str(data[field])
                logger.debug(f"[WEBHOOK] Found spoken_text in field '{field}'")
                break
        
        # If no text field found, check for nested structures (n8n AI Agent format)
        if not spoken_text:
            # Check for n8n AI Agent nested output: {"output": {"text": "..."}}
            if isinstance(data.get("output"), dict):
                nested = data["output"]
                for field in ["text", "content", "message", "response"]:
                    if field in nested and nested[field]:
                        spoken_text = str(nested[field])
                        logger.debug(f"[WEBHOOK] Found spoken_text in nested output.{field}")
                        break
        
        if not spoken_text:
            logger.error(f"[WEBHOOK] Could not extract text from response: {data}")
            raise ValueError(f"No text content found in webhook response. Keys: {list(data.keys())}")
        
        # Extract turn_id or use fallback
        turn_id = data.get("turn_id", "") or fallback_turn_id
        
        # Extract language (flexible)
        language = data.get("language", "en")
        if language not in ["en", "ar"]:
            language = "en"  # Default to English for TTS
        
        return WebhookResponse(
            turn_id=turn_id,
            spoken_text=spoken_text,
            language=language,
            voice_id=data.get("voice_id", ""),
            control=data.get("control", {}),
        )
