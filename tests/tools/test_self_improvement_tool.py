import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import self_improvement_tool


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_ontology_repo(tmp_path: Path, *, generated_at: str, status: str = "fresh") -> Path:
    repo = tmp_path / "ontology"
    _write_json(
        repo / "evolution" / "metrics.json",
        {
            "generated_at": generated_at,
            "status": status,
            "platform": {"total_cqs": 10, "total_answered": 10},
        },
    )
    return repo


def test_all_fresh_cross_source_skew_keeps_reliability_gate_healthy(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    older_but_fresh = (now - timedelta(hours=20)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(journal_path, {"entries": [{"occurredAt": older_but_fresh}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "healthy"
    assert {entry["status"] for entry in gate["sources"].values()} == {"fresh"}
    assert gate["warnings"] == []
    assert gate["contradictions"] == []
    assert gate["freshness_spread_hours"] == 20.0

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        history_path=tmp_path / "history.json",
        now=now,
        persist=False,
    )
    reliability_gate = benchmark["checks"]["reliability_gate"]
    assert reliability_gate["score"] == 1.0
    assert reliability_gate["status"] == "pass"
    assert reliability_gate["detail"] == "Reliability floor is healthy."
    assert reliability_gate["metrics"]["warning_count"] == 0
    assert benchmark["critical_failures"] == []


def test_ontology_scan_ignores_git_worktree_runtime_artifacts(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )
    _write_json(
        ontology_root / ".git" / "worktrees" / "old" / "hermes-codex" / "latest.json",
        {"generated_at": recent, "status": "failed"},
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "healthy"
    assert gate["ontology"]["status"] == "fresh"
    assert gate["ontology_alerts"] == []
    assert gate["warnings"] == []
    assert gate["contradictions"] == []


def test_stale_active_ctx_still_degrades_reliability_floor(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()
    stale_active = (now - timedelta(hours=18)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {
            "sessions": {
                "ctx_stale": {
                    "session_id": "ctx_stale",
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
    assert "stale active ctx bindings detected" in gate["warnings"]
    assert "stale active ctx bindings detected" in gate["contradictions"]
    assert len(gate["stale_active_ctx"]) == 1

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        history_path=tmp_path / "history.json",
        now=now,
        persist=False,
    )
    reliability_gate = benchmark["checks"]["reliability_gate"]
    assert reliability_gate["status"] == "fail"
    assert reliability_gate["metrics"]["warning_count"] >= 1
    assert "reliability_gate" in benchmark["critical_failures"]


def test_degraded_ontology_status_still_degrades_reliability_floor(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent, status="degraded")

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(
        codex_path,
        {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}},
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        now=now,
    )

    assert gate["status"] == "degraded"
    assert "ontology_intelligence evidence degraded" in gate["warnings"]
    assert "ontology intelligence artifacts are stale, missing, or degraded" in gate["contradictions"]
