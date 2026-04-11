import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

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
