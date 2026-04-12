"""Evidence freshness/consistency gate for the Hermes self-improvement loop."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from agent.ontology_context import (
    DEFAULT_ONTOLOGY_REPO_ROOT,
    build_self_improvement_context,
    load_ontology_snapshot,
    summarize_ontology_reliability,
)
from hermes_constants import display_hermes_home, get_hermes_home
from tools.registry import registry
from utils import atomic_json_write

import yaml

logger = logging.getLogger(__name__)


DEFAULT_JOURNAL_PATH = Path("/home/david/stacks/hermes-journal/src/data/journal.json")
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"
DEFAULT_CTX_BINDINGS_PATH = get_hermes_home() / "ctx" / "session_bindings.json"
DEFAULT_ONTOLOGY_ROOT = DEFAULT_ONTOLOGY_REPO_ROOT
DEFAULT_OBJECTIVE_PATH = get_hermes_home() / "notes" / "hermes-epoch-objective.yaml"
DEFAULT_BENCHMARK_HISTORY_PATH = get_hermes_home() / "self_improvement" / "benchmark_history.json"
DEFAULT_SELF_IMPROVEMENT_PROJECT_NAME = "Hermes Self-Improvement"
DEFAULT_SELF_IMPROVEMENT_TEAM_KEY = "HAD"
DEFAULT_BENCHMARK_LOOKBACK_DAYS = 14
BENCHMARK_CONTRACT_VERSION = "v1"
_BENCHMARK_HISTORY_LIMIT = 200
_SELF_IMPROVEMENT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "devops"
    / "hermes-self-improvement-loop"
    / "references"
    / "reward-policy-template.yaml"
)

DEFAULT_FRESHNESS_HOURS = 72
DEFAULT_ACTIVE_STALE_HOURS = 12
PROVENANCE_CONTRACT_VERSION = "v1"


SELF_IMPROVEMENT_EVIDENCE_SCHEMA = {
    "name": "self_improvement_evidence_gate",
    "description": (
        "Evaluate evidence freshness and contradictions for the Hermes self-improvement loop. "
        "Reads journal entries, Codex supervisor runs, ctx session bindings, and ontology "
        "intelligence artifacts to determine whether the reliability floor is degraded and "
        "non-maintenance work should be suppressed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "journal_path": {
                "type": "string",
                "description": "Path to journal.json (defaults to the Hermes journal data file).",
            },
            "codex_runs_path": {
                "type": "string",
                "description": "Path to codex runs.json (defaults to HERMES_HOME/codex/runs.json).",
            },
            "ctx_bindings_path": {
                "type": "string",
                "description": "Path to ctx session_bindings.json (defaults to HERMES_HOME/ctx/session_bindings.json).",
            },
            "ontology_root": {
                "type": "string",
                "description": (
                    "Path to the ontology repo root (defaults to /home/david/stacks/smb-ontology-platform)."
                ),
            },
            "now": {
                "type": "string",
                "description": "Optional ISO timestamp override for tests (defaults to now).",
            },
            "freshness_hours": {
                "type": "integer",
                "description": "Hours before a source is considered stale (default 72).",
                "minimum": 1,
            },
            "active_stale_hours": {
                "type": "integer",
                "description": "Hours after which active Codex/ctx records are treated as stale (default 12).",
                "minimum": 1,
            },
        },
        "required": [],
    },
}


SELF_IMPROVEMENT_BENCHMARK_SCHEMA = {
    "name": "self_improvement_benchmark",
    "description": (
        "Run Hermes self-improvement evals and benchmarks, compare the current "
        "result with prior runs, and persist a scorecard that shows whether "
        "self-evolution is moving in a positive direction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "journal_path": {
                "type": "string",
                "description": "Path to journal.json (defaults to the Hermes journal data file).",
            },
            "codex_runs_path": {
                "type": "string",
                "description": (
                    f"Path to codex runs.json (defaults to {display_hermes_home()}/codex/runs.json)."
                ),
            },
            "ctx_bindings_path": {
                "type": "string",
                "description": (
                    f"Path to ctx session_bindings.json (defaults to {display_hermes_home()}/ctx/session_bindings.json)."
                ),
            },
            "ontology_root": {
                "type": "string",
                "description": (
                    "Path to the ontology repo root "
                    "(defaults to /home/david/stacks/smb-ontology-platform)."
                ),
            },
            "objective_path": {
                "type": "string",
                "description": (
                    "Optional reward-policy/epoch-objective YAML path "
                    f"(defaults to {display_hermes_home()}/notes/hermes-epoch-objective.yaml)."
                ),
            },
            "history_path": {
                "type": "string",
                "description": (
                    "Optional benchmark history path "
                    f"(defaults to {display_hermes_home()}/self_improvement/benchmark_history.json)."
                ),
            },
            "project_name": {
                "type": "string",
                "description": "Linear project name to benchmark (default: Hermes Self-Improvement).",
            },
            "team_key": {
                "type": "string",
                "description": "Linear team key for workflow-state lookup (default: HAD).",
            },
            "now": {
                "type": "string",
                "description": "Optional ISO timestamp override for tests (defaults to now).",
            },
            "freshness_hours": {
                "type": "integer",
                "description": "Hours before a source is considered stale (default 72).",
                "minimum": 1,
            },
            "active_stale_hours": {
                "type": "integer",
                "description": "Hours after which active Codex/ctx records are treated as stale (default 12).",
                "minimum": 1,
            },
            "lookback_days": {
                "type": "integer",
                "description": "Lookback window for recent delivery evidence (default 14 days).",
                "minimum": 1,
            },
            "persist": {
                "type": "boolean",
                "description": "Persist the benchmark result into history (default true).",
            },
        },
        "required": [],
    },
}


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read evidence file %s", path, exc_info=True)
        return None


def _iter_journal_timestamps(payload: Any) -> Iterable[datetime]:
    entries = []
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        for key in ("entries", "items", "journal"):
            if isinstance(payload.get(key), list):
                entries = payload[key]
                break
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in (
            "occurredAt",
            "occurred_at",
            "createdAt",
            "created_at",
            "timestamp",
            "date",
        ):
            if key in entry:
                dt = _parse_time(entry.get(key))
                if dt:
                    yield dt


def _iter_codex_timestamps(payload: Any) -> Iterable[datetime]:
    if not isinstance(payload, dict):
        return
    runs = payload.get("runs")
    if not isinstance(runs, dict):
        return
    for record in runs.values():
        if not isinstance(record, dict):
            continue
        for key in ("completed_at", "started_at", "process_started_at", "updated_at"):
            if key in record:
                dt = _parse_time(record.get(key))
                if dt:
                    yield dt
                    break


def _iter_ctx_timestamps(payload: Any) -> Iterable[datetime]:
    if not isinstance(payload, dict):
        return
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        return
    for record in sessions.values():
        if not isinstance(record, dict):
            continue
        for key in ("updated_at", "created_at"):
            if key in record:
                dt = _parse_time(record.get(key))
                if dt:
                    yield dt
                    break


def _find_stale_active_codex(payload: Any, now: datetime, active_stale_hours: int) -> list[Dict[str, Any]]:
    stale: list[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return stale
    runs = payload.get("runs")
    if not isinstance(runs, dict):
        return stale
    for record in runs.values():
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "").lower()
        if status not in {"running", "unknown"}:
            continue
        ts = _parse_time(record.get("started_at") or record.get("process_started_at"))
        if not ts:
            continue
        age_hours = (now - ts).total_seconds() / 3600
        if age_hours >= active_stale_hours:
            stale.append(
                {
                    "run_id": record.get("run_id"),
                    "status": record.get("status"),
                    "age_hours": round(age_hours, 2),
                }
            )
    return stale


def _find_stale_active_ctx(payload: Any, now: datetime, active_stale_hours: int) -> list[Dict[str, Any]]:
    stale: list[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return stale
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        return stale
    for record in sessions.values():
        if not isinstance(record, dict):
            continue
        if not record.get("active"):
            continue
        ts = _parse_time(record.get("updated_at") or record.get("created_at"))
        if not ts:
            continue
        age_hours = (now - ts).total_seconds() / 3600
        if age_hours >= active_stale_hours:
            stale.append(
                {
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                    "age_hours": round(age_hours, 2),
                }
            )
    return stale


def _iter_ctx_records(payload: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        return
    for record in sessions.values():
        if isinstance(record, dict):
            yield record


def _iter_codex_records(payload: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return
    runs = payload.get("runs")
    if not isinstance(runs, dict):
        return
    for record in runs.values():
        if isinstance(record, dict):
            yield record


def _build_ctx_indexes(payload: Any) -> Dict[str, Dict[str, list[Dict[str, Any]]]]:
    indexes: Dict[str, Dict[str, list[Dict[str, Any]]]] = {
        "task_id": {},
        "ctx_session_id": {},
        "worktree_id": {},
        "worktree_path": {},
    }
    for record in _iter_ctx_records(payload):
        for key, index_name in (
            ("task_id", "task_id"),
            ("ctx_session_id", "ctx_session_id"),
            ("worktree_id", "worktree_id"),
            ("worktree_path", "worktree_path"),
        ):
            value = record.get(key)
            if value:
                bucket = indexes[index_name].setdefault(str(value), [])
                bucket.append(record)
    return indexes


def _has_active_ctx_binding(indexes: Dict[str, Dict[str, list[Dict[str, Any]]]], key: str, value: Any) -> bool:
    if not value:
        return False
    records = indexes.get(key, {}).get(str(value), [])
    return any(bool(record.get("active")) for record in records)


def _find_planning_contradictions(
    codex_payload: Any,
    ctx_payload: Any,
) -> list[Dict[str, Any]]:
    contradictions: list[Dict[str, Any]] = []
    ctx_indexes = _build_ctx_indexes(ctx_payload)

    for record in _iter_ctx_records(ctx_payload):
        if not record.get("active"):
            continue
        worktree_path = record.get("worktree_path")
        reason = str(record.get("reason") or "")
        if reason and "retired" in reason.lower():
            contradictions.append(
                {
                    "type": "ctx_binding_retired_but_active",
                    "message": "ctx binding marked active but reason indicates retirement",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                }
            )
        if not worktree_path:
            contradictions.append(
                {
                    "type": "ctx_binding_missing_worktree_path",
                    "message": "ctx binding marked active but missing worktree_path",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                }
            )
        elif not Path(str(worktree_path)).exists():
            contradictions.append(
                {
                    "type": "ctx_binding_worktree_missing",
                    "message": "ctx binding marked active but worktree path is missing on disk",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                    "worktree_path": worktree_path,
                }
            )

    for record in _iter_codex_records(codex_payload):
        status = str(record.get("status") or "").lower()
        if status not in {"running", "unknown"}:
            continue
        run_id = record.get("run_id")
        if record.get("completed_at") is not None or record.get("exit_code") is not None:
            contradictions.append(
                {
                    "type": "codex_running_but_completed",
                    "message": "codex run marked running but completed fields are set",
                    "run_id": run_id,
                }
            )
        if record.get("stale_reason"):
            contradictions.append(
                {
                    "type": "codex_running_but_stale",
                    "message": "codex run marked running but stale_reason is set",
                    "run_id": run_id,
                    "stale_reason": record.get("stale_reason"),
                }
            )
        ctx_task_id = record.get("ctx_task_id")
        ctx_session_id = record.get("ctx_session_id")
        ctx_worktree_id = record.get("ctx_worktree_id")
        ctx_worktree_path = record.get("ctx_worktree_path")
        ctx_binding_ok = any(
            (
                _has_active_ctx_binding(ctx_indexes, "task_id", ctx_task_id),
                _has_active_ctx_binding(ctx_indexes, "ctx_session_id", ctx_session_id),
                _has_active_ctx_binding(ctx_indexes, "worktree_id", ctx_worktree_id),
                _has_active_ctx_binding(ctx_indexes, "worktree_path", ctx_worktree_path),
            )
        )
        if any((ctx_task_id, ctx_session_id, ctx_worktree_id, ctx_worktree_path)) and not ctx_binding_ok:
            contradictions.append(
                {
                    "type": "codex_ctx_binding_missing",
                    "message": "codex run expects an active ctx binding but none is active",
                    "run_id": run_id,
                    "ctx_task_id": ctx_task_id,
                    "ctx_session_id": ctx_session_id,
                    "ctx_worktree_id": ctx_worktree_id,
                    "ctx_worktree_path": ctx_worktree_path,
                }
            )
        if ctx_worktree_path and not Path(str(ctx_worktree_path)).exists():
            contradictions.append(
                {
                    "type": "codex_worktree_missing",
                    "message": "codex run references a ctx worktree path that is missing on disk",
                    "run_id": run_id,
                    "ctx_worktree_path": ctx_worktree_path,
                }
            )

    return contradictions


def _summarize_source(name: str, latest: Optional[datetime], freshness_hours: int, now: datetime) -> Dict[str, Any]:
    if latest is None:
        return {
            "source": name,
            "status": "missing",
            "age_hours": None,
            "latest_timestamp": None,
        }
    age_hours = (now - latest).total_seconds() / 3600
    status = "fresh" if age_hours <= freshness_hours else "stale"
    return {
        "source": name,
        "status": status,
        "age_hours": round(age_hours, 2),
        "latest_timestamp": latest.isoformat(),
    }


def _build_provenance_item(tag: str, path: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tag": tag,
        "path": str(path),
        "status": summary.get("status"),
        "latest_timestamp": summary.get("latest_timestamp"),
        "age_hours": summary.get("age_hours"),
    }


def _attach_provenance_notes(
    items: list[Dict[str, Any]],
    *,
    stale_active_codex: list[Dict[str, Any]],
    stale_active_ctx: list[Dict[str, Any]],
    ontology_notes: list[str],
    active_stale_hours: int,
) -> None:
    for item in items:
        tag = item.get("tag")
        if tag == "codex" and stale_active_codex:
            item["notes"] = f"{len(stale_active_codex)} active run(s) exceed {active_stale_hours}h"
        if tag == "ctx" and stale_active_ctx:
            item["notes"] = f"{len(stale_active_ctx)} active session(s) exceed {active_stale_hours}h"
        if tag == "ontology" and ontology_notes:
            item["notes"] = " | ".join(ontology_notes)


def format_evidence_provenance(items: Iterable[Dict[str, Any]]) -> str:
    lines = ["Evidence provenance:"]
    for item in items:
        details: list[str] = []
        status = item.get("status")
        if status:
            details.append(f"status={status}")
        age_hours = item.get("age_hours")
        if age_hours is not None:
            details.append(f"age_hours={age_hours}")
        latest = item.get("latest_timestamp")
        if latest:
            details.append(f"latest={latest}")
        path = item.get("path")
        if path:
            details.append(f"path={path}")
        notes = item.get("notes")
        if notes:
            details.append(f"notes={notes}")
        if details:
            lines.append(f"- [{item.get('tag')}] " + "; ".join(details))
        else:
            lines.append(f"- [{item.get('tag')}]")
    return "\n".join(lines)


def evaluate_self_improvement_evidence(
    *,
    journal_path: Path = DEFAULT_JOURNAL_PATH,
    codex_runs_path: Path = DEFAULT_CODEX_RUNS_PATH,
    ctx_bindings_path: Path = DEFAULT_CTX_BINDINGS_PATH,
    ontology_root: Path = DEFAULT_ONTOLOGY_ROOT,
    now: Optional[datetime] = None,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    active_stale_hours: int = DEFAULT_ACTIVE_STALE_HOURS,
) -> Dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    journal_payload = _load_json(journal_path)
    codex_payload = _load_json(codex_runs_path)
    ctx_payload = _load_json(ctx_bindings_path)
    ontology_snapshot = load_ontology_snapshot(ontology_root)
    ontology_summary = summarize_ontology_reliability(
        ontology_snapshot,
        now=current,
        freshness_hours=freshness_hours,
    )

    journal_latest = max(_iter_journal_timestamps(journal_payload), default=None)
    codex_latest = max(_iter_codex_timestamps(codex_payload), default=None)
    ctx_latest = max(_iter_ctx_timestamps(ctx_payload), default=None)
    ontology_latest = _parse_time(ontology_summary.get("latest_timestamp"))

    sources = {
        "journal_entries": _summarize_source("journal_entries", journal_latest, freshness_hours, current),
        "codex_runs": _summarize_source("codex_runs", codex_latest, freshness_hours, current),
        "ctx_bindings": _summarize_source("ctx_bindings", ctx_latest, freshness_hours, current),
        "ontology_intelligence": {
            "source": "ontology_intelligence",
            "status": ontology_summary.get("status"),
            "age_hours": ontology_summary.get("age_hours"),
            "latest_timestamp": ontology_summary.get("latest_timestamp"),
        },
    }

    stale_active_codex = _find_stale_active_codex(codex_payload, current, active_stale_hours)
    stale_active_ctx = _find_stale_active_ctx(ctx_payload, current, active_stale_hours)
    planning_contradictions = _find_planning_contradictions(codex_payload, ctx_payload)

    statuses = {entry["status"] for entry in sources.values()}
    latest_timestamps = [
        ts for ts in (journal_latest, codex_latest, ctx_latest, ontology_latest) if ts is not None
    ]
    freshness_spread_hours = None
    if len(latest_timestamps) >= 2:
        spread_seconds = (max(latest_timestamps) - min(latest_timestamps)).total_seconds()
        freshness_spread_hours = round(spread_seconds / 3600, 2)
    contradictions = []
    if "fresh" in statuses and ("stale" in statuses or "missing" in statuses):
        contradictions.append("evidence freshness mismatch across sources")
    elif freshness_spread_hours is not None and freshness_spread_hours >= active_stale_hours:
        contradictions.append("evidence freshness mismatch across sources")
    if stale_active_codex:
        contradictions.append("stale active Codex runs detected")
    if stale_active_ctx:
        contradictions.append("stale active ctx bindings detected")
    if planning_contradictions:
        contradictions.append("planning contradictions detected")
    if ontology_summary.get("status") in {"missing", "stale"}:
        contradictions.append("ontology intelligence artifacts are stale or missing")

    ontology_alerts = [str(item).strip() for item in ontology_summary.get("alerts", []) if str(item).strip()]

    reasons = []
    for name, entry in sources.items():
        if entry["status"] == "missing":
            reasons.append(f"{name} evidence missing")
        elif entry["status"] == "stale":
            reasons.append(f"{name} evidence stale ({entry['age_hours']}h)")
    if stale_active_codex:
        reasons.append(f"{len(stale_active_codex)} active Codex run(s) exceed {active_stale_hours}h")
    if stale_active_ctx:
        reasons.append(f"{len(stale_active_ctx)} active ctx binding(s) exceed {active_stale_hours}h")
    if planning_contradictions:
        reasons.append(f"{len(planning_contradictions)} planning contradiction(s) detected")
    for item in ontology_summary.get("reasons", []):
        text = str(item).strip()
        if text:
            reasons.append(text)
    if contradictions and not reasons:
        reasons.extend(contradictions)

    degraded = bool(reasons or contradictions)
    gate_status = "degraded" if degraded else "healthy"

    suppression = {
        "suppress_non_maintenance": degraded,
        "message": (
            "Reliability floor degraded: non-maintenance work suppressed."
            if degraded
            else "Reliability floor healthy: normal lane selection permitted."
        ),
    }

    provenance_items = [
        _build_provenance_item("journal", journal_path, sources["journal_entries"]),
        _build_provenance_item("codex", codex_runs_path, sources["codex_runs"]),
        _build_provenance_item("ctx", ctx_bindings_path, sources["ctx_bindings"]),
        _build_provenance_item("ontology", ontology_root, sources["ontology_intelligence"]),
    ]
    _attach_provenance_notes(
        provenance_items,
        stale_active_codex=stale_active_codex,
        stale_active_ctx=stale_active_ctx,
        ontology_notes=[
            *[str(item).strip() for item in ontology_summary.get("reasons", []) if str(item).strip()],
            *ontology_alerts,
        ],
        active_stale_hours=active_stale_hours,
    )
    provenance = {
        "contract_version": PROVENANCE_CONTRACT_VERSION,
        "items": provenance_items,
        "summary_markdown": format_evidence_provenance(provenance_items),
    }

    return {
        "status": gate_status,
        "freshness_hours": freshness_hours,
        "active_stale_hours": active_stale_hours,
        "sources": sources,
        "freshness_spread_hours": freshness_spread_hours,
        "stale_active_codex": stale_active_codex,
        "stale_active_ctx": stale_active_ctx,
        "planning_contradictions": planning_contradictions,
        "ontology": ontology_summary,
        "ontology_alerts": ontology_alerts,
        "contradictions": contradictions,
        "reasons": reasons,
        "suppression": suppression,
        "provenance": provenance,
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read YAML file %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_objective_path(path: Optional[Path] = None) -> Path:
    candidate = path.expanduser() if path else DEFAULT_OBJECTIVE_PATH
    if candidate.exists():
        return candidate
    return _SELF_IMPROVEMENT_TEMPLATE_PATH


def _load_reward_policy(path: Optional[Path] = None) -> dict[str, Any]:
    resolved = _resolve_objective_path(path)
    payload = _read_yaml(resolved)
    payload.setdefault("epoch", {})
    payload.setdefault("guardrails", {})
    payload.setdefault("lanes", {})
    payload["_resolved_path"] = str(resolved)
    return payload


def _load_benchmark_history(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {"version": 1, "evaluations": []}
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        evaluations = []
    return {
        "version": int(payload.get("version") or 1),
        "evaluations": [item for item in evaluations if isinstance(item, dict)],
    }


def _save_benchmark_history(path: Path, payload: dict[str, Any]) -> None:
    atomic_json_write(path, payload)


def _history_record_from_benchmark(benchmark: dict[str, Any]) -> dict[str, Any]:
    return {
        "evaluated_at": benchmark.get("evaluated_at"),
        "score": benchmark.get("score"),
        "direction": benchmark.get("direction"),
        "critical_failures": list(benchmark.get("critical_failures") or []),
        "checks": {
            str(item.get("id")): float(item.get("score") or 0.0)
            for item in benchmark.get("benchmarks", [])
            if isinstance(item, dict) and item.get("id")
        },
    }


def _check_status(score: float) -> str:
    if score >= 0.85:
        return "pass"
    if score >= 0.6:
        return "warn"
    return "fail"


def _build_benchmark_item(
    benchmark_id: str,
    label: str,
    *,
    score: float,
    weight: int,
    detail: str,
    evidence_tags: list[str],
    critical: bool = False,
    recommendation: Optional[str] = None,
    metrics: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clipped = max(0.0, min(1.0, float(score)))
    item = {
        "id": benchmark_id,
        "label": label,
        "score": round(clipped, 3),
        "weight": int(weight),
        "status": _check_status(clipped),
        "detail": detail,
        "evidence_tags": list(evidence_tags),
        "critical": bool(critical),
        "weighted_score": round(clipped * int(weight), 2),
    }
    if recommendation:
        item["recommendation"] = recommendation
    if metrics:
        item["metrics"] = metrics
    return item


def _extract_dedupe_key(body: Any) -> str:
    text = str(body or "").strip()
    if not text.startswith("<!-- hermes-linear:v1 "):
        return ""
    match = re.match(r"<!-- hermes-linear:v1 (.+?) -->", text, re.DOTALL)
    if not match:
        return ""
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("dedupe_key") or "")


def _parse_lane(description: Any) -> Optional[str]:
    text = str(description or "")
    match = re.search(r"(?im)^\s*Lane:\s*(Maintenance|Growth|Capability)\b", text)
    if not match:
        return None
    lane = match.group(1).strip().title()
    return lane if lane in {"Maintenance", "Growth", "Capability"} else None


def _has_verification_expectation(description: Any) -> bool:
    text = str(description or "")
    return bool(
        re.search(
            r"(?im)^\s*(Verification(?: expectation)?|Expected verification|Verification plan)\s*:",
            text,
        )
    )


def _issue_delegate_is_hermes(issue: dict[str, Any]) -> bool:
    delegate = issue.get("delegate")
    if not isinstance(delegate, dict):
        return False
    haystacks = [
        str(delegate.get("name") or ""),
        str(delegate.get("displayName") or ""),
        str(delegate.get("email") or ""),
    ]
    return any("hermes" in value.casefold() for value in haystacks if value)


def _issue_has_assignee(issue: dict[str, Any]) -> bool:
    assignee = issue.get("assignee")
    return isinstance(assignee, dict) and any(
        str(assignee.get(field) or "").strip()
        for field in ("id", "name", "displayName", "email")
    )


def _issue_timestamp(issue: dict[str, Any]) -> Optional[datetime]:
    for key in ("completedAt", "updatedAt", "createdAt"):
        dt = _parse_time(issue.get(key))
        if dt:
            return dt
    return None


def _is_active_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state")
    state_type = str(state.get("type") or "").strip().casefold() if isinstance(state, dict) else ""
    return state_type in {"started", "inprogress", "in_progress"}


def _is_completed_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state")
    state_type = str(state.get("type") or "").strip().casefold() if isinstance(state, dict) else ""
    return state_type in {"completed", "done"}


def _issue_has_status_comment(issue: dict[str, Any]) -> bool:
    comments = issue.get("comments")
    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        dedupe_key = _extract_dedupe_key(comment.get("body"))
        if dedupe_key.startswith("status:"):
            return True
    return False


def _iter_recent_times(payload: Any, *, cutoff: datetime, iterator) -> list[datetime]:
    return [ts for ts in iterator(payload) if ts >= cutoff]


def _codex_record_status(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "").strip().casefold()
    if status in {"completed", "complete", "success", "succeeded", "done"}:
        return "completed"
    if status in {"failed", "error", "errored", "cancelled", "canceled"}:
        return "failed"
    if status in {"running", "unknown"}:
        return "running"
    if record.get("completed_at") is not None:
        return "completed"
    return status or "unknown"


def _record_timestamp(record: dict[str, Any], *keys: str) -> Optional[datetime]:
    for key in keys:
        dt = _parse_time(record.get(key))
        if dt:
            return dt
    return None


def _load_linear_benchmark_surface(
    *,
    project_name: str,
    issue_limit: int = 100,
    comment_limit: int = 50,
) -> dict[str, Any]:
    try:
        from tools import linear_issue_tool as linear_tool
    except Exception as exc:
        return {
            "available": False,
            "project": None,
            "issues": [],
            "error": f"linear tool unavailable: {exc}",
        }

    if not linear_tool.check_linear_issue_requirements():
        return {
            "available": False,
            "project": None,
            "issues": [],
            "error": "LINEAR_API_KEY is not configured for Hermes",
        }

    try:
        project = linear_tool._find_project_by_name(
            linear_tool._list_projects(limit=100),
            project_name,
        )
        if not project:
            return {
                "available": True,
                "project": None,
                "issues": [],
                "error": f"Linear project not found: {project_name}",
            }

        raw_issues = linear_tool._project_issues(str(project.get("id") or ""), limit=issue_limit)
        enriched: list[dict[str, Any]] = []
        for issue in raw_issues:
            entry = dict(issue)
            if _is_active_issue(entry):
                try:
                    detailed = linear_tool._fetch_issue(
                        str(entry.get("identifier") or entry.get("id") or ""),
                        comment_limit=comment_limit,
                    )
                    if isinstance(detailed, dict):
                        entry["comments"] = detailed.get("comments", [])
                except Exception:
                    logger.debug(
                        "Failed to fetch Linear comments for %s",
                        entry.get("identifier") or entry.get("id"),
                        exc_info=True,
                    )
            enriched.append(entry)
        return {
            "available": True,
            "project": project,
            "issues": enriched,
            "error": None,
        }
    except Exception as exc:
        return {
            "available": True,
            "project": None,
            "issues": [],
            "error": str(exc),
        }


def _format_benchmark_summary(
    *,
    score: float,
    direction: str,
    trend: str,
    benchmarks: list[dict[str, Any]],
    critical_failures: list[str],
    recommendations: list[str],
) -> str:
    lines = [
        (
            f"Self-improvement benchmark (`{BENCHMARK_CONTRACT_VERSION}`): "
            f"score={score:.1f}/100; direction={direction}; trend={trend}"
        )
    ]
    if critical_failures:
        lines.append("Critical failures: " + ", ".join(critical_failures))
    for item in benchmarks:
        tags = ", ".join(f"[{tag}]" for tag in item.get("evidence_tags", []))
        lines.append(
            f"- {item.get('id')}: {item.get('status')} "
            f"({float(item.get('score') or 0.0):.2f}) {item.get('detail')} {tags}".rstrip()
        )
    if recommendations:
        lines.append("Recommendations:")
        for text in recommendations[:5]:
            lines.append(f"- {text}")
    return "\n".join(lines)


def evaluate_self_improvement_benchmark(
    *,
    journal_path: Path = DEFAULT_JOURNAL_PATH,
    codex_runs_path: Path = DEFAULT_CODEX_RUNS_PATH,
    ctx_bindings_path: Path = DEFAULT_CTX_BINDINGS_PATH,
    ontology_root: Path = DEFAULT_ONTOLOGY_ROOT,
    objective_path: Optional[Path] = None,
    history_path: Path = DEFAULT_BENCHMARK_HISTORY_PATH,
    project_name: str = DEFAULT_SELF_IMPROVEMENT_PROJECT_NAME,
    team_key: str = DEFAULT_SELF_IMPROVEMENT_TEAM_KEY,
    now: Optional[datetime] = None,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    active_stale_hours: int = DEFAULT_ACTIVE_STALE_HOURS,
    lookback_days: int = DEFAULT_BENCHMARK_LOOKBACK_DAYS,
    persist: bool = True,
) -> dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    cutoff = current - timedelta(days=max(1, int(lookback_days or DEFAULT_BENCHMARK_LOOKBACK_DAYS)))
    gate = evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_runs_path,
        ctx_bindings_path=ctx_bindings_path,
        ontology_root=ontology_root,
        now=current,
        freshness_hours=freshness_hours,
        active_stale_hours=active_stale_hours,
    )
    reward_policy = _load_reward_policy(objective_path)
    linear_surface = _load_linear_benchmark_surface(project_name=project_name)
    ontology_context = build_self_improvement_context(
        repo_root=ontology_root,
        now=current,
        freshness_hours=freshness_hours,
    )

    journal_payload = _load_json(journal_path)
    codex_payload = _load_json(codex_runs_path)
    ctx_payload = _load_json(ctx_bindings_path)

    recent_journal_entries = _iter_recent_times(
        journal_payload,
        cutoff=cutoff,
        iterator=_iter_journal_timestamps,
    )
    recent_codex_records = [
        record
        for record in _iter_codex_records(codex_payload)
        if (_record_timestamp(record, "completed_at", "started_at", "updated_at", "process_started_at") or current)
        >= cutoff
    ]
    recent_completed_codex = [
        record for record in recent_codex_records if _codex_record_status(record) == "completed"
    ]
    recent_ctx_records = [
        record
        for record in _iter_ctx_records(ctx_payload)
        if (_record_timestamp(record, "updated_at", "created_at") or current) >= cutoff
    ]

    issues = [
        issue for issue in linear_surface.get("issues", [])
        if isinstance(issue, dict)
    ]
    active_issues = [issue for issue in issues if _is_active_issue(issue)]
    recent_done_issues = [
        issue for issue in issues
        if _is_completed_issue(issue) and ((_issue_timestamp(issue) or cutoff) >= cutoff)
    ]
    issues_with_lane = [issue for issue in issues if _parse_lane(issue.get("description"))]
    issues_with_verification = [
        issue for issue in issues if _has_verification_expectation(issue.get("description"))
    ]
    active_issues_with_status = [issue for issue in active_issues if _issue_has_status_comment(issue)]
    hermes_delegate_issues = [issue for issue in issues if _issue_delegate_is_hermes(issue)]
    delegate_conflicts = [
        issue for issue in hermes_delegate_issues if _issue_has_assignee(issue)
    ]

    lane_counts: dict[str, int] = {"Maintenance": 0, "Growth": 0, "Capability": 0}
    active_non_maintenance: list[str] = []
    for issue in active_issues:
        lane = _parse_lane(issue.get("description"))
        if lane in lane_counts:
            lane_counts[lane] += 1
            if lane != "Maintenance":
                active_non_maintenance.append(str(issue.get("identifier") or issue.get("id") or ""))

    guardrails = reward_policy.get("guardrails", {})
    lanes = reward_policy.get("lanes", {})
    capability_budget_percent = int(
        guardrails.get("capability_budget_percent")
        or ((lanes.get("capability") or {}).get("default_budget_percent") or 20)
    )
    max_active_issues_per_lane = int(guardrails.get("max_active_issues_per_lane") or 1)
    active_total = len(active_issues)
    active_capability_ratio = (lane_counts["Capability"] / active_total) if active_total else 0.0
    overflow_lanes = {
        lane: count
        for lane, count in lane_counts.items()
        if count > max_active_issues_per_lane
    }

    provider_summary = ((ontology_context.get("research_provider_policy") or {}).get("summary") or {})
    available_provider_count = int(provider_summary.get("available_provider_count") or 0)
    upgrade_target_count = len((ontology_context.get("textbook_study") or {}).get("upgrade_targets") or [])
    business_recommendation_count = len(ontology_context.get("business_recommendations") or [])
    ontology_reliability = ontology_context.get("reliability") or {}

    source_statuses = [
        str((entry or {}).get("status") or "")
        for entry in (gate.get("sources") or {}).values()
        if isinstance(entry, dict)
    ]
    stale_source_count = sum(status in {"stale", "missing"} for status in source_statuses)
    reliability_score = 1.0
    reliability_score -= 0.2 * stale_source_count
    reliability_score -= 0.15 if gate.get("stale_active_codex") else 0.0
    reliability_score -= 0.15 if gate.get("stale_active_ctx") else 0.0
    reliability_score -= 0.15 if gate.get("planning_contradictions") else 0.0
    reliability_score -= 0.15 if str((gate.get("ontology") or {}).get("status") or "") in {"stale", "missing"} else 0.0
    if gate.get("status") != "healthy":
        reliability_score = min(reliability_score, 0.45)

    execution_loop_score = (
        0.6 * min(len(recent_completed_codex) / 2.0, 1.0)
        + 0.4 * min(len(recent_ctx_records) / 2.0, 1.0)
    )

    stale_execution_score = 1.0
    stale_execution_score -= 0.45 if gate.get("stale_active_codex") else 0.0
    stale_execution_score -= 0.35 if gate.get("stale_active_ctx") else 0.0
    stale_execution_score -= 0.20 if gate.get("planning_contradictions") else 0.0

    if not linear_surface.get("available"):
        linear_planning_score = 0.0
    elif not linear_surface.get("project"):
        linear_planning_score = 0.0
    else:
        issue_count = len(issues)
        lane_rate = (len(issues_with_lane) / issue_count) if issue_count else 0.0
        verification_rate = (len(issues_with_verification) / issue_count) if issue_count else 0.0
        status_comment_rate = (
            len(active_issues_with_status) / len(active_issues)
            if active_issues
            else 1.0
        )
        linear_planning_score = (
            0.30
            + 0.20 * (1.0 if issue_count else 0.0)
            + 0.20 * lane_rate
            + 0.15 * verification_rate
            + 0.15 * status_comment_rate
        )

    if not issues:
        delegate_assignment_score = 0.0
    elif not hermes_delegate_issues:
        delegate_assignment_score = 0.0
    else:
        delegate_assignment_score = max(
            0.0,
            1.0 - (len(delegate_conflicts) / max(1, len(hermes_delegate_issues))),
        )

    if not issues:
        reward_alignment_score = 0.0
    elif gate.get("status") == "degraded" and active_non_maintenance:
        reward_alignment_score = 0.0
    elif not active_issues:
        reward_alignment_score = 0.8 if gate.get("status") == "healthy" else 0.2
    else:
        reward_alignment_score = 1.0
        if overflow_lanes:
            reward_alignment_score -= min(0.5, 0.25 * len(overflow_lanes))
        if active_capability_ratio > (capability_budget_percent / 100.0):
            reward_alignment_score -= min(
                0.3,
                (active_capability_ratio - (capability_budget_percent / 100.0)) * 1.5,
            )
        if any(_parse_lane(issue.get("description")) is None for issue in active_issues):
            reward_alignment_score -= 0.2

    recent_delivery_score = (
        0.5 * min(len(recent_done_issues) / 2.0, 1.0)
        + 0.5 * min(len(recent_completed_codex) / 2.0, 1.0)
    )

    ontology_readiness_score = 0.0
    ontology_status = str(ontology_reliability.get("status") or "")
    if ontology_status == "fresh":
        ontology_readiness_score += 0.4
    elif ontology_status == "stale":
        ontology_readiness_score += 0.2
    ontology_readiness_score += 0.4 * min(available_provider_count / 2.0, 1.0)
    if upgrade_target_count or business_recommendation_count:
        ontology_readiness_score += 0.2

    benchmarks = [
        _build_benchmark_item(
            "reliability_gate",
            "Reliability gate",
            score=reliability_score,
            weight=25,
            detail=(
                "Reliability floor is healthy."
                if gate.get("status") == "healthy"
                else "; ".join(gate.get("reasons") or gate.get("contradictions") or ["Reliability floor degraded."])
            ),
            evidence_tags=["journal", "codex", "ctx", "ontology"],
            critical=True,
            recommendation="Repair stale evidence and contradictions before funding new Growth or Capability work.",
            metrics={
                "gate_status": gate.get("status"),
                "stale_source_count": stale_source_count,
                "contradiction_count": len(gate.get("contradictions") or []),
            },
        ),
        _build_benchmark_item(
            "execution_loop",
            "Recent execution loop",
            score=execution_loop_score,
            weight=15,
            detail=(
                f"{len(recent_completed_codex)} completed Codex run(s), "
                f"{len(recent_ctx_records)} ctx binding(s), "
                f"{len(recent_journal_entries)} journal entry timestamp(s) in the last {lookback_days}d."
            ),
            evidence_tags=["codex", "ctx", "journal"],
            critical=True,
            recommendation="Run real bounded Codex work through ctx and let Hermes record the outcome.",
            metrics={
                "recent_completed_codex_runs": len(recent_completed_codex),
                "recent_ctx_bindings": len(recent_ctx_records),
                "recent_journal_entries": len(recent_journal_entries),
            },
        ),
        _build_benchmark_item(
            "stale_execution_records",
            "No stale execution records",
            score=stale_execution_score,
            weight=15,
            detail=(
                f"{len(gate.get('stale_active_codex') or [])} stale Codex run(s), "
                f"{len(gate.get('stale_active_ctx') or [])} stale ctx binding(s), "
                f"{len(gate.get('planning_contradictions') or [])} planning contradiction(s)."
            ),
            evidence_tags=["codex", "ctx"],
            critical=True,
            recommendation="Retire stale ctx/Codex records and resolve planning contradictions before trusting the agenda.",
        ),
        _build_benchmark_item(
            "linear_planning_surface",
            "Linear planning surface",
            score=linear_planning_score,
            weight=15,
            detail=(
                linear_surface.get("error")
                or (
                    f"Project {project_name!r} has {len(issues)} issue(s); "
                    f"{len(issues_with_lane)}/{len(issues) or 1} carry lanes, "
                    f"{len(issues_with_verification)}/{len(issues) or 1} carry verification expectations, "
                    f"{len(active_issues_with_status)}/{len(active_issues) or 1} active issue(s) carry status comments."
                )
            ),
            evidence_tags=["repo"],
            critical=True,
            recommendation="Keep the self-improvement project present, lane-tagged, verification-tagged, and updated with deduped status comments.",
        ),
        _build_benchmark_item(
            "delegate_assignment_hygiene",
            "Delegate/assignee hygiene",
            score=delegate_assignment_score,
            weight=10,
            detail=(
                f"{len(delegate_conflicts)} Hermes-delegated issue(s) still carry a human assignee out of "
                f"{len(hermes_delegate_issues)} delegated issue(s)."
            ),
            evidence_tags=["repo"],
            critical=True,
            recommendation="When Hermes owns the work, set delegateId and clear assigneeId unless a human is explicitly required.",
        ),
        _build_benchmark_item(
            "reward_policy_alignment",
            "Reward-policy alignment",
            score=reward_alignment_score,
            weight=10,
            detail=(
                f"Active lanes={lane_counts}; overflow_lanes={overflow_lanes or 'none'}; "
                f"capability_ratio={active_capability_ratio:.1%}; gate={gate.get('status')}."
            ),
            evidence_tags=["repo", "inference"],
            critical=True,
            recommendation="Keep at most one active issue per lane, respect the capability budget, and suppress non-maintenance work when the reliability gate is degraded.",
        ),
        _build_benchmark_item(
            "recent_delivery_outcomes",
            "Recent delivery outcomes",
            score=recent_delivery_score,
            weight=5,
            detail=(
                f"{len(recent_done_issues)} done Linear issue(s) and "
                f"{len(recent_completed_codex)} completed Codex run(s) in the last {lookback_days}d."
            ),
            evidence_tags=["repo", "codex"],
            recommendation="Close the loop on real issues and let the benchmark observe completed work, not just planning artifacts.",
        ),
        _build_benchmark_item(
            "ontology_readiness",
            "Ontology readiness",
            score=ontology_readiness_score,
            weight=5,
            detail=(
                f"Ontology status={ontology_status or 'unknown'}; "
                f"providers={available_provider_count}; "
                f"upgrade_targets={upgrade_target_count}; "
                f"business_recommendations={business_recommendation_count}."
            ),
            evidence_tags=["ontology"],
            recommendation="Keep ontology artifacts fresh and maintain multi-provider research coverage so ontology-driven self-improvement stays grounded.",
        ),
    ]

    total_weight = sum(int(item.get("weight") or 0) for item in benchmarks) or 1
    overall_score = round(
        sum(float(item.get("weighted_score") or 0.0) for item in benchmarks) / total_weight * 100.0,
        2,
    )
    critical_failures = [
        str(item.get("id"))
        for item in benchmarks
        if item.get("critical") and item.get("status") == "fail"
    ]
    direction = "positive"
    if critical_failures or overall_score < 60.0:
        direction = "negative"
    elif overall_score < 80.0:
        direction = "mixed"

    history = _load_benchmark_history(history_path.expanduser())
    previous = history["evaluations"][-1] if history["evaluations"] else None
    previous_score = float(previous.get("score")) if isinstance(previous, dict) and previous.get("score") is not None else None
    best_score = (
        max(float(item.get("score") or 0.0) for item in history["evaluations"])
        if history["evaluations"]
        else None
    )
    delta_vs_previous = round(overall_score - previous_score, 2) if previous_score is not None else None
    delta_vs_best = round(overall_score - best_score, 2) if best_score is not None else None
    trend = "baseline"
    if delta_vs_previous is not None:
        if delta_vs_previous >= 5.0:
            trend = "improving"
        elif delta_vs_previous <= -5.0:
            trend = "regressing"
        else:
            trend = "flat"

    previous_checks = previous.get("checks", {}) if isinstance(previous, dict) else {}
    regressions = []
    improvements = []
    for item in benchmarks:
        benchmark_id = str(item.get("id") or "")
        if benchmark_id not in previous_checks:
            continue
        prior_score = float(previous_checks.get(benchmark_id) or 0.0)
        delta = round(float(item.get("score") or 0.0) - prior_score, 3)
        if delta <= -0.2:
            regressions.append({"id": benchmark_id, "delta": delta})
        elif delta >= 0.2:
            improvements.append({"id": benchmark_id, "delta": delta})

    recommendations = []
    for item in benchmarks:
        if item.get("status") == "fail" and item.get("recommendation"):
            recommendations.append(str(item.get("recommendation")))

    benchmark = {
        "contract_version": BENCHMARK_CONTRACT_VERSION,
        "evaluated_at": current.isoformat(),
        "objective_name": str((reward_policy.get("epoch") or {}).get("name") or ""),
        "objective_review_question": str((reward_policy.get("epoch") or {}).get("review_question") or ""),
        "objective_path": reward_policy.get("_resolved_path"),
        "score": overall_score,
        "direction": direction,
        "trend": trend,
        "positive_direction": direction == "positive" and trend != "regressing",
        "lookback_days": int(lookback_days),
        "benchmarks": benchmarks,
        "critical_failures": critical_failures,
        "recommendations": recommendations,
        "history": {
            "path": str(history_path.expanduser()),
            "persisted": bool(persist),
            "previous_score": previous_score,
            "best_score": best_score,
            "delta_vs_previous": delta_vs_previous,
            "delta_vs_best": delta_vs_best,
            "evaluation_count_before_run": len(history["evaluations"]),
        },
        "improvements": improvements,
        "regressions": regressions,
        "linear_surface": {
            "available": linear_surface.get("available"),
            "project_name": project_name,
            "team_key": team_key,
            "project_id": (linear_surface.get("project") or {}).get("id") if isinstance(linear_surface.get("project"), dict) else None,
            "issue_count": len(issues),
            "active_issue_count": len(active_issues),
            "done_issue_count_recent": len(recent_done_issues),
            "delegate_conflict_count": len(delegate_conflicts),
            "lane_counts": lane_counts,
            "overflow_lanes": overflow_lanes,
            "error": linear_surface.get("error"),
        },
        "summary_markdown": _format_benchmark_summary(
            score=overall_score,
            direction=direction,
            trend=trend,
            benchmarks=benchmarks,
            critical_failures=critical_failures,
            recommendations=recommendations,
        ),
    }

    if persist:
        snapshot = _load_benchmark_history(history_path.expanduser())
        snapshot["evaluations"].append(_history_record_from_benchmark(benchmark))
        snapshot["evaluations"] = snapshot["evaluations"][-_BENCHMARK_HISTORY_LIMIT:]
        _save_benchmark_history(history_path.expanduser(), snapshot)
        benchmark["history"]["evaluation_count_after_run"] = len(snapshot["evaluations"])
    else:
        benchmark["history"]["evaluation_count_after_run"] = len(history["evaluations"])

    return benchmark


def self_improvement_benchmark(
    journal_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    ctx_bindings_path: Optional[str] = None,
    ontology_root: Optional[str] = None,
    objective_path: Optional[str] = None,
    history_path: Optional[str] = None,
    project_name: Optional[str] = None,
    team_key: Optional[str] = None,
    now: Optional[str] = None,
    freshness_hours: Optional[int] = None,
    active_stale_hours: Optional[int] = None,
    lookback_days: Optional[int] = None,
    persist: Optional[bool] = None,
    task_id: Optional[str] = None,
) -> str:
    benchmark = evaluate_self_improvement_benchmark(
        journal_path=Path(journal_path).expanduser() if journal_path else DEFAULT_JOURNAL_PATH,
        codex_runs_path=Path(codex_runs_path).expanduser() if codex_runs_path else DEFAULT_CODEX_RUNS_PATH,
        ctx_bindings_path=Path(ctx_bindings_path).expanduser() if ctx_bindings_path else DEFAULT_CTX_BINDINGS_PATH,
        ontology_root=Path(ontology_root).expanduser() if ontology_root else DEFAULT_ONTOLOGY_ROOT,
        objective_path=Path(objective_path).expanduser() if objective_path else None,
        history_path=Path(history_path).expanduser() if history_path else DEFAULT_BENCHMARK_HISTORY_PATH,
        project_name=str(project_name or DEFAULT_SELF_IMPROVEMENT_PROJECT_NAME),
        team_key=str(team_key or DEFAULT_SELF_IMPROVEMENT_TEAM_KEY),
        now=_parse_time(now) if now else None,
        freshness_hours=int(freshness_hours) if freshness_hours else DEFAULT_FRESHNESS_HOURS,
        active_stale_hours=int(active_stale_hours) if active_stale_hours else DEFAULT_ACTIVE_STALE_HOURS,
        lookback_days=int(lookback_days) if lookback_days else DEFAULT_BENCHMARK_LOOKBACK_DAYS,
        persist=True if persist is None else bool(persist),
    )
    return json.dumps({"success": True, "benchmark": benchmark, "task_id": task_id})


def self_improvement_evidence_gate(
    journal_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    ctx_bindings_path: Optional[str] = None,
    ontology_root: Optional[str] = None,
    now: Optional[str] = None,
    freshness_hours: Optional[int] = None,
    active_stale_hours: Optional[int] = None,
    task_id: Optional[str] = None,
) -> str:
    gate = evaluate_self_improvement_evidence(
        journal_path=Path(journal_path).expanduser() if journal_path else DEFAULT_JOURNAL_PATH,
        codex_runs_path=Path(codex_runs_path).expanduser() if codex_runs_path else DEFAULT_CODEX_RUNS_PATH,
        ctx_bindings_path=Path(ctx_bindings_path).expanduser() if ctx_bindings_path else DEFAULT_CTX_BINDINGS_PATH,
        ontology_root=Path(ontology_root).expanduser() if ontology_root else DEFAULT_ONTOLOGY_ROOT,
        now=_parse_time(now) if now else None,
        freshness_hours=int(freshness_hours) if freshness_hours else DEFAULT_FRESHNESS_HOURS,
        active_stale_hours=int(active_stale_hours) if active_stale_hours else DEFAULT_ACTIVE_STALE_HOURS,
    )
    return json.dumps({"success": True, "gate": gate, "task_id": task_id})


registry.register(
    name="self_improvement_evidence_gate",
    toolset="self_improvement",
    schema=SELF_IMPROVEMENT_EVIDENCE_SCHEMA,
    handler=lambda args, **kw: self_improvement_evidence_gate(
        journal_path=args.get("journal_path"),
        codex_runs_path=args.get("codex_runs_path"),
        ctx_bindings_path=args.get("ctx_bindings_path"),
        ontology_root=args.get("ontology_root"),
        now=args.get("now"),
        freshness_hours=args.get("freshness_hours"),
        active_stale_hours=args.get("active_stale_hours"),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="self_improvement_benchmark",
    toolset="self_improvement",
    schema=SELF_IMPROVEMENT_BENCHMARK_SCHEMA,
    handler=lambda args, **kw: self_improvement_benchmark(
        journal_path=args.get("journal_path"),
        codex_runs_path=args.get("codex_runs_path"),
        ctx_bindings_path=args.get("ctx_bindings_path"),
        ontology_root=args.get("ontology_root"),
        objective_path=args.get("objective_path"),
        history_path=args.get("history_path"),
        project_name=args.get("project_name"),
        team_key=args.get("team_key"),
        now=args.get("now"),
        freshness_hours=args.get("freshness_hours"),
        active_stale_hours=args.get("active_stale_hours"),
        lookback_days=args.get("lookback_days"),
        persist=args.get("persist"),
        task_id=kw.get("task_id"),
    ),
)
