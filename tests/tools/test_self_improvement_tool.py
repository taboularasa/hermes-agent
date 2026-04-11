import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import self_improvement_tool


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_evaluate_self_improvement_evidence_reports_healthy_sources(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"

    _write_json(journal_path, {"entries": [{"occurredAt": recent}]})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "status": "completed", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"sess_1": {"session_id": "sess_1", "active": True, "updated_at": recent}}})

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
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
                }
            }
        },
    )

    gate = self_improvement_tool.evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        now=now,
    )

    assert gate["status"] == "degraded"
    assert "evidence freshness mismatch across sources" in gate["contradictions"]
    assert gate["stale_active_codex"][0]["run_id"] == "codex_stale"
    assert gate["stale_active_ctx"][0]["session_id"] == "ctx_stale"
    assert gate["suppression"]["suppress_non_maintenance"] is True
