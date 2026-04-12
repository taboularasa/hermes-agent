import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from tools import codex_delegate_tool
from tools import linear_issue_tool
from tools import self_improvement_tool


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_yaml(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_ontology_repo(tmp_path: Path, *, generated_at: str) -> Path:
    repo = tmp_path / "ontology"
    _write_json(
        repo / "evolution" / "metrics.json",
        {
            "generated_at": generated_at,
            "verticals": {
                "home_services": {
                    "cq_total": 20,
                    "cq_answered": 20,
                    "cq_open": 0,
                    "cq_coverage": 1.0,
                    "foundation_reuse_ratio": 0.8,
                    "proposals_generated": 0,
                    "cqs_added": 2,
                }
            },
            "platform": {
                "total_cqs": 20,
                "total_answered": 20,
                "total_cqs_added": 2,
                "total_proposals_generated": 0,
            },
        },
    )
    _write_json(
        repo / "evolution" / "delta_report.json",
        {
            "generated_at": generated_at,
            "business_recommendations": ["Prioritize home_services packaging."],
            "current": {"verticals": {"home_services": {"foundation_reuse_ratio": 0.8}}},
        },
    )
    (repo / "evolution").mkdir(parents=True, exist_ok=True)
    (repo / "evolution" / "daily_report.md").write_text(
        f"# Ontology Evolution Daily Report\n\nGenerated at: `{generated_at}`\n",
        encoding="utf-8",
    )
    _write_json(
        repo / "evolution" / "logs" / "home_services_cycle.json",
        {"timestamp": generated_at, "vertical": "home_services", "proposals_generated": 0},
    )
    _write_yaml(
        repo / "research" / "manifests" / "home_services.yaml",
        {"manifest_id": "home-services", "prepared_at": generated_at, "sources": [{"title": "Site"}]},
    )
    _write_yaml(
        repo / "research" / "prompt_proposals" / "home_services" / "cycle-001.yaml",
        {"generated_at": generated_at, "vertical": "home_services"},
    )
    _write_yaml(
        repo / "orsd" / "home_services.yaml",
        {
            "vertical": "home_services",
            "last_evolved": generated_at,
            "purpose": "Dispatch and permit workflows.",
            "scope": "Field services.",
            "system_problems": [{"title": "Manual dispatch"}],
            "ontology_use_cases": [{"title": "Permit handling"}],
            "competency_questions": [{"group": "service_call", "source": "research_discovery"}],
        },
    )
    return repo


def _seed_reward_policy(path: Path) -> Path:
    _write_yaml(
        path,
        {
            "epoch": {
                "name": "Client Revenue and Social Proof",
                "review_question": "Does this help Hadto win or retain business?",
            },
            "guardrails": {
                "max_active_issues_per_lane": 1,
                "capability_budget_percent": 20,
            },
            "lanes": {
                "capability": {"default_budget_percent": 20},
            },
        },
    )
    return path


def _linear_issue(
    identifier: str,
    *,
    state_type: str,
    state_name: str,
    lane: str,
    delegate_name: str = "Hermes",
    assignee: dict | None = None,
    include_verification: bool = True,
    include_status_comment: bool = False,
    updated_at: str | None = None,
    completed_at: str | None = None,
) -> dict:
    description = [
        f"Lane: {lane}",
        "",
        "Capability gap:",
        "Durable self-improvement work item.",
        "",
        "Why now:",
        "Needed to improve Hermes execution quality.",
    ]
    if include_verification:
        description.extend(
            [
                "",
                "Verification expectation:",
                "Run the focused test suite and verify the output.",
            ]
        )
    issue = {
        "id": f"issue-{identifier.lower()}",
        "identifier": identifier,
        "title": f"{identifier} title",
        "description": "\n".join(description),
        "state": {"id": f"state-{state_type}", "name": state_name, "type": state_type},
        "delegate": {"id": "user-hermes", "name": delegate_name, "displayName": delegate_name, "email": "hermes@hadto.net"},
        "assignee": assignee,
        "comments": [],
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "completedAt": completed_at,
    }
    if include_status_comment:
        issue["comments"] = [
            {
                "id": f"comment-{identifier.lower()}",
                "body": linear_issue_tool._format_comment_body(
                    "In progress",
                    f"status:{identifier}",
                ),
            }
        ]
    return issue


def _ctx_state_comment(
    *,
    ctx_task_id: str,
    phase: str = "running",
    latest_turn_status: str = "queued",
    awaiting_new_assistant: bool = True,
    created_at: str | None = None,
) -> dict:
    payload = {
        "ctx_task_id": ctx_task_id,
        "phase": phase,
        "latest_turn_status": latest_turn_status,
        "awaiting_new_assistant": awaiting_new_assistant,
    }
    body = f"<!-- hermes-ctx-state:v1 {json.dumps(payload)} -->\n\n## Hermes ctx status"
    return {
        "id": f"ctx-{ctx_task_id}",
        "body": body,
        "createdAt": created_at,
        "updatedAt": created_at,
    }


def _clone(payload):
    return json.loads(json.dumps(payload))


def _install_fake_linear_surface(monkeypatch, store: dict) -> None:
    states = store["states"]

    def _state(name: str) -> dict:
        for entry in states:
            if entry["name"] == name:
                return _clone(entry)
        raise AssertionError(f"unknown state: {name}")

    def _find_issue(issue_ref: str) -> dict | None:
        wanted = str(issue_ref)
        for issue in store["issues"]:
            if issue.get("id") == wanted or issue.get("identifier") == wanted:
                return issue
        return None

    def _sync_state(issue: dict, state_name: str) -> None:
        issue["state"] = _state(state_name)
        if issue["state"]["type"] == "completed":
            issue["completedAt"] = issue.get("completedAt") or issue.get("updatedAt")
        else:
            issue["completedAt"] = None

    def fake_linear_issue(args: dict[str, object], **_kw) -> str:
        action = str(args.get("action") or "")
        if action == "project_upsert":
            created = store["project"] is None
            if created:
                store["project"] = {
                    "id": "project-1",
                    "name": str(args.get("name") or "Hermes Self-Improvement"),
                    "description": linear_issue_tool._format_project_description(
                        str(args.get("description") or ""),
                        str(args.get("dedupe_key") or ""),
                    ),
                    "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
                    "url": "https://linear.app/hadto/project/hermes-self-improvement",
                }
            else:
                store["project"]["name"] = str(args.get("name") or store["project"]["name"])
                store["project"]["description"] = linear_issue_tool._format_project_description(
                    str(args.get("description") or ""),
                    str(args.get("dedupe_key") or ""),
                )
            return json.dumps(
                {
                    "success": True,
                    "created": created,
                    "updated_existing": not created,
                    "project": _clone(store["project"]),
                }
            )

        if action == "issue_upsert":
            dedupe_key = str(args.get("dedupe_key") or "")
            issue_ref = str(args.get("issue_id") or args.get("identifier") or "")
            issue = _find_issue(issue_ref) if issue_ref else None
            if issue is None and dedupe_key:
                for candidate in store["issues"]:
                    marker = linear_issue_tool._parse_marker(str(candidate.get("description") or ""))
                    if str((marker or {}).get("dedupe_key") or "") == dedupe_key:
                        issue = candidate
                        break
            if issue is None:
                store["next_issue"] += 1
                identifier = f"HAD-{store['next_issue']}"
                issue = {
                    "id": f"issue-{identifier.lower()}",
                    "identifier": identifier,
                    "title": str(args.get("title") or ""),
                    "description": linear_issue_tool._format_marker_body(
                        str(args.get("description") or ""),
                        dedupe_key or None,
                    ),
                    "priority": args.get("priority"),
                    "project": _clone(store["project"]),
                    "team": {"id": "team-1", "key": "HAD", "name": "Hadto"},
                    "state": _state(str(args.get("state_name") or "Backlog")),
                    "delegate": _clone(store["users"]["hermes"]),
                    "assignee": None,
                    "labels": [],
                    "comments": [],
                    "createdAt": store["timestamp"],
                    "updatedAt": store["timestamp"],
                    "completedAt": None,
                    "url": f"https://linear.app/hadto/issue/{identifier.lower()}",
                }
                store["issues"].append(issue)
                created = True
            else:
                created = False
                issue["title"] = str(args.get("title") or issue["title"])
                issue["description"] = linear_issue_tool._format_marker_body(
                    str(args.get("description") or linear_issue_tool._strip_marker(str(issue.get("description") or ""))),
                    dedupe_key or None,
                )
                issue["updatedAt"] = store["timestamp"]

            if "priority" in args and args.get("priority") is not None:
                issue["priority"] = int(args["priority"])
            if "delegate_id" in args:
                issue["delegate"] = (
                    _clone(store["users"]["hermes"])
                    if str(args.get("delegate_id") or "")
                    else None
                )
            if "assignee_id" in args:
                issue["assignee"] = (
                    _clone(store["users"]["human"])
                    if str(args.get("assignee_id") or "")
                    else None
                )
            if args.get("state_name"):
                _sync_state(issue, str(args["state_name"]))

            return json.dumps(
                {
                    "success": True,
                    "created": created,
                    "updated_existing": not created,
                    "issue": _clone(issue),
                }
            )

        if action == "update_state":
            issue = _find_issue(str(args.get("identifier") or args.get("issue_id") or ""))
            if issue is None:
                return json.dumps({"error": "missing issue"})
            _sync_state(issue, str(args.get("state_name") or "Done"))
            issue["updatedAt"] = store["timestamp"]
            return json.dumps({"success": True, "issue": _clone(issue), "team_states": _clone(states)})

        if action == "comment":
            issue = _find_issue(str(args.get("identifier") or args.get("issue_id") or ""))
            if issue is None:
                return json.dumps({"error": "missing issue"})
            dedupe_key = str(args.get("dedupe_key") or "")
            body = linear_issue_tool._format_comment_body(str(args.get("body") or ""), dedupe_key or None)
            existing = None
            if dedupe_key:
                for comment in issue["comments"]:
                    marker = linear_issue_tool._parse_marker(str(comment.get("body") or ""))
                    if str((marker or {}).get("dedupe_key") or "") == dedupe_key:
                        existing = comment
                        break
            if existing:
                existing["body"] = body
                existing["updatedAt"] = store["timestamp"]
                comment = existing
                updated_existing = True
            else:
                comment = {
                    "id": f"comment-{issue['identifier'].lower()}-{len(issue['comments']) + 1}",
                    "body": body,
                    "createdAt": store["timestamp"],
                    "updatedAt": store["timestamp"],
                    "url": f"{issue['url']}#comment-{len(issue['comments']) + 1}",
                }
                issue["comments"].insert(0, comment)
                updated_existing = False
            issue["updatedAt"] = store["timestamp"]
            return json.dumps(
                {
                    "success": True,
                    "updated_existing": updated_existing,
                    "created": not updated_existing,
                    "issue_id": issue["id"],
                    "issue_identifier": issue["identifier"],
                    "comment": _clone(comment),
                }
            )

        raise AssertionError(f"unexpected action: {action}")

    monkeypatch.setattr(linear_issue_tool, "check_linear_issue_requirements", lambda: True)
    monkeypatch.setattr(linear_issue_tool, "_list_users", lambda: [_clone(store["users"]["hermes"]), _clone(store["users"]["human"])])
    monkeypatch.setattr(linear_issue_tool, "_list_projects", lambda limit=100: [_clone(store["project"])] if store["project"] else [])
    monkeypatch.setattr(linear_issue_tool, "_resolve_team_id", lambda team_id, team_key: ("team-1", "HAD"))
    monkeypatch.setattr(linear_issue_tool, "_fetch_workflow_states", lambda team_key: _clone(states))
    monkeypatch.setattr(
        linear_issue_tool,
        "_project_issues",
        lambda project_id, limit=250: [_clone(issue) for issue in store["issues"]],
    )
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_issue",
        lambda issue_ref, comment_limit=10: _clone(_find_issue(issue_ref) or {}),
    )
    monkeypatch.setattr(
        linear_issue_tool,
        "_update_issue",
        lambda issue_id, input_data: (
            _find_issue(issue_id).update(
                {
                    "assignee": None if "assigneeId" in input_data else _find_issue(issue_id).get("assignee"),
                    "updatedAt": store["timestamp"],
                }
            )
            or _clone(_find_issue(issue_id))
        ),
    )
    monkeypatch.setattr(linear_issue_tool, "linear_issue", fake_linear_issue)


