import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import workspace_backlog_tool


def _issue(
    identifier: str,
    *,
    title: str,
    project_name: str,
    state_name: str,
    state_type: str,
    updated_at: datetime,
    priority: int = 0,
    delegate: dict | None = None,
    assignee: dict | None = None,
    inverse_relations: list[dict] | None = None,
    relations: list[dict] | None = None,
) -> dict:
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": title,
        "url": f"https://linear.app/hadto/issue/{identifier.lower()}",
        "createdAt": updated_at.isoformat(),
        "updatedAt": updated_at.isoformat(),
        "priority": priority,
        "delegate": delegate,
        "assignee": assignee,
        "state": {"id": f"state-{identifier}", "name": state_name, "type": state_type},
        "team": {"id": "team-1", "key": "HAD", "name": "Hadto"},
        "project": {"id": f"project-{project_name}", "name": project_name},
        "labels": [],
        "comments": [],
        "inverseRelations": inverse_relations or [],
        "relations": relations or [],
    }


def test_parse_time_accepts_epoch_seconds_string():
    parsed = workspace_backlog_tool._parse_time("1775937402.8603923")

    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.year == 2026


def test_workspace_backlog_orchestrator_selects_actionable_started_issue_and_persists_state(
    monkeypatch, tmp_path
):
    now = datetime(2026, 4, 12, 16, 0, 0, tzinfo=timezone.utc)
    hermes_user = {
        "id": "user-hermes",
        "name": "Hermes",
        "displayName": "hermes",
        "email": "hermes@oauthapp.linear.app",
        "active": True,
    }
    david_user = {
        "id": "user-david",
        "name": "david@hadto.net",
        "displayName": "david",
        "email": "david@hadto.net",
        "active": True,
    }

    monkeypatch.setattr(workspace_backlog_tool.linear_tool, "_list_users", lambda: [hermes_user, david_user])
    monkeypatch.setattr(
        workspace_backlog_tool.linear_tool,
        "_list_issues",
        lambda *, limit=100, filter_input=None: [
            _issue(
                "HAD-101",
                title="Add OC",
                project_name="Home Server Deployment",
                state_name="In Progress",
                state_type="started",
                updated_at=now - timedelta(days=10),
                assignee=david_user,
            ),
            _issue(
                "HAD-271",
                title="Repair self-improvement reliability floor",
                project_name="Hermes Self-Improvement",
                state_name="In Progress",
                state_type="started",
                updated_at=now - timedelta(hours=2),
                priority=1,
                delegate=hermes_user,
            ),
            _issue(
                "HAD-283",
                title="Harden Hermes Linear webhooks against transient agent-session bootstrap failures",
                project_name="Hermes Self-Improvement",
                state_name="Todo",
                state_type="unstarted",
                updated_at=now - timedelta(hours=6),
                priority=1,
            ),
        ],
    )

    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.yaml"
    codex_runs_path = tmp_path / "runs.json"
    codex_runs_path.write_text(json.dumps({"runs": {}}), encoding="utf-8")

    result = workspace_backlog_tool.evaluate_workspace_backlog(
        team_key="HAD",
        config_path=config_path,
        state_path=state_path,
        codex_runs_path=codex_runs_path,
        stale_hours=24,
        persist=True,
        now=now,
    )

    assert result["selected_issue"]["identifier"] == "HAD-271"
    assert result["selected_issue"]["ownership"] == "hermes"
    assert result["selected_issue"]["repo_root"] == "/home/david/stacks/hermes-agent"
    assert result["counts"]["human_owned"] == 1
    assert state_path.exists() is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["selected"]["identifier"] == "HAD-271"


def test_workspace_backlog_orchestrator_can_claim_unowned_issue_and_write_comment(monkeypatch, tmp_path):
    now = datetime(2026, 4, 12, 16, 0, 0, tzinfo=timezone.utc)
    hermes_user = {
        "id": "user-hermes",
        "name": "Hermes",
        "displayName": "hermes",
        "email": "hermes@oauthapp.linear.app",
        "active": True,
    }
    calls: list[dict] = []

    monkeypatch.setattr(workspace_backlog_tool.linear_tool, "_list_users", lambda: [hermes_user])
    monkeypatch.setattr(
        workspace_backlog_tool.linear_tool,
        "_list_issues",
        lambda *, limit=100, filter_input=None: [
            _issue(
                "HAD-283",
                title="Harden Hermes Linear webhooks against transient agent-session bootstrap failures",
                project_name="Hermes Self-Improvement",
                state_name="Todo",
                state_type="unstarted",
                updated_at=now - timedelta(hours=4),
                priority=1,
            )
        ],
    )
    monkeypatch.setattr(
        workspace_backlog_tool.linear_tool,
        "linear_issue",
        lambda args, **_kw: calls.append(args)
        or json.dumps(
            {
                "success": True,
                "issue": {
                    "id": "id-HAD-283",
                    "identifier": "HAD-283",
                    "delegate": {"id": "user-hermes"},
                },
            }
            if args.get("action") == "issue_upsert"
            else {"success": True, "comment": {"id": "comment-1"}}
        ),
    )

    codex_runs_path = tmp_path / "runs.json"
    codex_runs_path.write_text(json.dumps({"runs": {}}), encoding="utf-8")

    result = workspace_backlog_tool.evaluate_workspace_backlog(
        team_key="HAD",
        config_path=tmp_path / "config.yaml",
        state_path=tmp_path / "state.json",
        codex_runs_path=codex_runs_path,
        auto_delegate=True,
        write_status_comment=True,
        persist=False,
        now=now,
    )

    assert result["selected_issue"]["identifier"] == "HAD-283"
    assert calls[0] == {
        "action": "issue_upsert",
        "identifier": "HAD-283",
        "delegate_id": "user-hermes",
    }
    assert calls[1]["action"] == "comment"
    assert calls[1]["identifier"] == "HAD-283"
    assert calls[1]["dedupe_key"] == "workspace-orchestrator:HAD-283"
