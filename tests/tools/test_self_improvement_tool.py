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
    anti_make_work = benchmark["checks"]["anti_make_work_check"]
    assert anti_make_work["score"] == 1.0
    assert anti_make_work["status"] == "pass"
    assert anti_make_work["metrics"]["assessed_work_item_count"] == 0
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


def test_status_language_only_work_fails_anti_make_work_check(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(
        journal_path,
        {
            "entries": [
                {
                    "id": "status-only",
                    "occurredAt": recent,
                    "summary": "Status update: HAD-1019 is in progress and actionable.",
                    "notes": "Working on the active item; next step is to keep monitoring.",
                    "linearIssues": ["HAD-1019"],
                    "commitShas": [],
                    "reposTouched": [],
                }
            ]
        },
    )
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_status": {
                    "run_id": "codex_status",
                    "status": "completed",
                    "completed_at": recent,
                    "final_message": "STATUS\nIn progress. Summary captured; no blockers.",
                    "exit_code": 0,
                }
            }
        },
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )

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
    assert reliability_gate["status"] == "pass"

    anti_make_work = benchmark["checks"]["anti_make_work_check"]
    assert anti_make_work["status"] == "fail"
    assert anti_make_work["score"] == 0.0
    assert anti_make_work["metrics"]["assessed_work_item_count"] == 2
    assert anti_make_work["metrics"]["status_language_only_count"] == 2
    assert "anti_make_work_check" in benchmark["critical_failures"]
    assert benchmark["project_score"] < 100.0


def test_durable_work_evidence_passes_anti_make_work_check(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(
        journal_path,
        {
            "entries": [
                {
                    "id": "durable",
                    "occurredAt": recent,
                    "summary": "Implemented HAD-1019 benchmark hardening.",
                    "notes": (
                        "PR #1019 opened with commit abc1234; "
                        "pytest tests/tools/test_self_improvement_tool.py passed."
                    ),
                    "linearIssues": ["HAD-1019"],
                    "commitShas": ["abc1234"],
                    "operatorDecisionSupport": "Operator can compare the PR and test evidence before selecting the next issue.",
                    "pullRequests": ["https://github.com/taboularasa/hermes-agent/pull/1019"],
                    "tests": ["pytest tests/tools/test_self_improvement_tool.py passed"],
                    "reposTouched": ["hermes-agent"],
                }
            ]
        },
    )
    _write_json(
        codex_path,
        {
            "runs": {
                "codex_durable": {
                    "run_id": "codex_durable",
                    "status": "completed",
                    "completed_at": recent,
                    "final_message": (
                        "CHANGED_FILES\n"
                        "- tools/self_improvement_tool.py\n"
                        "VERIFICATION\n"
                        "- pytest tests/tools/test_self_improvement_tool.py -q passed\n"
                        "COMMIT\n"
                        "- abc1234\n"
                        "PULL_REQUEST\n"
                        "- https://github.com/taboularasa/hermes-agent/pull/1019\n"
                        "OPERATOR_DECISION_SUPPORT\n"
                        "- Operator can choose the next issue from verified PR/test evidence."
                    ),
                    "exit_code": 0,
                }
            }
        },
    )
    _write_json(
        ctx_path,
        {"sessions": {"ctx_1": {"session_id": "ctx_1", "active": False, "updated_at": recent}}},
    )

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        history_path=tmp_path / "history.json",
        now=now,
        persist=False,
    )

    anti_make_work = benchmark["checks"]["anti_make_work_check"]
    assert anti_make_work["status"] == "pass"
    assert anti_make_work["score"] == 1.0
    assert anti_make_work["metrics"]["assessed_work_item_count"] == 2
    assert anti_make_work["metrics"]["durable_evidence_count"] == 2
    assert anti_make_work["metrics"]["shallow_work_item_count"] == 0
    assert benchmark["critical_failures"] == []


