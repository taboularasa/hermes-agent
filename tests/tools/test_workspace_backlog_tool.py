import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import codex_delegate_tool
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


def _empty_git_hygiene() -> dict:
    return {
        "counts": {
            "incidents": 0,
            "orphaned_dirty": 0,
            "linked_wip": 0,
            "cleanup_candidates": 0,
            "active_owned": 0,
        },
        "incidents": [],
        "selected": None,
    }


def test_parse_time_accepts_epoch_seconds_string():
    parsed = workspace_backlog_tool._parse_time("1775937402.8603923")

    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.year == 2026


def test_load_active_ctx_bindings_normalizes_stale_records(tmp_path):
    now = datetime(2026, 4, 12, 16, 0, 0, tzinfo=timezone.utc)
    stale = (now - timedelta(hours=13)).isoformat()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    ctx_bindings_path = tmp_path / "session_bindings.json"
    ctx_bindings_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "sess-stale": {
                        "session_id": "sess-stale",
                        "task_id": "task-1",
                        "active": True,
                        "reason": "ctx task bound",
                        "updated_at": stale,
                        "worktree_path": str(worktree),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    active = workspace_backlog_tool._load_active_ctx_bindings(ctx_bindings_path, now=now)
    persisted = json.loads(ctx_bindings_path.read_text(encoding="utf-8"))

    assert active == {}
    assert persisted["sessions"]["sess-stale"]["active"] is False
    assert persisted["sessions"]["sess-stale"]["reason"] == "ctx binding retired: stale active binding (>12h)"


def test_load_codex_run_indexes_normalizes_dead_running_records(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    last_message_path = tmp_path / "codex-final.txt"
    last_message_path.write_text("Completed the work.", encoding="utf-8")
    codex_runs_path = tmp_path / "runs.json"
    codex_runs_path.write_text(
        json.dumps(
            {
                "runs": {
                    "codex_dead": {
                        "run_id": "codex_dead",
                        "status": "running",
                        "pid": 8181,
                        "started_at": "2026-04-12T16:00:00+00:00",
                        "external_key": "linear:HAD-271",
                        "workdir": str(repo),
                        "last_message_path": str(last_message_path),
                        "record_path": str(tmp_path / "codex_dead.json"),
                        "latest_path": str(tmp_path / "latest.json"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_kill(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(codex_delegate_tool.os, "kill", fake_kill)

    indexes = workspace_backlog_tool._load_codex_run_indexes(codex_runs_path)
    persisted = json.loads(codex_runs_path.read_text(encoding="utf-8"))

    assert str(repo.resolve()) not in indexes["active_by_workdir"]
    assert indexes["by_issue"]["HAD-271"]["status"] == "completed"
    assert persisted["runs"]["codex_dead"]["status"] == "completed"
    assert persisted["runs"]["codex_dead"]["stale_reason"] == "process_missing"


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
    monkeypatch.setattr(workspace_backlog_tool, "_collect_git_hygiene", lambda **_kw: _empty_git_hygiene())
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
    assert result["selected_work"]["identifier"] == "HAD-271"
    assert result["selected_work"]["kind"] == "linear_issue"
    assert result["selected_issue"]["ownership"] == "hermes"
    assert result["selected_issue"]["repo_root"] == "/home/david/stacks/hermes-agent"
    assert result["counts"]["human_owned"] == 1
    assert state_path.exists() is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["selected"]["identifier"] == "HAD-271"
    assert persisted["selected_work"]["identifier"] == "HAD-271"


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
    monkeypatch.setattr(workspace_backlog_tool, "_collect_git_hygiene", lambda **_kw: _empty_git_hygiene())
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
    assert result["selected_work"]["identifier"] == "HAD-283"
    assert calls[0] == {
        "action": "issue_upsert",
        "identifier": "HAD-283",
        "delegate_id": "user-hermes",
    }
    assert calls[1]["action"] == "comment"
    assert calls[1]["identifier"] == "HAD-283"
    assert calls[1]["dedupe_key"] == "workspace-orchestrator:HAD-283"


def test_workspace_backlog_orchestrator_prefers_git_hygiene_and_comments_on_linked_issue(monkeypatch, tmp_path):
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
                "HAD-300",
                title="Recover linked dirty worktree",
                project_name="Hermes Self-Improvement",
                state_name="In Progress",
                state_type="started",
                updated_at=now - timedelta(hours=3),
                priority=1,
                delegate=hermes_user,
            ),
            _issue(
                "HAD-301",
                title="Fresh backlog item",
                project_name="Hermes Self-Improvement",
                state_name="Todo",
                state_type="unstarted",
                updated_at=now - timedelta(hours=1),
            ),
        ],
    )
    monkeypatch.setattr(
        workspace_backlog_tool,
        "_collect_git_hygiene",
        lambda **_kw: {
            "counts": {
                "incidents": 1,
                "orphaned_dirty": 0,
                "linked_wip": 1,
                "cleanup_candidates": 0,
                "active_owned": 0,
            },
            "incidents": [
                {
                    "kind": "git_hygiene",
                    "identifier": "HAD-300",
                    "selection_bucket": "reconcile_linked_wip",
                    "execution_mode": "investigate_merge_or_delete",
                    "recommended_action": "resume_or_merge_linked_wip",
                    "selection_reason": "Dirty linked worktree is unowned.",
                    "linked_issue_identifier": "HAD-300",
                    "linked_issue_open": True,
                    "repo_root": "/home/david/stacks/hermes-agent",
                    "worktree_path": "/tmp/worktree",
                    "deletion_blockers": ["linked Linear issue HAD-300 still open"],
                }
            ],
            "selected": {
                "kind": "git_hygiene",
                "identifier": "HAD-300",
                "selection_bucket": "reconcile_linked_wip",
                "execution_mode": "investigate_merge_or_delete",
                "recommended_action": "resume_or_merge_linked_wip",
                "selection_reason": "Dirty linked worktree is unowned.",
                "linked_issue_identifier": "HAD-300",
                "linked_issue_open": True,
                "repo_root": "/home/david/stacks/hermes-agent",
                "worktree_path": "/tmp/worktree",
                "deletion_blockers": ["linked Linear issue HAD-300 still open"],
            },
        },
    )
    monkeypatch.setattr(
        workspace_backlog_tool.linear_tool,
        "linear_issue",
        lambda args, **_kw: calls.append(args) or json.dumps({"success": True, "comment": {"id": "comment-1"}}),
    )

    codex_runs_path = tmp_path / "runs.json"
    codex_runs_path.write_text(json.dumps({"runs": {}}), encoding="utf-8")
    ctx_bindings_path = tmp_path / "session_bindings.json"
    ctx_bindings_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

    result = workspace_backlog_tool.evaluate_workspace_backlog(
        team_key="HAD",
        config_path=tmp_path / "config.yaml",
        state_path=tmp_path / "state.json",
        codex_runs_path=codex_runs_path,
        ctx_bindings_path=ctx_bindings_path,
        write_status_comment=True,
        persist=False,
        now=now,
    )

    assert result["selected_issue"]["identifier"] == "HAD-300"
    assert result["selected_work"]["kind"] == "git_hygiene"
    assert result["selected_work"]["linked_issue_identifier"] == "HAD-300"
    assert calls == [
        {
            "action": "comment",
            "identifier": "HAD-300",
            "body": workspace_backlog_tool._format_git_hygiene_comment(
                selected=result["selected_work"],
                counts=result["counts"],
                hygiene_counts=result["git_hygiene"]["counts"],
            ),
            "dedupe_key": "workspace-orchestrator:git-hygiene:HAD-300",
        }
    ]


def test_git_hygiene_incident_is_conservative_for_orphaned_dirty_state(monkeypatch):
    now = datetime(2026, 4, 12, 16, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        workspace_backlog_tool,
        "_git_branch_divergence",
        lambda *_args, **_kw: {"ahead": 3, "behind": 0, "error": ""},
    )
    monkeypatch.setattr(
        workspace_backlog_tool,
        "_git_head_merged_to_default",
        lambda *_args, **_kw: False,
    )

    incident = workspace_backlog_tool._git_hygiene_incident(
        repo_root="/repo",
        worktree={"worktree_path": "/repo", "branch": "feat/test"},
        status={
            "available": True,
            "worktree_path": "/repo",
            "branch": "feat/test",
            "upstream": "",
            "ahead": 0,
            "behind": 0,
            "tracked_dirty": 2,
            "untracked": 1,
            "dirty": True,
        },
        default_branch="main",
        active_ctx_records=[],
        active_codex=None,
        latest_codex={
            "run_id": "codex_1",
            "status": "completed",
            "completed_at": (now - timedelta(hours=36)).isoformat(),
        },
        linked_issue=None,
        now=now,
        dirty_stale_hours=24,
    )

    assert incident is not None
    assert incident["selection_bucket"] == "resolve_orphaned_dirty_wip"
    assert incident["recommended_action"] == "investigate_unowned_dirty_state"
    assert incident["preempts_backlog"] is True
    assert incident["deletion_candidate"] is False
    assert "HEAD not merged into main" in incident["deletion_blockers"]
    assert "tracked file modifications still present" in incident["deletion_blockers"]
