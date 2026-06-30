import json

import model_tools

from cron.workspace_coordinator_policy import (
    DEFAULT_IGNORED_PROJECTS,
    activate_workspace_coordinator_policy,
    clear_workspace_coordinator_policy,
    normalize_workspace_coordinator_config,
)


def test_workspace_orchestrator_auto_delegate_defaults_off_even_when_prompt_requests_it(monkeypatch):
    captured = {}

    def fake_dispatch(function_name, function_args, **kwargs):
        captured["function_name"] = function_name
        captured["function_args"] = dict(function_args)
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    token = activate_workspace_coordinator_policy({})
    try:
        result = model_tools.handle_function_call(
            "workspace_backlog_orchestrator",
            {"auto_delegate": True, "team_key": "HAD"},
        )
    finally:
        clear_workspace_coordinator_policy(token)

    assert json.loads(result) == {"ok": True}
    assert captured["function_name"] == "workspace_backlog_orchestrator"
    assert captured["function_args"]["team_key"] == "HAD"
    assert captured["function_args"]["auto_delegate"] is False


def test_workspace_orchestrator_auto_delegate_requires_typed_enable(monkeypatch):
    captured = {}

    def fake_dispatch(function_name, function_args, **kwargs):
        captured["function_args"] = dict(function_args)
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    token = activate_workspace_coordinator_policy({"auto_delegate": True})
    try:
        model_tools.handle_function_call(
            "workspace_backlog_orchestrator",
            {"auto_delegate": False, "team_key": "HAD"},
        )
    finally:
        clear_workspace_coordinator_policy(token)

    assert captured["function_args"]["auto_delegate"] is True


def test_workspace_coordinator_policy_keeps_hadto_ignored_projects_as_code_defaults():
    config = normalize_workspace_coordinator_config({})

    assert config["auto_delegate"] is False
    for project_name in ("De Novo", "Symphony", "Hermes Agent Upstream", "dojoMOO"):
        assert project_name in DEFAULT_IGNORED_PROJECTS
        assert project_name in config["ignored_projects"]


def test_workspace_coordinator_policy_blocks_direct_delegation_for_ignored_projects(monkeypatch):
    def fake_dispatch(function_name, function_args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("dispatch should be blocked before delegation")

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    token = activate_workspace_coordinator_policy({"auto_delegate": True})
    try:
        result = model_tools.handle_function_call(
            "delegate_task",
            {"goal": "Implement selected Symphony backlog item", "toolsets": ["terminal"]},
        )
    finally:
        clear_workspace_coordinator_policy(token)

    payload = json.loads(result)
    assert "error" in payload
    assert "Symphony" in payload["error"]
    assert "workspace coordinator typed policy" in payload["error"]
