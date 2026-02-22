"""Shared test fixtures for Carely test suite."""

import os
import pytest
from src.config import Config


@pytest.fixture
def default_config() -> Config:
    """Return a Config with all defaults (no .env needed)."""
    return Config()


@pytest.fixture
def test_env(tmp_path):
    """Create a temporary .env file and set env vars for testing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEVICE_ID=test-device\n"
        "WEBHOOK_URL=http://localhost:5678/webhook/test\n"
        "LOG_LEVEL=DEBUG\n"
        "UI_PORT=9090\n"
        "HEALTH_PORT=9091\n"
        "LOG_FILE=/tmp/carely-test.log\n"
    )
    return env_file
