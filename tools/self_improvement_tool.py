"""Reliability gate for Hermes self-improvement evidence.

This is the small repo-local reliability floor retained after the Hadto-specific
orchestration stack moved to plugins. It deliberately avoids Linear writeback
and cross-repo ontology orchestration; callers provide paths to evidence files
and get a deterministic scorecard back.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import display_hermes_home, get_hermes_home
from tools.registry import registry
from utils import atomic_json_write

logger = logging.getLogger(__name__)


DEFAULT_JOURNAL_PATH = Path("/home/david/stacks/hermes-journal/src/data/journal.json")
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"
DEFAULT_CTX_BINDINGS_PATH = get_hermes_home() / "ctx" / "session_bindings.json"
DEFAULT_ONTOLOGY_ROOT = Path("/home/david/stacks/smb-ontology-platform")
DEFAULT_BENCHMARK_HISTORY_PATH = get_hermes_home() / "self_improvement" / "benchmark_history.json"
DEFAULT_FRESHNESS_HOURS = 72
DEFAULT_ACTIVE_STALE_HOURS = 12
PROVENANCE_CONTRACT_VERSION = "v1"
BENCHMARK_CONTRACT_VERSION = "v1"
_BENCHMARK_HISTORY_LIMIT = 200
_ONTOLOGY_SCAN_SUFFIXES = {".json", ".yaml", ".yml", ".md"}
_ONTOLOGY_SCAN_PRUNED_DIRS = {".git"}


SELF_IMPROVEMENT_EVIDENCE_SCHEMA = {
    "name": "self_improvement_evidence_gate",
    "description": (
        "Evaluate freshness and consistency of Hermes self-improvement evidence "
        "without creating or updating backlog work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "journal_path": {"type": "string"},
            "codex_runs_path": {"type": "string"},
            "ctx_bindings_path": {"type": "string"},
            "ontology_root": {"type": "string"},
            "now": {"type": "string"},
            "freshness_hours": {"type": "integer", "minimum": 1},
            "active_stale_hours": {"type": "integer", "minimum": 1},
        },
        "required": [],
    },
}


SELF_IMPROVEMENT_BENCHMARK_SCHEMA = {
    "name": "self_improvement_benchmark",
    "description": (
        "Score the Hermes self-improvement reliability floor and optionally "
        f"persist benchmark history under {display_hermes_home()}/self_improvement."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "journal_path": {"type": "string"},
            "codex_runs_path": {"type": "string"},
            "ctx_bindings_path": {"type": "string"},
            "ontology_root": {"type": "string"},
            "history_path": {"type": "string"},
            "now": {"type": "string"},
            "freshness_hours": {"type": "integer", "minimum": 1},
            "active_stale_hours": {"type": "integer", "minimum": 1},
            "persist": {"type": "boolean"},
        },
        "required": [],
    },
}


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read JSON evidence file %s", path, exc_info=True)
        return None


def _iter_records(payload: Any, key: str) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, dict):
            for item in value.values():
                if isinstance(item, dict):
                    yield item
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return
        if all(not isinstance(item, (dict, list)) for item in payload.values()):
            yield payload
            return
        for item in payload.values():
            if isinstance(item, dict):
                yield item
            elif isinstance(item, list):
                for child in item:
                    if isinstance(child, dict):
                        yield child
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def _record_timestamp(record: dict[str, Any], *keys: str) -> Optional[datetime]:
    for key in keys:
        parsed = _parse_time(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _iter_journal_timestamps(payload: Any) -> Iterable[datetime]:
    for record in _iter_records(payload, "entries"):
        parsed = _record_timestamp(
            record,
            "occurredAt",
            "occurred_at",
            "updatedAt",
            "updated_at",
            "createdAt",
            "created_at",
            "timestamp",
            "date",
        )
        if parsed is not None:
            yield parsed


def _iter_codex_records(payload: Any) -> Iterable[dict[str, Any]]:
    yield from _iter_records(payload, "runs")


def _iter_codex_timestamps(payload: Any) -> Iterable[datetime]:
    for record in _iter_codex_records(payload):
        parsed = _record_timestamp(
            record,
            "completed_at",
            "updated_at",
            "started_at",
            "process_started_at",
            "created_at",
            "timestamp",
        )
        if parsed is not None:
            yield parsed


def _iter_ctx_records(payload: Any) -> Iterable[dict[str, Any]]:
    yield from _iter_records(payload, "sessions")


def _iter_ctx_timestamps(payload: Any) -> Iterable[datetime]:
    for record in _iter_ctx_records(payload):
        parsed = _record_timestamp(
            record,
            "updated_at",
            "updatedAt",
            "created_at",
            "createdAt",
            "timestamp",
        )
        if parsed is not None:
            yield parsed


def _latest_timestamp(values: Iterable[datetime]) -> Optional[datetime]:
    return max(values, default=None)


def _summarize_source(
    name: str,
    latest: Optional[datetime],
    freshness_hours: int,
    now: datetime,
) -> dict[str, Any]:
    if latest is None:
        return {
            "source": name,
            "status": "missing",
            "age_hours": None,
            "latest_timestamp": None,
        }
    age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    return {
        "source": name,
        "status": "fresh" if age_hours <= freshness_hours else "stale",
        "age_hours": round(age_hours, 2),
        "latest_timestamp": latest.isoformat(),
    }


def _extract_timestamps_from_text(text: str) -> Iterable[datetime]:
    for match in re.finditer(
        r"\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+(?:Z|[+-]\d{2}:?\d{2})?",
        text,
    ):
        parsed = _parse_time(match.group(0).replace(" ", "T", 1))
        if parsed is not None:
            yield parsed


def _scan_ontology_file(path: Path) -> tuple[list[datetime], list[str]]:
    timestamps: list[datetime] = []
    alerts: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return timestamps, [f"{path.name} unreadable"]

    if path.suffix == ".json":
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            for key in (
                "generated_at",
                "updated_at",
                "prepared_at",
                "last_evolved",
                "timestamp",
                "created_at",
            ):
                parsed = _parse_time(payload.get(key))
                if parsed is not None:
                    timestamps.append(parsed)
            reliability = payload.get("reliability")
            status = ""
            if isinstance(reliability, dict):
                status = str(reliability.get("status") or "")
            status = status or str(payload.get("status") or "")
            if status.strip().lower() in {"degraded", "error", "failed", "missing", "stale"}:
                alerts.append(f"{path.name} status={status.strip().lower()}")
    else:
        timestamps.extend(_extract_timestamps_from_text(text))

    return timestamps, alerts


def _iter_ontology_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in _ONTOLOGY_SCAN_PRUNED_DIRS
        ]
        current_dir = Path(dirpath)
        for filename in filenames:
            path = current_dir / filename
            if path.suffix.lower() in _ONTOLOGY_SCAN_SUFFIXES:
                yield path


def _summarize_ontology(root: Path, freshness_hours: int, now: datetime) -> dict[str, Any]:
    if not root.exists():
        return {
            "status": "missing",
            "latest_timestamp": None,
            "age_hours": None,
            "reasons": ["ontology root missing"],
            "alerts": [],
        }

    timestamps: list[datetime] = []
    alerts: list[str] = []
    for path in _iter_ontology_files(root):
        file_timestamps, file_alerts = _scan_ontology_file(path)
        timestamps.extend(file_timestamps)
        alerts.extend(file_alerts)

    latest = _latest_timestamp(timestamps)
    if latest is None:
        return {
            "status": "missing",
            "latest_timestamp": None,
            "age_hours": None,
            "reasons": ["ontology intelligence timestamp missing"],
            "alerts": alerts,
        }

    age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    status = "fresh" if age_hours <= freshness_hours else "stale"
    reasons: list[str] = []
    if status == "stale":
        reasons.append(f"ontology_intelligence stale ({round(age_hours, 2)}h)")
    if alerts:
        status = "degraded"
        reasons.extend(alerts)
    return {
        "status": status,
        "latest_timestamp": latest.isoformat(),
        "age_hours": round(age_hours, 2),
        "reasons": reasons,
        "alerts": alerts,
    }


def _codex_record_status(record: dict[str, Any]) -> str:
    return str(record.get("status") or "").strip().lower()


def _codex_record_is_active(record: dict[str, Any]) -> bool:
    status = _codex_record_status(record)
    if status in {"running", "queued", "in_progress", "active", "unknown"}:
        return True
    if record.get("active") is True:
        return True
    return record.get("completed_at") is None and record.get("exit_code") is None and bool(status)


def _find_stale_active_codex(
    payload: Any,
    now: datetime,
    active_stale_hours: int,
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for record in _iter_codex_records(payload):
        if not _codex_record_is_active(record):
            continue
        started = _record_timestamp(
            record,
            "updated_at",
            "started_at",
            "process_started_at",
            "created_at",
            "timestamp",
        )
        if started is None:
            continue
        age_hours = max(0.0, (now - started).total_seconds() / 3600)
        if age_hours > active_stale_hours:
            stale.append(
                {
                    "run_id": record.get("run_id") or record.get("id"),
                    "status": record.get("status"),
                    "age_hours": round(age_hours, 2),
                    "latest_timestamp": started.isoformat(),
                }
            )
    return stale


def _find_stale_active_ctx(
    payload: Any,
    now: datetime,
    active_stale_hours: int,
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for record in _iter_ctx_records(payload):
        if not record.get("active"):
            continue
        updated = _record_timestamp(record, "updated_at", "updatedAt", "created_at", "createdAt")
        if updated is None:
            continue
        age_hours = max(0.0, (now - updated).total_seconds() / 3600)
        if age_hours > active_stale_hours:
            stale.append(
                {
                    "session_id": record.get("session_id") or record.get("id"),
                    "task_id": record.get("task_id"),
                    "age_hours": round(age_hours, 2),
                    "latest_timestamp": updated.isoformat(),
                }
            )
    return stale


def _find_planning_contradictions(codex_payload: Any, ctx_payload: Any) -> list[dict[str, Any]]:
    contradictions: list[dict[str, Any]] = []
    for record in _iter_ctx_records(ctx_payload):
        if not record.get("active"):
            continue
        worktree_path = str(record.get("worktree_path") or "").strip()
        reason = str(record.get("reason") or "").strip().lower()
        if "retired" in reason:
            contradictions.append(
                {
                    "type": "ctx_binding_retired_but_active",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                }
            )
        if not worktree_path:
            contradictions.append(
                {
                    "type": "ctx_binding_missing_worktree_path",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                }
            )
        elif not Path(worktree_path).exists():
            contradictions.append(
                {
                    "type": "ctx_binding_worktree_missing",
                    "session_id": record.get("session_id"),
                    "task_id": record.get("task_id"),
                    "worktree_path": worktree_path,
                }
            )

    for record in _iter_codex_records(codex_payload):
        if not _codex_record_is_active(record):
            continue
        if record.get("completed_at") is not None or record.get("exit_code") is not None:
            contradictions.append(
                {
                    "type": "codex_running_but_completed",
                    "run_id": record.get("run_id") or record.get("id"),
                }
            )
        worktree_path = str(record.get("ctx_worktree_path") or "").strip()
        if worktree_path and not Path(worktree_path).exists():
            contradictions.append(
                {
                    "type": "codex_worktree_missing",
                    "run_id": record.get("run_id") or record.get("id"),
                    "ctx_worktree_path": worktree_path,
                }
            )
    return contradictions


def _build_provenance_item(tag: str, path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": tag,
        "path": str(path),
        "status": summary.get("status"),
        "latest_timestamp": summary.get("latest_timestamp"),
        "age_hours": summary.get("age_hours"),
    }


def format_evidence_provenance(items: Iterable[dict[str, Any]]) -> str:
    lines = ["Evidence provenance:"]
    for item in items:
        details: list[str] = []
        for key in ("status", "age_hours", "latest_timestamp", "path", "notes"):
            value = item.get(key)
            if value is not None and value != "":
                label = "latest" if key == "latest_timestamp" else key
                details.append(f"{label}={value}")
        lines.append(f"- [{item.get('tag')}] " + "; ".join(details))
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
) -> dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    journal_payload = _load_json(journal_path)
    codex_payload = _load_json(codex_runs_path)
    ctx_payload = _load_json(ctx_bindings_path)
    ontology_summary = _summarize_ontology(ontology_root, freshness_hours, current)

    journal_latest = _latest_timestamp(_iter_journal_timestamps(journal_payload))
    codex_latest = _latest_timestamp(_iter_codex_timestamps(codex_payload))
    ctx_latest = _latest_timestamp(_iter_ctx_timestamps(ctx_payload))
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

    latest_timestamps = [
        item
        for item in (journal_latest, codex_latest, ctx_latest, ontology_latest)
        if item is not None
    ]
    freshness_spread_hours = None
    if len(latest_timestamps) >= 2:
        spread_seconds = (max(latest_timestamps) - min(latest_timestamps)).total_seconds()
        freshness_spread_hours = round(spread_seconds / 3600, 2)

    reasons: list[str] = []
    contradictions: list[str] = []
    warnings: list[str] = []
    for name, entry in sources.items():
        status = str(entry.get("status") or "")
        if status == "missing":
            reasons.append(f"{name} evidence missing")
            warnings.append(f"{name} evidence missing")
        elif status == "stale":
            reasons.append(f"{name} evidence stale ({entry.get('age_hours')}h)")
            warnings.append(f"{name} evidence stale")
        elif status == "degraded":
            reasons.append(f"{name} evidence degraded")
            warnings.append(f"{name} evidence degraded")

    statuses = {str(entry.get("status") or "") for entry in sources.values()}
    if "fresh" in statuses and statuses.intersection({"stale", "missing", "degraded"}):
        contradictions.append("evidence freshness mismatch across sources")
    if stale_active_codex:
        contradictions.append("stale active Codex runs detected")
        warnings.append("stale active Codex runs detected")
        reasons.append(f"{len(stale_active_codex)} active Codex run(s) exceed {active_stale_hours}h")
    if stale_active_ctx:
        contradictions.append("stale active ctx bindings detected")
        warnings.append("stale active ctx bindings detected")
        reasons.append(f"{len(stale_active_ctx)} active ctx binding(s) exceed {active_stale_hours}h")
    if planning_contradictions:
        contradictions.append("planning contradictions detected")
        warnings.append("planning contradictions detected")
        reasons.append(f"{len(planning_contradictions)} planning contradiction(s) detected")

    ontology_alerts = [
        str(item).strip()
        for item in ontology_summary.get("alerts", [])
        if str(item).strip()
    ]
    for item in ontology_summary.get("reasons", []):
        text = str(item).strip()
        if text and text not in reasons:
            reasons.append(text)
    if str(ontology_summary.get("status") or "") in {"missing", "stale", "degraded"}:
        message = "ontology intelligence artifacts are stale, missing, or degraded"
        if message not in contradictions:
            contradictions.append(message)

    degraded = bool(reasons or contradictions)
    gate_status = "degraded" if degraded else "healthy"

    provenance_items = [
        _build_provenance_item("journal", journal_path, sources["journal_entries"]),
        _build_provenance_item("codex", codex_runs_path, sources["codex_runs"]),
        _build_provenance_item("ctx", ctx_bindings_path, sources["ctx_bindings"]),
        _build_provenance_item("ontology", ontology_root, sources["ontology_intelligence"]),
    ]
    if stale_active_codex:
        provenance_items[1]["notes"] = f"{len(stale_active_codex)} active run(s) exceed {active_stale_hours}h"
    if stale_active_ctx:
        provenance_items[2]["notes"] = f"{len(stale_active_ctx)} active session(s) exceed {active_stale_hours}h"
    if ontology_alerts:
        provenance_items[3]["notes"] = " | ".join(ontology_alerts)

    return {
        "status": gate_status,
        "freshness_hours": freshness_hours,
        "active_stale_hours": active_stale_hours,
        "sources": sources,
        "freshness_spread_hours": freshness_spread_hours,
        "stale_active_codex": stale_active_codex,
        "stale_active_ctx": stale_active_ctx,
        "planning_contradictions": planning_contradictions,
        "warnings": warnings,
        "ontology": ontology_summary,
        "ontology_alerts": ontology_alerts,
        "contradictions": contradictions,
        "reasons": reasons,
        "suppression": {
            "suppress_non_maintenance": degraded,
            "message": (
                "Reliability floor degraded: non-maintenance work suppressed."
                if degraded
                else "Reliability floor healthy: normal lane selection permitted."
            ),
        },
        "provenance": {
            "contract_version": PROVENANCE_CONTRACT_VERSION,
            "items": provenance_items,
            "summary_markdown": format_evidence_provenance(provenance_items),
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
    critical: bool,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": benchmark_id,
        "label": label,
        "score": round(max(0.0, min(1.0, score)), 4),
        "weight": weight,
        "status": _check_status(score),
        "detail": detail,
        "critical": critical,
        "metrics": metrics,
    }


def _load_benchmark_history(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if isinstance(payload, dict):
        history = payload
    elif isinstance(payload, list):
        history = {"runs": payload}
    else:
        history = {"runs": []}
    if not isinstance(history.get("runs"), list):
        history["runs"] = []
    return history


def _save_benchmark_history(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, payload)


def evaluate_self_improvement_benchmark(
    *,
    journal_path: Path = DEFAULT_JOURNAL_PATH,
    codex_runs_path: Path = DEFAULT_CODEX_RUNS_PATH,
    ctx_bindings_path: Path = DEFAULT_CTX_BINDINGS_PATH,
    ontology_root: Path = DEFAULT_ONTOLOGY_ROOT,
    history_path: Path = DEFAULT_BENCHMARK_HISTORY_PATH,
    now: Optional[datetime] = None,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    active_stale_hours: int = DEFAULT_ACTIVE_STALE_HOURS,
    persist: bool = True,
) -> dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    gate = evaluate_self_improvement_evidence(
        journal_path=journal_path,
        codex_runs_path=codex_runs_path,
        ctx_bindings_path=ctx_bindings_path,
        ontology_root=ontology_root,
        now=current,
        freshness_hours=freshness_hours,
        active_stale_hours=active_stale_hours,
    )
    source_statuses = [
        str((entry or {}).get("status") or "")
        for entry in (gate.get("sources") or {}).values()
        if isinstance(entry, dict)
    ]
    stale_source_count = sum(status in {"stale", "missing", "degraded"} for status in source_statuses)

    reliability_score = 1.0
    reliability_score -= 0.2 * stale_source_count
    reliability_score -= 0.15 if gate.get("stale_active_codex") else 0.0
    reliability_score -= 0.15 if gate.get("stale_active_ctx") else 0.0
    reliability_score -= 0.15 if gate.get("planning_contradictions") else 0.0
    reliability_score -= (
        0.15
        if str((gate.get("ontology") or {}).get("status") or "") in {"stale", "missing", "degraded"}
        else 0.0
    )
    if gate.get("status") == "degraded":
        reliability_score = min(reliability_score, 0.45)

    detail = (
        "Reliability floor is healthy."
        if gate.get("status") == "healthy"
        else "; ".join(
            gate.get("reasons")
            or gate.get("warnings")
            or gate.get("contradictions")
            or ["Reliability floor degraded."]
        )
    )
    reliability_gate = _build_benchmark_item(
        "reliability_gate",
        "Reliability gate",
        score=reliability_score,
        weight=25,
        detail=detail,
        critical=True,
        metrics={
            "gate_status": gate.get("status"),
            "stale_source_count": stale_source_count,
            "warning_count": len(gate.get("warnings") or []),
            "contradiction_count": len(gate.get("contradictions") or []),
            "freshness_spread_hours": gate.get("freshness_spread_hours"),
        },
    )
    checks = {"reliability_gate": reliability_gate}
    project_score = round(reliability_gate["score"] * 100, 2)
    critical_failures = [
        name
        for name, check in checks.items()
        if check.get("critical") and check.get("status") == "fail"
    ]
    benchmark = {
        "contract_version": BENCHMARK_CONTRACT_VERSION,
        "generated_at": current.isoformat(),
        "project_score": project_score,
        "score": project_score,
        "direction": "stable",
        "trend": "single_run",
        "gate": gate,
        "checks": checks,
        "critical_failures": critical_failures,
        "history_path": str(history_path),
    }

    if persist:
        history = _load_benchmark_history(history_path)
        history["runs"].append(
            {
                "generated_at": benchmark["generated_at"],
                "project_score": benchmark["project_score"],
                "checks": {
                    name: {
                        "score": check.get("score"),
                        "status": check.get("status"),
                    }
                    for name, check in checks.items()
                },
            }
        )
        history["runs"] = history["runs"][-_BENCHMARK_HISTORY_LIMIT:]
        _save_benchmark_history(history_path, history)

    return benchmark


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


def self_improvement_benchmark(
    journal_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    ctx_bindings_path: Optional[str] = None,
    ontology_root: Optional[str] = None,
    history_path: Optional[str] = None,
    now: Optional[str] = None,
    freshness_hours: Optional[int] = None,
    active_stale_hours: Optional[int] = None,
    persist: Optional[bool] = None,
    task_id: Optional[str] = None,
) -> str:
    benchmark = evaluate_self_improvement_benchmark(
        journal_path=Path(journal_path).expanduser() if journal_path else DEFAULT_JOURNAL_PATH,
        codex_runs_path=Path(codex_runs_path).expanduser() if codex_runs_path else DEFAULT_CODEX_RUNS_PATH,
        ctx_bindings_path=Path(ctx_bindings_path).expanduser() if ctx_bindings_path else DEFAULT_CTX_BINDINGS_PATH,
        ontology_root=Path(ontology_root).expanduser() if ontology_root else DEFAULT_ONTOLOGY_ROOT,
        history_path=Path(history_path).expanduser() if history_path else DEFAULT_BENCHMARK_HISTORY_PATH,
        now=_parse_time(now) if now else None,
        freshness_hours=int(freshness_hours) if freshness_hours else DEFAULT_FRESHNESS_HOURS,
        active_stale_hours=int(active_stale_hours) if active_stale_hours else DEFAULT_ACTIVE_STALE_HOURS,
        persist=True if persist is None else bool(persist),
    )
    return json.dumps({"success": True, "benchmark": benchmark, "task_id": task_id})


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
        history_path=args.get("history_path"),
        now=args.get("now"),
        freshness_hours=args.get("freshness_hours"),
        active_stale_hours=args.get("active_stale_hours"),
        persist=args.get("persist"),
        task_id=kw.get("task_id"),
    ),
)
