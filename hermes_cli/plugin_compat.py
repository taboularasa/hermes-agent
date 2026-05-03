"""Runtime compatibility helpers for plugin-local state."""

from __future__ import annotations

import importlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_HADTO_PLUGIN_NAMES = {"hadto", "hadto-hermes-plugin"}


def apply_runtime_plugin_compatibility(
    plugin_name: str,
    *,
    plugin_path: str | None = None,
    module: Any | None = None,
) -> None:
    """Apply runtime compatibility shims for known plugins.

    The Hadto plugin stores long-lived operator state under ``~/.hermes``. Some
    installations still have the pre-Pydantic ledger contract on disk, which
    newer plugin releases validate strictly. Migrate that payload once on load
    so existing self-improvement control paths keep working.
    """

    normalized = str(plugin_name or "").strip().casefold()
    if normalized not in _HADTO_PLUGIN_NAMES:
        return

    patched_any = False
    for module_name in _candidate_hadto_capability_ledger_modules(module):
        capability_ledger = _import_plugin_module(module_name, plugin_path=plugin_path)
        if capability_ledger is None:
            continue
        try:
            _patch_hadto_capability_ledger(capability_ledger)
            patched_any = True
        except Exception:
            logger.warning(
                "Failed to apply Hadto capability-ledger compatibility for %s",
                module_name,
                exc_info=True,
            )

    if not patched_any:
        logger.debug(
            "Hadto capability-ledger compatibility skipped: plugin module not importable",
        )