def test_raw_throughput_does_not_pass_operator_value_alignment(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    _write_json(
        journal_path,
        {
            "entries": [
                {
                    "id": "volume-1",
                    "occurredAt": recent,
                    "summary": "Implemented HAD-1020 benchmark update.",
                    "changedFiles": ["tools/self_improvement_tool.py"],
                    "tests": ["pytest tests/tools/test_self_improvement_tool.py passed"],
                },
                {
                    "id": "volume-2",
                    "occurredAt": recent,
                    "summary": "Opened PR #1020 with commit abc1234.",
                    "pullRequests": ["https://github.com/taboularasa/hermes-agent/pull/1020"],
                    "commitShas": ["abc1234"],
                },
            ]
        },
    )
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"ctx_1": {"session_id": "ctx_1", "updated_at": recent}}})

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        history_path=tmp_path / "history.json",
        now=now,
        persist=False,
    )

    anti_make_work = benchmark["checks"]["anti_make_work_check"]
    assert anti_make_work["status"] == "pass"
    assert anti_make_work["metrics"]["durable_evidence_count"] == 2

    operator_value = benchmark["checks"]["operator_value_alignment"]
    assert operator_value["status"] == "fail"
    assert operator_value["score"] == 0.45
    assert operator_value["metrics"]["verified_system_change_count"] == 2
    assert operator_value["metrics"]["operator_decision_support_count"] == 0
    assert "operator_value_alignment" in benchmark["critical_failures"]
    assert benchmark["issue_selection"]["quantity_guardrail_active"] is True
    assert "decision support" in benchmark["summary"]["operator_value_alignment"]


def test_leading_indicator_drift_fails_when_operator_value_regresses(tmp_path):
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    recent = now.isoformat()

    journal_path = tmp_path / "journal.json"
    codex_path = tmp_path / "runs.json"
    ctx_path = tmp_path / "session_bindings.json"
    history_path = tmp_path / "history.json"
    ontology_root = _seed_ontology_repo(tmp_path, generated_at=recent)

    entries = []
    for idx in range(4):
        entries.append(
            {
                "id": f"aligned-{idx}",
                "occurredAt": recent,
                "summary": f"Implemented operator-value path {idx}.",
                "operatorDecisionSupport": "Operator can compare the verified change and choose the next issue.",
                "changedFiles": ["tools/self_improvement_tool.py"],
                "tests": ["pytest tests/tools/test_self_improvement_tool.py passed"],
            }
        )
    for idx in range(2):
        entries.append(
            {
                "id": f"decision-only-{idx}",
                "occurredAt": recent,
                "summary": f"Prepared operator decision support note {idx}.",
                "operatorDecisionSupport": "Operator has a blocker and recommended next decision.",
            }
        )

    _write_json(journal_path, {"entries": entries})
    _write_json(codex_path, {"runs": {"codex_1": {"run_id": "codex_1", "completed_at": recent}}})
    _write_json(ctx_path, {"sessions": {"ctx_1": {"session_id": "ctx_1", "updated_at": recent}}})
    _write_json(
        history_path,
        {
            "version": 1,
            "evaluations": [
                {
                    "evaluated_at": (now - timedelta(hours=6)).isoformat(),
                    "score": 94.0,
                    "direction": "positive",
                    "checks": {"operator_value_alignment": 0.95},
                }
            ],
        },
    )

    benchmark = self_improvement_tool.evaluate_self_improvement_benchmark(
        journal_path=journal_path,
        codex_runs_path=codex_path,
        ctx_bindings_path=ctx_path,
        ontology_root=ontology_root,
        history_path=history_path,
        now=now,
        persist=False,
    )

    operator_value = benchmark["checks"]["operator_value_alignment"]
    assert operator_value["status"] == "pass"
    assert operator_value["score"] == 0.8833

    drift = benchmark["checks"]["leading_indicator_drift"]
    assert drift["status"] == "fail"
    assert drift["metrics"]["previous_operator_value_score"] == 0.95
    assert drift["metrics"]["operator_value_delta"] == -0.0667
    assert "leading_indicator_drift" in benchmark["critical_failures"]
    assert benchmark["direction"] == "negative"
    assert benchmark["trend"] == "regressing"


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