def test_evaluate_self_improvement_evidence_reports_healthy_sources(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {
                    "session_id": "sess_1",
                    "active": True,
                    "updated_at": recent,
                    "worktree_path": str(worktree),
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "healthy"
    assert gate["reasons"] == []
    assert gate["suppression"]["suppress_non_maintenance"] is False


def test_evaluate_self_improvement_evidence_keeps_gate_healthy_after_retiring_stale_ctx_binding(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    stale_active = (now - timedelta(hours=18)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(
        ctx_path,
        {
            "sessions": {
                "ctx_stale": {
                    "session_id": "ctx_stale",
                    "task_id": "task-1",
                    "active": True,
                    "updated_at": stale_active,
                    "worktree_path": str(worktree),
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )
    persisted_ctx = json.loads(ctx_path.read_text(encoding="utf-8"))

    assert gate["status"] == "healthy"
    assert gate["reasons"] == []
    assert gate["stale_active_ctx"] == []
    assert persisted_ctx["sessions"]["ctx_stale"]["active"] is False
    assert persisted_ctx["sessions"]["ctx_stale"]["updated_at"] == recent


def test_evaluate_self_improvement_evidence_downgrades_freshness_skew_to_warning(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    older_but_fresh = (now - timedelta(hours=20)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=older_but_fresh)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": older_but_fresh}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "ctx_1": {
                    "session_id": "ctx_1",
                    "task_id": "task-1",
                    "active": False,
                    "updated_at": recent,
                    "worktree_path": str(worktree),
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "warning"
    assert gate["reasons"] == []
    assert gate["contradictions"] == []
    assert gate["warnings"] == ["evidence freshness skew across sources"]
    assert gate["freshness_spread_hours"] == 20.0
    assert gate["suppression"]["suppress_non_maintenance"] is False


def test_evaluate_self_improvement_evidence_retires_stale_ctx_bindings_before_scoring(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    stale = (now - timedelta(hours=96)).isoformat()
    stale_active = (now - timedelta(hours=18)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=stale)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_stale": {
                    "run_id": "codex_stale",
                    "status": "running",
                    "started_at": stale_active,
                    "updated_at": stale,
                }
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "ctx_stale": {
                    "session_id": "ctx_stale",
                    "task_id": "task-1",
                    "active": True,
                    "updated_at": stale_active,
                    "worktree_path": str(worktree),
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )
    persisted_codex = json.loads(codex_path.read_text(encoding="utf-8"))
    persisted_ctx = json.loads(ctx_path.read_text(encoding="utf-8"))

    assert gate["status"] == "degraded"
    assert "evidence freshness mismatch across sources" in gate["contradictions"]
    assert gate["stale_active_codex"] == []
    assert persisted_codex["runs"]["codex_stale"]["status"] == "failed"
    assert persisted_codex["runs"]["codex_stale"]["stale_reason"] == "process_missing"
    assert gate["stale_active_ctx"] == []
    assert persisted_ctx["sessions"]["ctx_stale"]["active"] is False
    assert persisted_ctx["sessions"]["ctx_stale"]["reason"] == "ctx binding retired: stale active binding (>12h)"
    assert gate["suppression"]["suppress_non_maintenance"] is True


def test_evaluate_self_improvement_evidence_detects_planning_contradictions(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_running_done": {
                    "run_id": "codex_running_done",
                    "status": "running",
                    "started_at": recent,
                    "completed_at": recent,
                },
                "codex_ctx_missing": {
                    "run_id": "codex_ctx_missing",
                    "status": "running",
                    "started_at": recent,
                    "ctx_task_id": "task-missing",
                    "ctx_worktree_path": str(tmp_path / "missing-worktree"),
                },
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "ctx_active_missing": {
                    "session_id": "ctx_active_missing",
                    "task_id": "task-1",
                    "active": True,
                    "updated_at": recent,
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "degraded"
    assert "planning contradictions detected" in gate["contradictions"]
    assert gate["planning_contradictions"]


def test_evaluate_self_improvement_evidence_normalizes_dead_codex_run_before_scoring(
    monkeypatch, tmp_path
):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    last_message_path = tmp_path / "codex-final.txt"
    last_message_path.write_text("Implemented the fix.", encoding="utf-8")

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_dead": {
                    "run_id": "codex_dead",
                    "status": "running",
                    "pid": 8181,
                    "started_at": recent,
                    "workdir": str(tmp_path),
                    "last_message_path": str(last_message_path),
                    "record_path": str(tmp_path / "codex_dead.json"),
                    "latest_path": str(tmp_path / "latest.json"),
                }
            }
        },
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_inactive": {"session_id": "ctx_inactive", "active": False, "updated_at": recent}}},
    )

    def fake_kill(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(codex_delegate_tool.os, "kill", fake_kill)

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )
    persisted_codex = json.loads(codex_path.read_text(encoding="utf-8"))

    assert gate["status"] == "healthy"
    assert gate["planning_contradictions"] == []
    assert persisted_codex["runs"]["codex_dead"]["status"] == "completed"
    assert persisted_codex["runs"]["codex_dead"]["stale_reason"] == "process_missing"


def test_evidence_provenance_contract_includes_source_tags(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent}}})

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    provenance = gate["provenance"]
    assert provenance["contract_version"] == "v1"
    tags = [item["tag"] for item in provenance["items"]]
    assert tags == ["journal", "codex", "ctx", "ontology"]
    for item in provenance["items"]:
        assert "path" in item
        assert "status" in item
        assert "latest_timestamp" in item
        assert "age_hours" in item

    summary = provenance["summary_markdown"]
    assert "[journal]" in summary
    assert "[codex]" in summary
    assert "[ctx]" in summary
    assert "[ontology]" in summary
    assert f"path={journal_path}" in summary


def test_evaluate_self_improvement_evidence_flags_stale_ontology_artifacts(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=96)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=stale)

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent}}})

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "degraded"
    assert "ontology intelligence artifacts are stale or missing" in gate["contradictions"]
    assert "ontology_metrics stale" in " ".join(gate["reasons"])