def migrate_legacy_hadto_capability_ledger_payload(
    payload: dict[str, Any],
    *,
    contract_version: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Convert the legacy Hadto capability-ledger payload into the current shape."""

    now_iso = now_iso or _utcnow_iso()
    capability_surface_map = {
        item["capability_id"]: item["owning_surface"]
        for item in (
            _migrate_legacy_capability(record)
            for record in _iter_records(payload.get("capabilities"))
        )
        if item is not None
    }

    capabilities = [
        item
        for item in (
            _migrate_legacy_capability(record)
            for record in _iter_records(payload.get("capabilities"))
        )
        if item is not None
    ]
    gaps = [
        item
        for item in (
            _migrate_legacy_gap(record, capability_surface_map=capability_surface_map)
            for record in _iter_records(payload.get("gaps"))
        )
        if item is not None
    ]
    competency_question_contracts = [
        item
        for item in (
            _migrate_legacy_competency_question(record)
            for record in _iter_records(payload.get("competency_questions"))
        )
        if item is not None
    ]
    interventions = [
        item
        for item in (
            _migrate_legacy_intervention(record, capability_surface_map=capability_surface_map)
            for record in _iter_records(payload.get("interventions"))
        )
        if item is not None
    ]
    verification_targets = [
        item
        for item in (
            _migrate_legacy_verification_target(record)
            for record in _iter_records(payload.get("verification_targets"))
        )
        if item is not None
    ]
    outcomes = [
        item
        for item in (
            _migrate_legacy_outcome(record)
            for record in _iter_records(payload.get("outcomes"))
        )
        if item is not None
    ]
    evidence_refs = [
        item
        for item in (
            _migrate_legacy_evidence_ref(record)
            for record in _iter_records(payload.get("evidence_sources"))
        )
        if item is not None
    ]

    hypotheses = [item for item in _iter_records(payload.get("hypotheses"))]
    indexes = payload.get("indexes")

    return {
        "contract_version": str(payload.get("contract_version") or contract_version).strip() or contract_version,
        "updated_at": str(payload.get("updated_at") or now_iso).strip() or now_iso,
        "capabilities": capabilities,
        "gaps": gaps,
        "competency_question_contracts": competency_question_contracts,
        "hypotheses": hypotheses,
        "evidence_refs": evidence_refs,
        "interventions": interventions,
        "verification_targets": verification_targets,
        "outcomes": outcomes,
        "indexes": indexes if isinstance(indexes, dict) else {},
    }


def _patch_hadto_capability_ledger(module: Any) -> None:
    if getattr(module, "_hermes_runtime_compat_applied", False):
        _migrate_hadto_capability_ledger_file(module)
        return

    original_coerce = getattr(module, "_coerce_ledger_input", None)
    contract_version = str(getattr(module, "CAPABILITY_LEDGER_CONTRACT_VERSION", "v1") or "v1")

    if callable(original_coerce):

        def _compat_coerce(payload: Any, *, now: datetime | None = None):  # type: ignore[override]
            try:
                return original_coerce(payload, now=now)
            except Exception:
                if not _looks_like_legacy_hadto_capability_ledger(payload):
                    raise
                migrated = migrate_legacy_hadto_capability_ledger_payload(
                    payload,
                    contract_version=contract_version,
                    now_iso=_datetime_to_iso(now),
                )
                return original_coerce(migrated, now=now)

        module._coerce_ledger_input = _compat_coerce

    module._hermes_runtime_compat_applied = True
    _migrate_hadto_capability_ledger_file(module)


def _migrate_hadto_capability_ledger_file(module: Any) -> bool:
    ledger_path = Path(
        getattr(
            module,
            "DEFAULT_CAPABILITY_LEDGER_PATH",
            get_hermes_home() / "self_improvement" / "capability_ledger.json",
        )
    )
    if not ledger_path.exists():
        return False

    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read Hadto capability ledger from %s", ledger_path, exc_info=True)
        return False

    if not isinstance(payload, dict):
        return False

    try:
        getattr(module, "CapabilityLedger").model_validate(payload)
        return False
    except Exception:
        if not _looks_like_legacy_hadto_capability_ledger(payload):
            return False
        migrated = migrate_legacy_hadto_capability_ledger_payload(
            payload,
            contract_version=str(getattr(module, "CAPABILITY_LEDGER_CONTRACT_VERSION", "v1") or "v1"),
        )
        try:
            getattr(module, "CapabilityLedger").model_validate(migrated)
        except Exception:
            logger.warning("Failed to migrate legacy Hadto capability ledger at %s", ledger_path, exc_info=True)
            return False
        atomic_json_write(ledger_path, migrated)
        logger.info("Migrated legacy Hadto capability ledger at %s", ledger_path)
        return True


def _migrate_legacy_capability(record: dict[str, Any]) -> dict[str, Any] | None:
    capability_id = _first_text(record, "capability_id", "id")
    if not capability_id:
        return None
    source_tools = _string_list(record.get("source_tools"))
    source_tool = _first_text(record, "source_tool", "tool_source")
    if source_tool and source_tool not in source_tools:
        source_tools.append(source_tool)
    return {
        "capability_id": capability_id,
        "name": _first_text(record, "name", "title") or capability_id,
        "lane": _normalize_lane(_first_text(record, "lane", "lane_affinity"), default="Capability"),
        "status": _first_text(record, "status", "current_status") or "active",
        "owning_surface": _first_text(record, "owning_surface", "change_surface", "target_surface", "domain") or "legacy",
        "upstream_strategy": _first_text(record, "upstream_strategy") or "legacy_import",
        "source_tools": source_tools,
        "summary": _first_text(record, "summary", "description", "detail"),
    }


def _migrate_legacy_gap(
    record: dict[str, Any],
    *,
    capability_surface_map: dict[str, str],
) -> dict[str, Any] | None:
    gap_id = _first_text(record, "gap_id", "id")
    if not gap_id:
        return None
    capability_id = _first_text(record, "capability_id") or _first_list_item(record, "capability_ids")
    if not capability_id:
        return None
    return {
        "gap_id": gap_id,
        "capability_id": capability_id,
        "problem_statement": _first_text(record, "problem_statement", "detail", "description", "title") or gap_id,
        "urgency": _first_text(record, "urgency", "priority") or _default_gap_urgency(record.get("status")),
        "change_surface": (
            _first_text(record, "change_surface", "target_surface")
            or capability_surface_map.get(capability_id)
            or "legacy"
        ),
        "blocking_assumptions": _string_list(record.get("blocking_assumptions")),
        "status": _first_text(record, "status") or "open",
    }


def _migrate_legacy_competency_question(record: dict[str, Any]) -> dict[str, Any] | None:
    cq_id = _first_text(record, "cq_id", "question_id", "id")
    if not cq_id:
        return None
    return {
        "cq_id": cq_id,
        "capability_id": _first_text(record, "capability_id") or _first_list_item(record, "capability_ids"),
        "gap_id": _first_text(record, "gap_id") or _first_list_item(record, "gap_ids"),
        "question": _first_text(record, "question", "title") or cq_id,
        "expected_answer_shape": _first_text(record, "expected_answer_shape", "answer_shape") or "Evidence-backed answer",
        "evidence_requirements": _string_list(record.get("evidence_requirements")),
        "acceptance_rule": (
            _first_text(record, "acceptance_rule", "success_criteria")
            or "Answer is grounded in durable evidence and can drive execution."
        ),
    }


def _migrate_legacy_intervention(
    record: dict[str, Any],
    *,
    capability_surface_map: dict[str, str],
) -> dict[str, Any] | None:
    intervention_id = _first_text(record, "intervention_id", "id")
    if not intervention_id:
        return None
    capability_id = _first_text(record, "capability_id") or _first_list_item(record, "capability_ids")
    return {
        "intervention_id": intervention_id,
        "capability_id": capability_id,
        "gap_id": _first_text(record, "gap_id") or _first_list_item(record, "gap_ids"),
        "tool_source": _first_text(record, "tool_source", "source_tool"),
        "hook_source": _first_text(record, "hook_source"),
        "run_ref": _first_text(record, "run_ref", "external_key", "linked_linear_issue_identifier"),
        "change_surface": (
            _first_text(record, "change_surface", "target_surface")
            or (capability_surface_map.get(capability_id) if capability_id else None)
            or "legacy"
        ),
        "started_at": _first_text(record, "started_at", "start_timestamp", "recorded_at", "updated_at"),
        "ended_at": _first_text(record, "ended_at", "end_timestamp", "recorded_at", "updated_at"),
        "status": _first_text(record, "status") or "planned",
    }


def _migrate_legacy_verification_target(record: dict[str, Any]) -> dict[str, Any] | None:
    verification_id = _first_text(record, "verification_id", "verification_target_id", "id")
    if not verification_id:
        return None
    target = _first_text(record, "target", "title", "verification")
    if not target:
        return None
    return {
        "target": target,
        "method": _first_text(record, "method", "verification_method"),
        "command": _first_text(record, "command"),
        "success_criteria": _first_text(record, "success_criteria", "verification_expectation"),
        "verification_id": verification_id,
        "capability_id": _first_text(record, "capability_id") or _first_list_item(record, "capability_ids"),
        "gap_id": _first_text(record, "gap_id") or _first_list_item(record, "gap_ids"),
        "intervention_id": _first_text(record, "intervention_id"),
    }


def _migrate_legacy_outcome(record: dict[str, Any]) -> dict[str, Any] | None:
    outcome_id = _first_text(record, "outcome_id", "id")
    if not outcome_id:
        return None
    return {
        "outcome_id": outcome_id,
        "intervention_id": _first_text(record, "intervention_id"),
        "verification_ids": _string_list(record.get("verification_ids") or record.get("verification_target_ids")),
        "result_status": _normalize_outcome_status(record),
        "evidence_ref_ids": _string_list(
            record.get("evidence_ref_ids") or record.get("evidence_refs") or record.get("evidence_ids")
        ),
        "delta_summary": _first_text(record, "delta_summary", "detail", "notes", "summary") or "",
        "next_decision": _first_text(record, "next_decision", "recommended_next_step") or "",
    }


def _migrate_legacy_evidence_ref(record: dict[str, Any]) -> dict[str, Any] | None:
    evidence_id = _first_text(record, "evidence_id", "id")
    if not evidence_id:
        return None
    return {
        "evidence_id": evidence_id,
        "source_kind": _first_text(record, "source_kind", "kind", "source_type") or "legacy",
        "source_ref": _first_text(record, "source_ref", "source", "source_location", "label") or evidence_id,
        "collected_at": _first_text(record, "collected_at", "collected_timestamp", "updated_at"),
        "summary": _first_text(record, "summary", "label", "detail", "notes", "title") or evidence_id,
        "capability_ids": _string_list(record.get("capability_ids")),
        "gap_ids": _string_list(record.get("gap_ids")),
        "outcome_ids": _string_list(record.get("outcome_ids")),
    }


def _iter_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _candidate_hadto_capability_ledger_modules(module: Any | None) -> list[str]:
    module_names: list[str] = []
    base_name = str(getattr(module, "__name__", "") or "").strip()
    if base_name:
        module_names.append(f"{base_name}.hadto_hermes_plugin.agent.capability_ledger")
    module_names.append("hadto_hermes_plugin.agent.capability_ledger")
    return list(dict.fromkeys(module_names))


def _import_plugin_module(module_name: str, *, plugin_path: str | None = None) -> Any | None:
    plugin_root = str(Path(plugin_path).expanduser()) if plugin_path else None
    inserted_path = False

    if plugin_root and plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)
        inserted_path = True

    try:
        return importlib.import_module(module_name)
    except Exception:
        logger.debug("Failed to import plugin compatibility module %s", module_name, exc_info=True)
        return None
    finally:
        if inserted_path:
            try:
                sys.path.remove(plugin_root)
            except ValueError:
                pass


def _looks_like_legacy_hadto_capability_ledger(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if "version" in payload and "contract_version" not in payload:
        return True
    if "competency_questions" in payload and "competency_question_contracts" not in payload:
        return True
    if "evidence_sources" in payload and "evidence_refs" not in payload:
        return True

    legacy_collection_shapes = (
        ("capabilities", "capability_id"),
        ("gaps", "gap_id"),
        ("competency_questions", "cq_id"),
        ("interventions", "intervention_id"),
        ("verification_targets", "verification_id"),
        ("outcomes", "outcome_id"),
        ("evidence_sources", "evidence_id"),
    )
    for collection_name, current_id_field in legacy_collection_shapes:
        for record in _iter_records(payload.get(collection_name)):
            if record.get("id") and not record.get(current_id_field):
                return True
    return False


def _first_text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_list_item(record: dict[str, Any], key: str) -> str | None:
    return next(iter(_string_list(record.get(key))), None)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := str(item).strip())]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _normalize_lane(value: str | None, *, default: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in {"maintenance", "growth", "capability"} else default.casefold()


def _default_gap_urgency(status: Any) -> str:
    normalized = str(status or "").strip().casefold()
    if normalized in {"resolved", "closed", "done"}:
        return "low"
    if normalized in {"active", "blocked", "warn", "warning"}:
        return "high"
    return "medium"


def _normalize_outcome_status(record: dict[str, Any]) -> str:
    status = str(record.get("result_status") or record.get("benchmark_status") or "").strip().casefold()
    classification = str(record.get("classification") or "").strip().casefold()
    if status in {"passed", "failed", "partial", "unverified"}:
        return status
    if status in {"pass", "success", "succeeded", "improved"}:
        return "passed"
    if status in {"warn", "warning", "mixed", "partial"}:
        return "partial"
    if status in {"fail", "error", "regressed", "failed"}:
        return "failed"
    if classification in {"improved", "passed", "success"}:
        return "passed"
    if classification in {"mixed", "partial"}:
        return "partial"
    if classification in {"regressed", "failed"}:
        return "failed"
    return "unverified"


def _datetime_to_iso(value: datetime | None) -> str:
    if value is None:
        return _utcnow_iso()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
