"""Tests for webhook JSON schema validation."""

import pytest
import jsonschema
from src.webhook_client import REQUEST_SCHEMA, RESPONSE_SCHEMA


class TestRequestSchema:
    def _valid_request(self, **overrides):
        base = {
            "device_id": "carely-proto-001",
            "turn_id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": "2025-01-01T00:00:00Z",
            "transcript": "Hello how are you",
            "language": "en",
            "language_confidence": 0.95,
            "is_emergency": False,
            "metadata": {"stt_latency_ms": 1500},
        }
        base.update(overrides)
        return base

    def test_valid_request_passes(self):
        jsonschema.validate(self._valid_request(), REQUEST_SCHEMA)

    def test_missing_transcript_fails(self):
        req = self._valid_request()
        del req["transcript"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_missing_device_id_fails(self):
        req = self._valid_request()
        del req["device_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_missing_turn_id_fails(self):
        req = self._valid_request()
        del req["turn_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_missing_language_fails(self):
        req = self._valid_request()
        del req["language"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_invalid_language_enum(self):
        req = self._valid_request(language="french")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_empty_transcript_fails(self):
        req = self._valid_request(transcript="")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_language_confidence_out_of_range(self):
        req = self._valid_request(language_confidence=1.5)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(req, REQUEST_SCHEMA)

    def test_all_valid_languages(self):
        for lang in ["en", "ar", "mixed", "unknown"]:
            jsonschema.validate(self._valid_request(language=lang), REQUEST_SCHEMA)

    def test_emergency_flag_types(self):
        jsonschema.validate(self._valid_request(is_emergency=True), REQUEST_SCHEMA)
        jsonschema.validate(self._valid_request(is_emergency=False), REQUEST_SCHEMA)

    def test_minimal_valid_request(self):
        """Only required fields."""
        req = {
            "device_id": "test",
            "turn_id": "abc-123",
            "timestamp": "2025-01-01T00:00:00Z",
            "transcript": "hello",
            "language": "en",
        }
        jsonschema.validate(req, REQUEST_SCHEMA)


class TestResponseSchema:
    def _valid_response(self, **overrides):
        base = {
            "turn_id": "550e8400-e29b-41d4-a716-446655440000",
            "spoken_text": "Hello! I'm doing well.",
            "language": "en",
            "control": {
                "ui_state": "speaking",
                "priority": "normal",
                "action": "none",
            },
        }
        base.update(overrides)
        return base

    def test_valid_response_passes(self):
        jsonschema.validate(self._valid_response(), RESPONSE_SCHEMA)

    def test_missing_turn_id_fails(self):
        resp = self._valid_response()
        del resp["turn_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, RESPONSE_SCHEMA)

    def test_missing_spoken_text_fails(self):
        resp = self._valid_response()
        del resp["spoken_text"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, RESPONSE_SCHEMA)

    def test_minimal_response(self):
        """Only required fields."""
        resp = {"turn_id": "abc", "spoken_text": "Hello"}
        jsonschema.validate(resp, RESPONSE_SCHEMA)

    def test_extra_fields_tolerated(self):
        """Forward-compatibility: unknown fields should not fail validation."""
        resp = self._valid_response()
        resp["future_field"] = "some_value"
        jsonschema.validate(resp, RESPONSE_SCHEMA)

    def test_invalid_language_enum(self):
        resp = self._valid_response(language="french")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, RESPONSE_SCHEMA)

    def test_with_voice_id(self):
        resp = self._valid_response(voice_id="en_US-lessac-medium")
        jsonschema.validate(resp, RESPONSE_SCHEMA)