def test_self_improvement_benchmark_scores_positive_and_persists_history(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    older = (now - timedelta(days=1)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree_a = tmp_path / "worktree-a"
    worktree_b = tmp_path / "worktree-b"
    worktree_a.mkdir()
    worktree_b.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}, {"occurredAt": older}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree_a)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree_b)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Prioritize contract-facing reliability work."],
        },
    )
    monkeypatch.setattr(
        self_improvement_tool,
        "_load_linear_benchmark_surface",
        lambda **kwargs: {
            "available": True,
            "project": {"id": "project-1", "name": "Hermes Self-Improvement"},
            "issues": [
                _linear_issue(
                    "HAD-201",
                    state_type="started",
                    state_name="In Progress",
                    lane="Maintenance",
                    include_status_comment=True,
                    updated_at=recent,
                ),
                _linear_issue(
                    "HAD-202",
                    state_type="completed",
                    state_name="Done",
                    lane="Growth",
                    updated_at=recent,
                    completed_at=recent,
                ),
            ],
            "error": None,
        },
    )

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=True,
    )

    assert benchmark["direction"] == "positive"
    assert benchmark["trend"] == "baseline"
    assert benchmark["positive_direction"] is True
    assert benchmark["critical_failures"] == []
    assert benchmark["history"]["evaluation_count_after_run"] == 1
    assert benchmark["score"] >= 90.0

    persisted = json.loads(history_path.read_text(encoding="utf-8"))
    assert persisted["evaluations"][0]["score"] == benchmark["score"]
    assert "delegate_assignment_hygiene" in persisted["evaluations"][0]["checks"]


