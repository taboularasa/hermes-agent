import json
import logging
import yaml

import pytest


def _run_scheduled_job_with_fallback(monkeypatch, tmp_path, fallback_payload):
    """Run a cron job with temporary Hermes config and return captured AIAgent kwargs."""

    from cron import scheduler

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(fallback_payload))
    monkeypatch.setattr(scheduler, "_hermes_home", hermes_home)

    captured = {}

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "api_key": "x",
            "base_url": "https://x",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.format_runtime_provider_error",
        lambda exc: str(exc),
    )
    monkeypatch.setattr(
        "agent.smart_model_routing.resolve_turn_route",
        lambda *_args, **_kwargs: {
            "model": "openrouter/primary",
            "runtime": {
                "api_key": "x",
                "base_url": "https://x",
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "command": None,
                "args": [],
            },
        },
    )

    job = {
        "id": "cron-1",
        "name": "fallback test",
        "prompt": "ping",
    }

    success, _, _, _ = scheduler.run_job(job)
    assert success is True
    return captured["kwargs"]["fallback_model"]


@pytest.mark.parametrize(
    "fallback_key, fallback_payload",
    [
        ("fallback_providers", [{"provider": "openai", "model": "gpt-4o-mini"}]),
        ("fallback_model", {"provider": "openai", "model": "gpt-4o-mini"}),
    ],
)
def test_cron_run_job_forwards_configured_fallback_model(tmp_path, monkeypatch, fallback_key, fallback_payload):
    """Cron jobs should pass fallback provider configuration from config.yaml into AIAgent."""

    cfg = {
        fallback_key: fallback_payload,
        "provider_routing": {},
        "smart_model_routing": {},
    }

    forwarded = _run_scheduled_job_with_fallback(monkeypatch, tmp_path, cfg)
    assert forwarded == cfg[fallback_key]


def test_cron_run_job_logs_fallback_model_type_and_count(monkeypatch, tmp_path, caplog):
    """Cron should log fallback provider type/count so configuration bugs are visible."""

    from cron import scheduler

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    cfg = {
        "fallback_providers": [
            {"provider": "openai", "model": "gpt-4o-mini"},
            {"provider": "groq", "model": "llama-3.3-70b-versatile"},
        ],
        "provider_routing": {},
        "smart_model_routing": {},
    }
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(scheduler, "_hermes_home", hermes_home)

    class FakeAIAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "api_key": "***",
            "base_url": "https://x",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.format_runtime_provider_error",
        lambda exc: str(exc),
    )
    monkeypatch.setattr(
        "agent.smart_model_routing.resolve_turn_route",
        lambda *_args, **_kwargs: {
            "model": "openrouter/primary",
            "runtime": {
                "api_key": "***",
                "base_url": "https://x",
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "command": None,
                "args": [],
            },
        },
    )

    with caplog.at_level(logging.DEBUG, logger="cron.scheduler"):
        job = {
            "id": "cron-logging",
            "name": "fallback log test",
            "prompt": "ping",
        }
        success, _, _, _ = scheduler.run_job(job)

    assert success is True
    assert any(
        "fallback_model resolved from config.yaml" in record.message
        and "type=list" in record.message
        and "count=2" in record.message
        for record in caplog.records
    )


