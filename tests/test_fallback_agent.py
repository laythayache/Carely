"""Tests for the offline fallback agent."""

import pytest
from src.fallback_agent import FallbackAgent


@pytest.fixture
def agent():
    return FallbackAgent()


class TestTimeIntent:
    def test_what_time(self, agent):
        result = agent.match("What time is it?")
        assert result is not None
        assert "time" in result.spoken_text.lower()

    def test_current_time(self, agent):
        result = agent.match("current time please")
        assert result is not None

    def test_arabizi_time(self, agent):
        result = agent.match("shu lwa2t?")
        assert result is not None

    def test_arabizi_time_2(self, agent):
        result = agent.match("2addesh lsa3a?")
        assert result is not None


class TestDateIntent:
    def test_what_day(self, agent):
        result = agent.match("What day is it?")
        assert result is not None
        assert result.language == "en"

    def test_what_date(self, agent):
        result = agent.match("What's the date today?")
        assert result is not None

    def test_arabizi_date(self, agent):
        result = agent.match("shu lyom?")
        assert result is not None


class TestDeviceStatus:
    def test_are_you_working(self, agent):
        result = agent.match("Are you working?")
        assert result is not None

    def test_can_you_hear_me(self, agent):
        result = agent.match("Can you hear me?")
        assert result is not None

    def test_hello(self, agent):
        result = agent.match("hello")
        assert result is not None


class TestHelp:
    def test_help_exact(self, agent):
        result = agent.match("help")
        assert result is not None
        assert "button" in result.spoken_text.lower() or "press" in result.spoken_text.lower()

    def test_what_can_you_do(self, agent):
        result = agent.match("What can you do?")
        assert result is not None

    def test_how_to_use(self, agent):
        result = agent.match("How to use this?")
        assert result is not None


class TestEmergencyGuidance:
    def test_chest_pain(self, agent):
        result = agent.match("I'm having chest pain")
        assert result is not None
        assert result.priority == "urgent"
        assert "112" in result.spoken_text or "emergency" in result.spoken_text.lower()

    def test_cant_breathe(self, agent):
        result = agent.match("I can't breathe")
        assert result is not None
        assert result.priority == "urgent"

    def test_fell(self, agent):
        result = agent.match("I fell down")
        assert result is not None

    def test_call_ambulance(self, agent):
        result = agent.match("Call an ambulance")
        assert result is not None


class TestNoMatch:
    def test_random_text(self, agent):
        result = agent.match("qwerty asdfgh zxcvbn")
        assert result is None

    def test_empty_string(self, agent):
        result = agent.match("")
        assert result is None

    def test_whitespace(self, agent):
        result = agent.match("   ")
        assert result is None


class TestGenericError:
    def test_generic_error_message(self, agent):
        result = agent.get_generic_error()
        assert result is not None
        assert "server" in result.spoken_text.lower() or "try again" in result.spoken_text.lower()


class TestOfflineEmergency:
    def test_offline_emergency(self, agent):
        result = agent.get_offline_emergency()
        assert result is not None
        assert result.priority == "urgent"
        assert "112" in result.spoken_text