def test_self_improvement_benchmark_treats_freshness_skew_as_warning(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    older_but_fresh = (now - timedelta(hours=20)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=older_but_fresh)

    _write_json(journal_path, {"entries": [{"occurredAt": older_but_fresh}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Keep backlog flow while evidence cadence catches up."],
        },
    )
    monkeypatch.setattr(
        self_improvement_tool,
        "_load_linear_benchmark_surface",
        lambda **kwargs: {
            "available": True,
            "project": {"id": "project-1", "name": "Hermes Self-Improvement"},
            "issues": [
                _linear_issue(
                    "HAD-205",
                    state_type="started",
                    state_name="In Progress",
                    lane="Growth",
                    include_status_comment=True,
                    updated_at=recent,
                ),
                _linear_issue(
                    "HAD-206",
                    state_type="completed",
                    state_name="Done",
                    lane="Maintenance",
                    updated_at=recent,
                    completed_at=recent,
                ),
            ],
            "error": None,
        },
    )

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
        persist=False,
    )
    items = {item["id"]: item for item in benchmark["benchmarks"]}

    assert benchmark["gate"]["status"] == "warning"
    assert "reliability_gate" not in benchmark["critical_failures"]
    assert items["reliability_gate"]["status"] == "warn"
    assert items["reliability_gate"]["detail"] == "evidence freshness skew across sources"
    assert items["reliability_gate"]["metrics"]["warning_count"] == 1
    assert items["reliability_gate"]["metrics"]["contradiction_count"] == 0


def test_self_improvement_benchmark_detects_regression_and_delegate_conflicts(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Prioritize contract-facing reliability work."],
        },
    )

    clean_surface = {
        "available": True,
        "project": {"id": "project-1", "name": "Hermes Self-Improvement"},
        "issues": [
            _linear_issue(
                "HAD-301",
                state_type="started",
                state_name="In Progress",
                lane="Maintenance",
                include_status_comment=True,
                updated_at=recent,
            ),
            _linear_issue(
                "HAD-302",
                state_type="completed",
                state_name="Done",
                lane="Growth",
                updated_at=recent,
                completed_at=recent,
            ),
        ],
        "error": None,
    }
    dirty_surface = {
        "available": True,
        "project": {"id": "project-1", "name": "Hermes Self-Improvement"},
        "issues": [
            _linear_issue(
                "HAD-303",
                state_type="started",
                state_name="In Progress",
                lane="Capability",
                assignee={"id": "user-human", "name": "david"},
                include_status_comment=False,
                updated_at=recent,
            ),
            _linear_issue(
                "HAD-304",
                state_type="started",
                state_name="In Progress",
                lane="Capability",
                assignee={"id": "user-human", "name": "david"},
                include_status_comment=False,
                updated_at=recent,
            ),
        ],
        "error": None,
    }

    state = {"surface": clean_surface}
    monkeypatch.setattr(
        self_improvement_tool,
        "_load_linear_benchmark_surface",
        lambda **kwargs: state["surface"],
    )

    first = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=True,
    )
    state["surface"] = dirty_surface
    second = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now + timedelta(hours=1),
        persist=True,
    )

    assert first["direction"] == "positive"
    assert second["trend"] == "regressing"
    assert second["direction"] == "negative"
    assert "delegate_assignment_hygiene" in second["critical_failures"]
    assert "reward_policy_alignment" in second["critical_failures"]
    assert second["history"]["delta_vs_previous"] < 0
    assert second["linear_surface"]["delegate_conflict_count"] == 2

    persisted = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(persisted["evaluations"]) == 2


