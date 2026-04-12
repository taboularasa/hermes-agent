import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

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


def test_evaluate_self_improvement_evidence_flags_freshness_mismatch_and_stale_activity(tmp_path):
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

    assert gate["status"] == "degraded"
    assert "evidence freshness mismatch across sources" in gate["contradictions"]
    assert gate["stale_active_codex"][0]["run_id"] == "codex_stale"
    assert gate["stale_active_ctx"][0]["session_id"] == "ctx_stale"
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
