"""Evidence freshness/consistency gate for the Hermes self-improvement loop."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from hermes_constants import get_hermes_home
from tools.registry import registry

logger = logging.getLogger(__name__)


DEFAULT_JOURNAL_PATH = Path("/home/david/stacks/hermes-journal/src/data/journal.json")
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"
DEFAULT_CTX_BINDINGS_PATH = get_hermes_home() / "ctx" / "session_bindings.json"

DEFAULT_FRESHNESS_HOURS = 72
DEFAULT_ACTIVE_STALE_HOURS = 12


SELF_IMPROVEMENT_EVIDENCE_SCHEMA = {
    "name": "self_improvement_evidence_gate",
    "description": (
        "Evaluate evidence freshness and contradictions for the Hermes self-improvement loop. "
        "Reads journal entries, Codex supervisor runs, and ctx session bindings to determine "
        "whether the reliability floor is degraded and non-maintenance work should be suppressed."
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


def evaluate_self_improvement_evidence(
    *,
    journal_path: Path = DEFAULT_JOURNAL_PATH,
    codex_runs_path: Path = DEFAULT_CODEX_RUNS_PATH,
    ctx_bindings_path: Path = DEFAULT_CTX_BINDINGS_PATH,
    now: Optional[datetime] = None,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    active_stale_hours: int = DEFAULT_ACTIVE_STALE_HOURS,
) -> Dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    journal_payload = _load_json(journal_path)
    codex_payload = _load_json(codex_runs_path)
    ctx_payload = _load_json(ctx_bindings_path)

    journal_latest = max(_iter_journal_timestamps(journal_payload), default=None)
    codex_latest = max(_iter_codex_timestamps(codex_payload), default=None)
    ctx_latest = max(_iter_ctx_timestamps(ctx_payload), default=None)

    sources = {
        "journal_entries": _summarize_source("journal_entries", journal_latest, freshness_hours, current),
        "codex_runs": _summarize_source("codex_runs", codex_latest, freshness_hours, current),
        "ctx_bindings": _summarize_source("ctx_bindings", ctx_latest, freshness_hours, current),
    }

    stale_active_codex = _find_stale_active_codex(codex_payload, current, active_stale_hours)
    stale_active_ctx = _find_stale_active_ctx(ctx_payload, current, active_stale_hours)

    statuses = {entry["status"] for entry in sources.values()}
    latest_timestamps = [ts for ts in (journal_latest, codex_latest, ctx_latest) if ts is not None]
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

    return {
        "status": gate_status,
        "freshness_hours": freshness_hours,
        "active_stale_hours": active_stale_hours,
        "sources": sources,
        "freshness_spread_hours": freshness_spread_hours,
        "stale_active_codex": stale_active_codex,
        "stale_active_ctx": stale_active_ctx,
        "contradictions": contradictions,
        "reasons": reasons,
        "suppression": suppression,
    }


def self_improvement_evidence_gate(
    journal_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    ctx_bindings_path: Optional[str] = None,
    now: Optional[str] = None,
    freshness_hours: Optional[int] = None,
    active_stale_hours: Optional[int] = None,
    task_id: Optional[str] = None,
) -> str:
    gate = evaluate_self_improvement_evidence(
        journal_path=Path(journal_path).expanduser() if journal_path else DEFAULT_JOURNAL_PATH,
        codex_runs_path=Path(codex_runs_path).expanduser() if codex_runs_path else DEFAULT_CODEX_RUNS_PATH,
        ctx_bindings_path=Path(ctx_bindings_path).expanduser() if ctx_bindings_path else DEFAULT_CTX_BINDINGS_PATH,
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
        now=args.get("now"),
        freshness_hours=args.get("freshness_hours"),
        active_stale_hours=args.get("active_stale_hours"),
        task_id=kw.get("task_id"),
    ),
)