def test_self_improvement_pipeline_repairs_delegate_conflicts_and_upserts_top_issue(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=96)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=stale)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)}}})

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "stale"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Restore ontology freshness before new capability work."],
        },
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [
            _linear_issue(
                "HAD-401",
                state_type="started",
                state_name="In Progress",
                lane="Maintenance",
                assignee={"id": "user-human", "name": "david"},
                include_status_comment=False,
                updated_at=recent,
            ),
        ],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 499,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=False,
    )

    assert pipeline["linear"]["repairs"][0]["identifier"] == "HAD-401"
    assert pipeline["benchmark"]["linear_surface"]["delegate_conflict_count"] == 0
    assert pipeline["top_candidate"]["benchmark_id"] == "reliability_gate"
    assert pipeline["strategic_conversation"]["selected"] is None
    assert pipeline["strategic_conversation"]["suppressed_reason"] == "reliability_gate_degraded"

    reliability_issue = next(
        issue for issue in store["issues"]
        if "issue:hermes-self-improvement:benchmark:reliability_gate" in str(issue.get("description") or "")
    )
    assert reliability_issue["delegate"]["id"] == "user-hermes"
    assert reliability_issue["assignee"] is None
    assert any(
        str((linear_issue_tool._parse_marker(comment["body"]) or {}).get("dedupe_key") or "")
        == f"status:{reliability_issue['identifier']}"
        for comment in reliability_issue["comments"]
    )


