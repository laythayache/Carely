"""
Carely device configuration loader and validator.
Reads from .env file using python-dotenv, validates all fields.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


@dataclass(frozen=True)
class Config:
    # Device Identity
    device_id: str = "carely-proto-001"

    # Webhook
    webhook_url: str = "http://192.168.1.100:5678/webhook/carely"
    webhook_timeout_s: float = 8.0
    webhook_max_retries: int = 2
    webhook_retry_backoff_ms: int = 500
    webhook_auth_token: str = ""

    # Audio Devices
    audio_input_device: str = "default"
    audio_output_device: str = "default"

    # VAD
    vad_aggressiveness: int = 3
    vad_preroll_ms: int = 300
    vad_speech_start_ms: int = 30
    vad_silence_end_ms: int = 800
    vad_min_speech_ms: int = 500
    vad_max_utterance_ms: int = 30000

    # STT
    whisper_model_path: str = "/opt/assistant/models/whisper/ggml-base.bin"
    whisper_binary_path: str = "/opt/assistant/bin/whisper-cli"
    whisper_threads: int = 3
    whisper_language: str = "auto"

    # TTS
    piper_binary_path: str = "/opt/assistant/bin/piper"
    piper_voice_en: str = "/opt/assistant/models/piper/en_US-lessac-medium.onnx"
    piper_voice_ar: str = "/opt/assistant/models/piper/ar_JO-kareem-medium.onnx"
    piper_default_voice: str = "en"

    # Keyword Spotter
    keyword_spotter_enabled: bool = True
    keyword_spotter_threshold: float = 0.7

    # Input
    button_key: str = "space"
    emergency_key: str = "f12"
    long_press_ms: int = 1000

    # UI
    ui_host: str = "0.0.0.0"
    ui_port: int = 8080

    # Logging
    log_level: str = "INFO"
    log_file: str = "/var/log/carely/carely.log"

    # System
    health_port: int = 8081
    watchdog_interval_s: int = 15
    crash_loop_threshold: int = 5
    crash_loop_window_s: int = 300

    # IPC
    ipc_socket_path: str = "/run/carely/audio.sock"


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got: {val!r}")


def _get_env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        raise ConfigError(f"{key} must be a float, got: {val!r}")


def _get_env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def load_config(env_path: str | Path | None = None) -> Config:
    """
    Load configuration from .env file and environment variables.
    Environment variables override .env file values.
    """
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    config = Config(
        device_id=_get_env("DEVICE_ID", Config.device_id),
        webhook_url=_get_env("WEBHOOK_URL", Config.webhook_url),
        webhook_timeout_s=_get_env_float("WEBHOOK_TIMEOUT_S", Config.webhook_timeout_s),
        webhook_max_retries=_get_env_int("WEBHOOK_MAX_RETRIES", Config.webhook_max_retries),
        webhook_retry_backoff_ms=_get_env_int("WEBHOOK_RETRY_BACKOFF_MS", Config.webhook_retry_backoff_ms),
        webhook_auth_token=_get_env("WEBHOOK_AUTH_TOKEN", Config.webhook_auth_token),
        audio_input_device=_get_env("AUDIO_INPUT_DEVICE", Config.audio_input_device),
        audio_output_device=_get_env("AUDIO_OUTPUT_DEVICE", Config.audio_output_device),
        vad_aggressiveness=_get_env_int("VAD_AGGRESSIVENESS", Config.vad_aggressiveness),
        vad_preroll_ms=_get_env_int("VAD_PREROLL_MS", Config.vad_preroll_ms),
        vad_speech_start_ms=_get_env_int("VAD_SPEECH_START_MS", Config.vad_speech_start_ms),
        vad_silence_end_ms=_get_env_int("VAD_SILENCE_END_MS", Config.vad_silence_end_ms),
        vad_min_speech_ms=_get_env_int("VAD_MIN_SPEECH_MS", Config.vad_min_speech_ms),
        vad_max_utterance_ms=_get_env_int("VAD_MAX_UTTERANCE_MS", Config.vad_max_utterance_ms),
        whisper_model_path=_get_env("WHISPER_MODEL_PATH", Config.whisper_model_path),
        whisper_binary_path=_get_env("WHISPER_BINARY_PATH", Config.whisper_binary_path),
        whisper_threads=_get_env_int("WHISPER_THREADS", Config.whisper_threads),
        whisper_language=_get_env("WHISPER_LANGUAGE", Config.whisper_language),
        piper_binary_path=_get_env("PIPER_BINARY_PATH", Config.piper_binary_path),
        piper_voice_en=_get_env("PIPER_VOICE_EN", Config.piper_voice_en),
        piper_voice_ar=_get_env("PIPER_VOICE_AR", Config.piper_voice_ar),
        piper_default_voice=_get_env("PIPER_DEFAULT_VOICE", Config.piper_default_voice),
        keyword_spotter_enabled=_get_env_bool("KEYWORD_SPOTTER_ENABLED", Config.keyword_spotter_enabled),
        keyword_spotter_threshold=_get_env_float("KEYWORD_SPOTTER_THRESHOLD", Config.keyword_spotter_threshold),
        button_key=_get_env("BUTTON_KEY", Config.button_key),
        emergency_key=_get_env("EMERGENCY_KEY", Config.emergency_key),
        long_press_ms=_get_env_int("LONG_PRESS_MS", Config.long_press_ms),
        ui_host=_get_env("UI_HOST", Config.ui_host),
        ui_port=_get_env_int("UI_PORT", Config.ui_port),
        log_level=_get_env("LOG_LEVEL", Config.log_level),
        log_file=_get_env("LOG_FILE", Config.log_file),
        health_port=_get_env_int("HEALTH_PORT", Config.health_port),
        watchdog_interval_s=_get_env_int("WATCHDOG_INTERVAL_S", Config.watchdog_interval_s),
        crash_loop_threshold=_get_env_int("CRASH_LOOP_THRESHOLD", Config.crash_loop_threshold),
        crash_loop_window_s=_get_env_int("CRASH_LOOP_WINDOW_S", Config.crash_loop_window_s),
    )

    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    """Validate configuration values. Raises ConfigError on invalid values."""
    errors: list[str] = []

    if not config.device_id:
        errors.append("DEVICE_ID must not be empty")

    if not config.webhook_url.startswith(("http://", "https://")):
        errors.append(f"WEBHOOK_URL must start with http:// or https://, got: {config.webhook_url}")

    if config.webhook_timeout_s <= 0:
        errors.append(f"WEBHOOK_TIMEOUT_S must be positive, got: {config.webhook_timeout_s}")

    if config.webhook_max_retries < 0:
        errors.append(f"WEBHOOK_MAX_RETRIES must be >= 0, got: {config.webhook_max_retries}")

    if config.vad_aggressiveness not in (0, 1, 2, 3):
        errors.append(f"VAD_AGGRESSIVENESS must be 0-3, got: {config.vad_aggressiveness}")

    if config.vad_preroll_ms < 0 or config.vad_preroll_ms > 2000:
        errors.append(f"VAD_PREROLL_MS must be 0-2000, got: {config.vad_preroll_ms}")

    if config.vad_silence_end_ms < 100 or config.vad_silence_end_ms > 5000:
        errors.append(f"VAD_SILENCE_END_MS must be 100-5000, got: {config.vad_silence_end_ms}")

    if config.vad_max_utterance_ms < 1000:
        errors.append(f"VAD_MAX_UTTERANCE_MS must be >= 1000, got: {config.vad_max_utterance_ms}")

    if config.whisper_threads < 1 or config.whisper_threads > 16:
        errors.append(f"WHISPER_THREADS must be 1-16, got: {config.whisper_threads}")

    if config.whisper_language != "auto" and len(config.whisper_language) != 2:
        errors.append(f"WHISPER_LANGUAGE must be 'auto' or 2-letter code, got: {config.whisper_language}")

    if config.piper_default_voice not in ("en", "ar"):
        errors.append(f"PIPER_DEFAULT_VOICE must be 'en' or 'ar', got: {config.piper_default_voice}")

    if config.keyword_spotter_threshold < 0.0 or config.keyword_spotter_threshold > 1.0:
        errors.append(f"KEYWORD_SPOTTER_THRESHOLD must be 0.0-1.0, got: {config.keyword_spotter_threshold}")

    if config.ui_port < 1 or config.ui_port > 65535:
        errors.append(f"UI_PORT must be 1-65535, got: {config.ui_port}")

    if config.health_port < 1 or config.health_port > 65535:
        errors.append(f"HEALTH_PORT must be 1-65535, got: {config.health_port}")

    if config.ui_port == config.health_port:
        errors.append(f"UI_PORT and HEALTH_PORT must be different, both are: {config.ui_port}")

    if config.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        errors.append(f"LOG_LEVEL must be a valid Python log level, got: {config.log_level}")

    if config.long_press_ms < 200:
        errors.append(f"LONG_PRESS_MS must be >= 200, got: {config.long_press_ms}")

    if config.crash_loop_threshold < 2:
        errors.append(f"CRASH_LOOP_THRESHOLD must be >= 2, got: {config.crash_loop_threshold}")

    if errors:
        msg = "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(msg)

    logger.info("Configuration validated successfully")


def setup_logging(config: Config) -> None:
    """Configure logging based on config values."""
    log_dir = Path(config.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        file_handler = logging.FileHandler(config.log_file)
        handlers.append(file_handler)
    except OSError:
        # If we can't write to log file (e.g. in dev mode), just use stderr
        pass

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