def test_cron_run_job_loads_config_from_runtime_hermes_home(monkeypatch, tmp_path):
    """Cron should re-read HERMES_HOME from launched runtime and use its config.yaml."""

    from cron import scheduler

    launcher_home = tmp_path / "launcher-home"
    launcher_home.mkdir()
    fallback_model = {"provider": "openrouter", "model": "qwen/qwen-2.5-72b-instruct"}

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir()
    (runtime_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "fallback_model": fallback_model,
                "provider_routing": {},
                "smart_model_routing": {},
            }
        )
    )

    (launcher_home / ".env").write_text(f"HERMES_HOME={runtime_home}\n")
    (launcher_home / "config.yaml").write_text(yaml.safe_dump({"fallback_model": {"provider": "invalid", "model": "ignored"}}))

    # Keep imported/runtime split behavior: _hermes_home initially points at launcher_home.
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(launcher_home))
    monkeypatch.setattr(scheduler, "_hermes_home", launcher_home)
    monkeypatch.setattr(scheduler, "_HERMES_HOME_IMPORTED", launcher_home)

    captured = {}

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "api_key": "***",
            "base_url": "https://x",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.format_runtime_provider_error",
        lambda exc: str(exc),
    )
    monkeypatch.setattr(
        "agent.smart_model_routing.resolve_turn_route",
        lambda *_args, **_kwargs: {
            "model": "openrouter/primary",
            "runtime": {
                "api_key": "***",
                "base_url": "https://x",
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "command": None,
                "args": [],
            },
        },
    )

    job = {
        "id": "cron-runtime",
        "name": "runtime home test",
        "prompt": "ping",
    }
    success, _, _, _ = scheduler.run_job(job)

    assert success is True
    assert captured["kwargs"]["fallback_model"] == fallback_model


def test_cron_run_job_loads_relative_prefill_messages_from_runtime_hermes_home(monkeypatch, tmp_path):
    """Cron should load prefill_messages_file from the runtime-chosen Hermes home."""

    from cron import scheduler

    launcher_home = tmp_path / "launcher-home"
    launcher_home.mkdir()
    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir()

    prefill_messages = [
        {"role": "system", "content": "Prefill from runtime config"},
    ]

    (runtime_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "prefill_messages_file": "prefill.json",
                "provider_routing": {},
                "smart_model_routing": {},
            }
        )
    )
    (runtime_home / "prefill.json").write_text(json.dumps(prefill_messages))

    (launcher_home / ".env").write_text(f"HERMES_HOME={runtime_home}\n")
    (launcher_home / "config.yaml").write_text(yaml.safe_dump({"provider_routing": {}, "smart_model_routing": {}}))

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(launcher_home))
    monkeypatch.setattr(scheduler, "_hermes_home", launcher_home)
    monkeypatch.setattr(scheduler, "_HERMES_HOME_IMPORTED", launcher_home)

    captured = {}

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def run_conversation(self, prompt):
            return {"final_response": "ok"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "api_key": "***",
            "base_url": "https://x",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.format_runtime_provider_error",
        lambda exc: str(exc),
    )
    monkeypatch.setattr(
        "agent.smart_model_routing.resolve_turn_route",
        lambda *_args, **_kwargs: {
            "model": "openrouter/primary",
            "runtime": {
                "api_key": "***",
                "base_url": "https://x",
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "command": None,
                "args": [],
            },
        },
    )

    job = {
        "id": "cron-prefill",
        "name": "prefill home test",
        "prompt": "ping",
    }
    success, _, _, _ = scheduler.run_job(job)

    assert success is True
    assert captured["kwargs"]["prefill_messages"] == prefill_messages


def test_resolve_hermes_home_prefers_environment_when_not_overridden(monkeypatch, tmp_path):
    """_resolve_hermes_home should honor runtime env HERMES_HOME when no override applies."""

    from cron import scheduler

    launcher_home = tmp_path / "launcher"
    launcher_home.mkdir()
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()

    monkeypatch.setattr(scheduler, "_hermes_home", launcher_home)
    monkeypatch.setattr(scheduler, "_HERMES_HOME_IMPORTED", launcher_home)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(runtime_home))

    assert scheduler._resolve_hermes_home() == runtime_home


def test_resolve_hermes_home_prefers_explicit_override(monkeypatch, tmp_path):
    """An explicitly patched _hermes_home should win over env var once import-time value is different."""

    from cron import scheduler

    imported_home = tmp_path / "imported"
    imported_home.mkdir()
    override_home = tmp_path / "override"
    override_home.mkdir()
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()

    monkeypatch.setattr(scheduler, "_hermes_home", override_home)
    monkeypatch.setattr(scheduler, "_HERMES_HOME_IMPORTED", imported_home)
    monkeypatch.setenv("HERMES_HOME", str(runtime_home))

    assert scheduler._resolve_hermes_home() == override_home
