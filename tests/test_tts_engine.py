"""Tests for TTS language resolution."""

from src.tts_engine import TTSEngine


def _engine() -> TTSEngine:
    return TTSEngine(
        binary_path="/opt/assistant/bin/piper",
        voice_paths={
            "en": "/opt/assistant/models/piper/en_US-lessac-medium.onnx",
            "ar": "/opt/assistant/models/piper/ar_JO-kareem-medium.onnx",
        },
        default_language="en",
    )


def test_resolve_exact_arabic_key() -> None:
    assert _engine()._resolve_language("ar") == "ar"


def test_resolve_arabic_aliases() -> None:
    engine = _engine()
    assert engine._resolve_language("arabic") == "ar"
    assert engine._resolve_language("ara") == "ar"


def test_resolve_arabic_locale_tags() -> None:
    engine = _engine()
    assert engine._resolve_language("ar-JO") == "ar"
    assert engine._resolve_language("ar_SA") == "ar"


def test_resolve_english_locale_tags() -> None:
    engine = _engine()
    assert engine._resolve_language("en-US") == "en"
    assert engine._resolve_language("english") == "en"


def test_resolve_unknown_language_falls_back_to_default() -> None:
    assert _engine()._resolve_language("fr") == "en"
