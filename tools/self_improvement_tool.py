"""Evidence freshness/consistency gate for the Hermes self-improvement loop."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from agent.ontology_context import (
    DEFAULT_ONTOLOGY_REPO_ROOT,
    load_ontology_snapshot,
    summarize_ontology_reliability,
)
from hermes_constants import get_hermes_home
from tools.registry import registry

logger = logging.getLogger(__name__)


DEFAULT_JOURNAL_PATH = Path("/home/david/stacks/hermes-journal/src/data/journal.json")
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"
DEFAULT_CTX_BINDINGS_PATH = get_hermes_home() / "ctx" / "session_bindings.json"
DEFAULT_ONTOLOGY_ROOT = DEFAULT_ONTOLOGY_REPO_ROOT

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
