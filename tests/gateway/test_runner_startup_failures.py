import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner
from gateway.status import read_runtime_status


class _RetryableFailureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        self._set_fatal_error(
            "telegram_connect_error",
            "Telegram startup failed: temporary DNS resolution failure.",
            retryable=True,
        )
        return False

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


class _DisabledAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=False, token="***"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        raise AssertionError("connect should not be called for disabled platforms")

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.asyncio
async def test_runner_returns_failure_for_retryable_startup_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _RetryableFailureAdapter())

    ok = await runner.start()

    assert ok is False
    assert runner.should_exit_cleanly is False
    state = read_runtime_status()
    assert state["gateway_state"] == "startup_failed"
    assert "temporary DNS resolution failure" in state["exit_reason"]
    assert state["platforms"]["telegram"]["state"] == "fatal"
    assert state["platforms"]["telegram"]["error_code"] == "telegram_connect_error"


@pytest.mark.asyncio
async def test_runner_allows_cron_only_mode_when_no_platforms_are_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=False, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    ok = await runner.start()

    assert ok is True
    assert runner.should_exit_cleanly is False
    assert runner.adapters == {}
    state = read_runtime_status()
    assert state["gateway_state"] == "running"


@pytest.mark.asyncio
async def test_runner_startup_resumes_interrupted_codex_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=False, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    calls = []

    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_resume_interrupted_codex_runs",
        lambda: (lambda: calls.append("called") or {"enabled": True, "resumed": [], "skipped": [], "errors": []}),
    )

    ok = await runner.start()

    assert ok is True
    assert calls == ["called"]


@pytest.mark.asyncio
async def test_start_gateway_accepts_verbosity_argument(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.should_exit_cleanly = True
            self.exit_reason = None
            self.adapters = {}

        async def start(self):
            return True

        async def stop(self):
            return None

    import logging
    import gateway.run as gateway_run

    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr(gateway_run, "GatewayRunner", _CleanExitRunner)
    monkeypatch.setattr(gateway_run, "RotatingFileHandler", lambda *args, **kwargs: logging.NullHandler())

    try:
        ok = await gateway_run.start_gateway(config=GatewayConfig(), replace=False, verbosity=1)
        assert ok is True
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in saved_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(saved_level)