def test_self_improvement_pipeline_demotes_started_issue_without_live_execution(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {
                    "session_id": "sess_1",
                    "task_id": "task-1",
                    "active": False,
                    "updated_at": recent,
                    "worktree_path": str(worktree),
                },
                "sess_2": {
                    "session_id": "sess_2",
                    "task_id": "task-2",
                    "active": False,
                    "updated_at": recent,
                    "worktree_path": str(worktree),
                },
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Prioritize contract-facing reliability work."],
        },
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [
            _linear_issue(
                "HAD-510",
                state_type="started",
                state_name="In Progress",
                lane="Maintenance",
                include_status_comment=True,
                updated_at=recent,
            ),
            _linear_issue(
                "HAD-511",
                state_type="started",
                state_name="In Progress",
                lane="Maintenance",
                include_status_comment=True,
                updated_at=recent,
            ),
            _linear_issue(
                "HAD-512",
                state_type="completed",
                state_name="Done",
                lane="Growth",
                updated_at=recent,
                completed_at=recent,
            ),
        ],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-todo", "name": "Todo", "type": "unstarted"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 599,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=False,
    )

    assert pipeline["top_candidate"] is None
    assert pipeline["benchmark"]["linear_surface"]["active_issue_count"] == 0
    assert pipeline["benchmark"]["linear_surface"]["overflow_lanes"] == {}
    demoted = [item for item in pipeline["linear"]["repairs"] if item["action"] == "demoted_without_live_execution"]
    assert len(demoted) == 2
    assert all(
        issue["state"]["type"] in {"backlog", "unstarted"}
        for issue in store["issues"]
        if issue["identifier"] in {"HAD-510", "HAD-511"}
    )


def test_self_improvement_pipeline_preserves_started_issue_with_live_ctx_execution(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {
                    "session_id": "sess_1",
                    "task_id": "task-running",
                    "active": True,
                    "updated_at": recent,
                    "worktree_path": str(worktree),
                }
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Prioritize contract-facing reliability work."],
        },
    )

    live_issue = _linear_issue(
        "HAD-520",
        state_type="started",
        state_name="In Progress",
        lane="Maintenance",
        include_status_comment=True,
        updated_at=recent,
    )
    live_issue["comments"].insert(0, _ctx_state_comment(ctx_task_id="task-running", created_at=recent))

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [live_issue],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-todo", "name": "Todo", "type": "unstarted"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 599,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=False,
    )

    assert pipeline["linear"]["repairs"] == []
    assert pipeline["benchmark"]["linear_surface"]["active_issue_count"] == 1
    assert store["issues"][0]["state"]["type"] == "started"


