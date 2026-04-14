import json
from unittest.mock import patch

import pytest

from tools import browser_tool


def test_browser_navigate_blocks_private_host_without_allowlist(monkeypatch):
    monkeypatch.delenv("HERMES_BROWSER_PRIVATE_HOST_ALLOWLIST", raising=False)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda *args, **kwargs: pytest.fail("browser command should not run for blocked private host"),
    )

    with patch("tools.url_safety.socket.getaddrinfo", return_value=[
        (2, 1, 6, "", ("100.72.243.76", 0)),
    ]):
        result = json.loads(browser_tool.browser_navigate("http://100.72.243.76:4321", task_id="t-private"))

    assert result["success"] is False
    assert "private or internal" in result["error"]


def test_browser_navigate_allows_private_host_from_allowlist(monkeypatch):
    monkeypatch.setenv("HERMES_BROWSER_PRIVATE_HOST_ALLOWLIST", "100.72.243.76, localhost")
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(browser_tool, "_get_session_info", lambda task_id: {"_first_nav": False})
    monkeypatch.setattr(browser_tool, "_maybe_start_recording", lambda task_id: None)

    calls: list[tuple[str, str, tuple[str, ...]]] = []

    def fake_run(task_id, command, args, timeout=None):
        calls.append((task_id, command, tuple(args)))
        return {"success": True, "data": {"url": args[0], "title": "Hermes Journal"}}

    monkeypatch.setattr(browser_tool, "_run_browser_command", fake_run)

    with patch("tools.url_safety.socket.getaddrinfo", return_value=[
        (2, 1, 6, "", ("100.72.243.76", 0)),
    ]):
        result = json.loads(browser_tool.browser_navigate("http://100.72.243.76:4321", task_id="t-private"))

    assert result["success"] is True
    assert result["url"] == "http://100.72.243.76:4321"
    assert calls == [("t-private", "open", ("http://100.72.243.76:4321",))]
