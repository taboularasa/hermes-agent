"""Tests for home-channel onboarding prompts."""

from gateway.config import Platform
from gateway.run import _home_channel_setup_command, _home_channel_setup_message


def test_slack_home_channel_prompt_uses_hermes_subcommand():
    assert _home_channel_setup_command(Platform.SLACK) == "/hermes sethome"
    assert "Type /hermes sethome" in _home_channel_setup_message(Platform.SLACK)


def test_telegram_home_channel_prompt_uses_direct_command():
    assert _home_channel_setup_command(Platform.TELEGRAM) == "/sethome"
    prompt = _home_channel_setup_message(Platform.TELEGRAM)
    assert "Type /sethome" in prompt
    assert "/hermes sethome" not in prompt
