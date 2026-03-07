"""Tests for configuration loading and validation."""

import os
import pytest
from src.config import Config, load_config, validate_config, ConfigError


class TestConfigDefaults:
    def test_default_device_id(self, default_config):
        assert default_config.device_id == "carely-proto-001"

    def test_default_vad_aggressiveness(self, default_config):
        assert default_config.vad_aggressiveness == 3

    def test_default_webhook_timeout(self, default_config):
        assert default_config.webhook_timeout_s == 120.0

    def test_default_whisper_threads(self, default_config):
        assert default_config.whisper_threads == 3

    def test_default_ports_different(self, default_config):
        assert default_config.ui_port != default_config.health_port


class TestConfigValidation:
    def test_valid_defaults_pass(self, default_config):
        validate_config(default_config)  # should not raise

    def test_invalid_vad_aggressiveness(self):
        config = Config(vad_aggressiveness=5)
        with pytest.raises(ConfigError, match="VAD_AGGRESSIVENESS"):
            validate_config(config)

    def test_invalid_webhook_url(self):
        config = Config(webhook_url="ftp://bad-url")
        with pytest.raises(ConfigError, match="WEBHOOK_URL"):
            validate_config(config)

    def test_negative_webhook_timeout(self):
        config = Config(webhook_timeout_s=-1.0)
        with pytest.raises(ConfigError, match="WEBHOOK_TIMEOUT_S"):
            validate_config(config)

    def test_invalid_log_level(self):
        config = Config(log_level="VERBOSE")
        with pytest.raises(ConfigError, match="LOG_LEVEL"):
            validate_config(config)

    def test_same_ports(self):
        config = Config(ui_port=8080, health_port=8080)
        with pytest.raises(ConfigError, match="UI_PORT and HEALTH_PORT"):
            validate_config(config)

    def test_invalid_whisper_language(self):
        config = Config(whisper_language="english")
        with pytest.raises(ConfigError, match="WHISPER_LANGUAGE"):
            validate_config(config)

    def test_invalid_piper_default_voice(self):
        config = Config(piper_default_voice="fr")
        with pytest.raises(ConfigError, match="PIPER_DEFAULT_VOICE"):
            validate_config(config)

    def test_keyword_threshold_out_of_range(self):
        config = Config(keyword_spotter_threshold=1.5)
        with pytest.raises(ConfigError, match="KEYWORD_SPOTTER_THRESHOLD"):
            validate_config(config)

    def test_too_short_long_press(self):
        config = Config(long_press_ms=50)
        with pytest.raises(ConfigError, match="LONG_PRESS_MS"):
            validate_config(config)


class TestConfigLoading:
    def test_load_from_env_file(self, test_env):
        config = load_config(test_env)
        assert config.device_id == "test-device"
        assert config.webhook_url == "http://localhost:5678/webhook/test"
        assert config.log_level == "DEBUG"
        assert config.ui_port == 9090

    def test_env_var_override(self, test_env, monkeypatch):
        monkeypatch.setenv("DEVICE_ID", "override-device")
        config = load_config(test_env)
        assert config.device_id == "override-device"

    def test_invalid_int_env_var(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("UI_PORT=not_a_number\n")
        with pytest.raises(ConfigError, match="UI_PORT must be an integer"):
            load_config(env_file)

    def test_bool_parsing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEYWORD_SPOTTER_ENABLED=false\n")
        config = load_config(env_file)
        assert config.keyword_spotter_enabled is False