def test_self_improvement_pipeline_auto_closes_resolved_benchmark_issue(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": [{"title": "Typed execution frame"}]},
            "business_recommendations": ["Prioritize contract-facing reliability work."],
        },
    )

    resolved_issue = _linear_issue(
        "HAD-450",
        state_type="started",
        state_name="In Progress",
        lane="Maintenance",
        include_status_comment=True,
        updated_at=recent,
    )
    resolved_issue["description"] = linear_issue_tool._format_marker_body(
        linear_issue_tool._strip_marker(str(resolved_issue["description"] or "")),
        "issue:hermes-self-improvement:benchmark:delegate_assignment_hygiene",
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [resolved_issue],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 499,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        now=now,
        persist=False,
    )

    assert any(item["benchmark_id"] == "delegate_assignment_hygiene" for item in pipeline["linear"]["closed_issues"])
    assert store["issues"][0]["state"]["type"] == "completed"


def test_self_improvement_pipeline_surfaces_strategic_conversation_when_healthy(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    discussion_history_path = tmp_path / "strategic_conversations.json"
    discussion_notes_dir = tmp_path / "strategic_notes"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": []},
            "candidates": {
                "maintenance": [],
                "growth": [
                    {
                        "lane": "Growth",
                        "title": "Develop a strategic sounding-board loop with David",
                        "why_now": "Hermes keeps getting future-direction ideas through ad hoc user guidance, but the loop lacks a deliberate way to pair before turning that into implementation.",
                    }
                ],
                "capability": [],
            },
            "business_recommendations": [
                "Use strategic Slack discussions as a durable source of ontology notes and future journal synthesis."
            ],
            "evidence": {
                "metrics_path": str(ontology_root / "evolution" / "metrics.json"),
                "delta_report_path": str(ontology_root / "evolution" / "delta_report.json"),
                "daily_report_path": str(ontology_root / "evolution" / "daily_report.md"),
            },
        },
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [
            _linear_issue(
                "HAD-530",
                state_type="completed",
                state_name="Done",
                lane="Growth",
                updated_at=recent,
                completed_at=recent,
            ),
            _linear_issue(
                "HAD-531",
                state_type="completed",
                state_name="Done",
                lane="Maintenance",
                updated_at=recent,
                completed_at=recent,
            ),
        ],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 599,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        discussion_history_path=discussion_history_path,
        discussion_notes_dir=discussion_notes_dir,
        now=now,
        persist=True,
    )

    assert pipeline["top_candidate"] is None
    selected = pipeline["strategic_conversation"]["selected"]
    assert selected is not None
    assert selected["should_reach_out"] is True
    assert selected["slack_target"] == "slack"
    assert "Discussion key:" in selected["slack_message"]
    assert Path(selected["note_path"]).exists()

    history = json.loads(discussion_history_path.read_text(encoding="utf-8"))
    assert history["conversations"][0]["dedupe_key"] == selected["dedupe_key"]
    assert history["conversations"][0]["last_proposed_at"] == now.isoformat()


def test_self_improvement_pipeline_allows_strategic_conversation_when_gate_is_warning(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    older_but_fresh = (now - timedelta(hours=20)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=older_but_fresh)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    discussion_history_path = tmp_path / "strategic_conversations.json"
    discussion_notes_dir = tmp_path / "strategic_notes"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": older_but_fresh}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": []},
            "candidates": {
                "maintenance": [],
                "growth": [
                    {
                        "lane": "Growth",
                        "title": "Develop a strategic sounding-board loop with David",
                        "why_now": "Hermes should still surface future-direction discussions even when evidence cadence is uneven but execution is healthy.",
                    }
                ],
                "capability": [],
            },
            "business_recommendations": [
                "Keep strategic discussion flow available when the reliability gate is only warning-level."
            ],
            "evidence": {
                "metrics_path": str(ontology_root / "evolution" / "metrics.json"),
                "delta_report_path": str(ontology_root / "evolution" / "delta_report.json"),
                "daily_report_path": str(ontology_root / "evolution" / "daily_report.md"),
            },
        },
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [
            _linear_issue(
                "HAD-532",
                state_type="completed",
                state_name="Done",
                lane="Growth",
                updated_at=recent,
                completed_at=recent,
            ),
            _linear_issue(
                "HAD-533",
                state_type="completed",
                state_name="Done",
                lane="Maintenance",
                updated_at=recent,
                completed_at=recent,
            ),
        ],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 609,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    pipeline = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        discussion_history_path=discussion_history_path,
        discussion_notes_dir=discussion_notes_dir,
        now=now,
        persist=True,
    )

    assert pipeline["benchmark"]["gate"]["status"] == "warning"
    assert pipeline["top_candidate"]["benchmark_id"] == "reliability_gate"
    assert pipeline["top_candidate"]["status"] == "warn"
    selected = pipeline["strategic_conversation"]["selected"]
    assert selected is not None
    assert selected["should_reach_out"] is True
    assert selected["slack_target"] == "slack"
    assert Path(selected["note_path"]).exists()


def test_self_improvement_pipeline_respects_strategic_conversation_cooldown(monkeypatch, tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    objective_path = _seed_reward_policy(tmp_path / "epoch.yaml")
    history_path = tmp_path / "benchmarks.json"
    discussion_history_path = tmp_path / "strategic_conversations.json"
    discussion_notes_dir = tmp_path / "strategic_notes"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent},
                "codex_2": {"run_id": "codex_2", "status": "completed", "completed_at": recent},
            }
        },
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "sess_1": {"session_id": "sess_1", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
                "sess_2": {"session_id": "sess_2", "active": False, "updated_at": recent, "worktree_path": str(worktree)},
            }
        },
    )

    monkeypatch.setattr(
        self_improvement_tool,
        "build_self_improvement_context",
        lambda *args, **kwargs: {
            "reliability": {"status": "fresh"},
            "research_provider_policy": {"summary": {"available_provider_count": 2}},
            "textbook_study": {"upgrade_targets": []},
            "candidates": {
                "maintenance": [],
                "growth": [
                    {
                        "lane": "Growth",
                        "title": "Develop a strategic sounding-board loop with David",
                        "why_now": "Hermes needs a deliberate path for future-direction conversations before it turns them into implementation.",
                    }
                ],
                "capability": [],
            },
            "business_recommendations": [],
            "evidence": {
                "metrics_path": str(ontology_root / "evolution" / "metrics.json"),
                "delta_report_path": str(ontology_root / "evolution" / "delta_report.json"),
                "daily_report_path": str(ontology_root / "evolution" / "daily_report.md"),
            },
        },
    )

    store = {
        "project": {
            "id": "project-1",
            "name": "Hermes Self-Improvement",
            "description": linear_issue_tool._format_project_description(
                "Track capability gaps and implementation follow-through.",
                "project:hermes-self-improvement",
            ),
            "teams": [{"id": "team-1", "key": "HAD", "name": "Hadto"}],
            "url": "https://linear.app/hadto/project/hermes-self-improvement",
        },
        "issues": [
            _linear_issue(
                "HAD-540",
                state_type="completed",
                state_name="Done",
                lane="Growth",
                updated_at=recent,
                completed_at=recent,
            ),
            _linear_issue(
                "HAD-541",
                state_type="completed",
                state_name="Done",
                lane="Maintenance",
                updated_at=recent,
                completed_at=recent,
            ),
        ],
        "states": [
            {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
            {"id": "state-progress", "name": "In Progress", "type": "started"},
            {"id": "state-done", "name": "Done", "type": "completed"},
        ],
        "users": {
            "hermes": {"id": "user-hermes", "name": "Hermes", "displayName": "Hermes", "email": "hermes@hadto.net", "active": True},
            "human": {"id": "user-human", "name": "David", "displayName": "David", "email": "david@hadto.net", "active": True},
        },
        "next_issue": 699,
        "timestamp": recent,
    }
    _install_fake_linear_surface(monkeypatch, store)

    first = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        discussion_history_path=discussion_history_path,
        discussion_notes_dir=discussion_notes_dir,
        discussion_cooldown_hours=72,
        now=now,
        persist=True,
    )
    second = self_improvement_tool.evaluate_self_improvement_pipeline(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        objective_path=objective_path,
        history_path=history_path,
        discussion_history_path=discussion_history_path,
        discussion_notes_dir=discussion_notes_dir,
        discussion_cooldown_hours=72,
        now=now + timedelta(hours=1),
        persist=True,
    )

    first_selected = first["strategic_conversation"]["selected"]
    second_selected = second["strategic_conversation"]["selected"]
    assert first_selected is not None
    assert second_selected is not None
    assert first_selected["should_reach_out"] is True
    assert second_selected["should_reach_out"] is False
    assert second_selected["suppressed_reason"] == "cooldown_active"

    history = json.loads(discussion_history_path.read_text(encoding="utf-8"))
    assert history["conversations"][0]["last_proposed_at"] == now.isoformat()
    assert history["conversations"][0]["last_seen_at"] == (now + timedelta(hours=1)).isoformat()
