"""
Cron job storage and management.

Jobs are stored in ~/.hermes/cron/jobs.json
Output is saved to ~/.hermes/cron/output/{job_id}/{timestamp}.md
"""

import copy
import json
import logging
import tempfile
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Optional, Dict, List, Any
from hadto_patches.recurring_routines import inspect_routine_delivery_gate

logger = logging.getLogger("cron.jobs")

from hermes_time import now as _hermes_now

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

# =============================================================================
# Configuration
# =============================================================================

HERMES_DIR = get_hermes_home()
CRON_DIR = HERMES_DIR / "cron"
JOBS_FILE = CRON_DIR / "jobs.json"
OUTPUT_DIR = CRON_DIR / "output"
ONESHOT_GRACE_SECONDS = 120
RATCHET_WINDOW_RUNS = 4
RATCHET_MAX_OUTPUT_BYTES = 64_000
RATCHET_CONTROL_ROLES = {
    "coordinate",
    "implement",
    "publish",
    "report",
    "study",
}
RATCHET_SURFACES = [
    "operator_value",
    "anti_make_work",
    "leading_indicator",
]
GEOMETRY_SHAPING_FIELDS = [
    "default_changed",
    "channel_opened",
    "friction_changed",
    "stale_path_pruned",
    "policy_vs_path",
]
FIRST_PROOF_POINT_FIELDS = [
    "seed_surface",
    "protection_assumptions",
    "success_signal",
    "imitation_path",
    "why_first",
]
VALUE_SURFACE_FIELDS = [
    "durable_store",
    "circulation",
    "closure_rule",
]
ATTENTION_BUDGET_FIELDS = [
    "attention_cost",
    "decision_value",
    "focus_effect",
]
AGGREGATE_STEWARDSHIP_FIELDS = [
    "shared_provider_concentration",
    "dependency_choke_points",
    "verification_debt",
    "synchronized_failure_risk",
    "portfolio_state",
    "shared_artifact",
]
DISCOVERY_ROLES = {"report", "study"}
EXECUTION_ROLES = {"implement", "publish"}
BRIDGE_ROLES = {"coordinate", "self-improve"}


def _normalize_skill_list(skill: Optional[str] = None, skills: Optional[Any] = None) -> List[str]:
    """Normalize legacy/single-skill and multi-skill inputs into a unique ordered list."""
    if skills is None:
        raw_items = [skill] if skill else []
    elif isinstance(skills, str):
        raw_items = [skills]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_taxonomy_value(value: Optional[Any]) -> Optional[str]:
    """Normalize free-form job taxonomy values like role/scope."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"\s+", "-", text)


def _apply_skill_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    """Return a job dict with canonical skills and optional topology fields aligned."""
    normalized = dict(job)
    skills = _normalize_skill_list(normalized.get("skill"), normalized.get("skills"))
    normalized["skills"] = skills
    normalized["skill"] = skills[0] if skills else None
    normalized["role"] = _normalize_taxonomy_value(normalized.get("role"))
    normalized["scope"] = _normalize_taxonomy_value(normalized.get("scope"))
    return normalized


def should_check_persistence_ratchet(job: Dict[str, Any]) -> bool:
    """Return True when a cron job is a recurring control loop worth ratchet-checking."""
    schedule = job.get("schedule") or {}
    if schedule.get("kind") not in {"cron", "interval"}:
        return False

    role = _normalize_taxonomy_value(job.get("role"))
    scope = _normalize_taxonomy_value(job.get("scope"))
    return bool(scope or role in RATCHET_CONTROL_ROLES)


def _recent_output_files(job_id: str, *, limit: int = RATCHET_WINDOW_RUNS) -> List[Path]:
    """Return the most recent saved output files for a job, bounded for cheap checks."""
    output_dir = OUTPUT_DIR / job_id
    if not output_dir.exists():
        return []
    try:
        files = [path for path in output_dir.glob("*.md") if path.is_file()]
        files.sort(key=lambda path: (path.stat().st_mtime, path.name))
    except OSError:
        return []
    return files[-limit:]


def _read_bounded_output(path: Path) -> str:
    """Read a bounded tail of an output document; the response is at the end."""
    try:
        with open(path, "rb") as handle:
            try:
                handle.seek(-RATCHET_MAX_OUTPUT_BYTES, os.SEEK_END)
            except OSError:
                handle.seek(0)
            return handle.read(RATCHET_MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _job_artifact_path(job: Dict[str, Any]) -> str:
    job_id = str(job.get("id") or "<job-id>")
    return str(OUTPUT_DIR / job_id)


def _job_commitment(job: Dict[str, Any]) -> str:
    prompt = str(job.get("prompt") or "").strip()
    for line in prompt.splitlines():
        text = re.sub(r"\s+", " ", line).strip(" -*`_")
        if text:
            return text[:160]
    name = str(job.get("name") or job.get("id") or "cron job").strip()
    return name[:160]


def _discovery_execution_mode(job: Dict[str, Any]) -> str:
    role = _normalize_taxonomy_value(job.get("role"))
    if role in DISCOVERY_ROLES:
        return "discovery"
    if role in EXECUTION_ROLES:
        return "execution"
    if role in BRIDGE_ROLES:
        return "bridge"
    return "unclassified"


def _operator_contract_checks(job: Dict[str, Any]) -> Dict[str, str]:
    mode = _discovery_execution_mode(job)
    checks = {
        "discovery": {
            "dignity_check": "Preserve operator agency by naming evidence gaps plainly, keeping misses visible in shared artifacts, and escalating before a study or review loop asks the operator to surrender judgment for basic access.",
            "capability_check": "Compound operator capability by turning fresh evidence into durable notes, status, or backlog inputs that make the next decision easier instead of replacing operator reasoning with an opaque summary.",
            "viability_check": "Keep the loop stable and inspectable with durable artifacts, bounded evidence refresh, and an explicit escalation checkpoint before evidence requirements or review rules are rewritten.",
        },
        "execution": {
            "dignity_check": "Preserve operator agency by keeping concrete blockers, rollback points, and approval boundaries visible before delegated execution asks the operator to trust an opaque change.",
            "capability_check": "Compound operator capability by leaving branch, PR, verification, and status evidence that helps the operator steer the next execution step instead of hiding the work behind automation.",
            "viability_check": "Keep execution stable and inspectable with bounded acceptance criteria, visible failure state, and a stop point before rollout or merge policy is rewritten for future runs.",
        },
        "bridge": {
            "dignity_check": "Preserve operator agency by keeping backlog selection, ownership, and blocker evidence explicit so the operator can see why a delegated run or coordination pass moved this item now.",
            "capability_check": "Compound operator capability by turning each coordination pass into durable issue state, comments, or repo evidence that sharpens the next human or delegated decision.",
            "viability_check": "Keep the coordination surface stable and inspectable with machine-readable status, bounded preemption, and an escalation checkpoint before backlog policy or recurring-loop rules are changed.",
        },
        "unclassified": {
            "dignity_check": "Preserve operator agency by making the current commitment and any miss plainly visible before automation expands its scope.",
            "capability_check": "Compound operator capability by leaving durable evidence that improves the next decision instead of only producing a transient answer.",
            "viability_check": "Keep the surface stable and inspectable with explicit verification and a stop point before global rules are revised.",
        },
    }
    return checks.get(mode, checks["unclassified"])


def _fast_slow_loop_contract(job: Dict[str, Any]) -> Dict[str, Any]:
    mode = _discovery_execution_mode(job)
    shared_slow = [
        "change trust contracts, verification targets, or benchmark criteria",
        "rewrite cron/job governance, topology, or durable policy docs",
        "change delegate, assignee, or approval rules beyond the current work item",
    ]
    contracts = {
        "discovery": {
            "fast_loop_surfaces": [
                "collect fresh evidence and refresh durable artifacts",
                "update machine-readable status and visible failure state",
                "flag evidence gaps without rewriting the governing contract",
            ],
            "slow_loop_surfaces": shared_slow + [
                "change study/report scope, cadence, or evidence requirements",
            ],
            "escalation_checkpoint": (
                "Escalate when the run needs to change evidence requirements, job cadence, or the trust contract instead of only refreshing this cycle's artifact."
            ),
        },
        "execution": {
            "fast_loop_surfaces": [
                "advance the current issue, PR, deployment, or verification step",
                "record concrete blockers and pause unsafe execution",
                "update issue state or status comments for the current work item",
            ],
            "slow_loop_surfaces": shared_slow + [
                "change rollout gates, deployment policy, or execution-wide acceptance criteria",
            ],
            "escalation_checkpoint": (
                "Escalate when the run must redefine merge, rollout, or verification policy rather than finishing or safely blocking the current execution step."
            ),
        },
        "bridge": {
            "fast_loop_surfaces": [
                "reacquire backlog and move the currently selected work item",
                "resume, merge, or comment on existing owned work with live evidence",
                "formalize a concrete blocker when no safe execution step remains",
            ],
            "slow_loop_surfaces": shared_slow + [
                "change backlog selection policy, preemption rules, or recurring loop contracts",
            ],
            "escalation_checkpoint": (
                "Escalate when the run must change backlog policy, recurring-loop governance, or trust-contract rules instead of only moving the selected item."
            ),
        },
        "unclassified": {
            "fast_loop_surfaces": [
                "update the current artifact or status without changing global rules",
            ],
            "slow_loop_surfaces": shared_slow,
            "escalation_checkpoint": (
                "Escalate when the run needs new governance or policy, not just a fresh execution pass."
            ),
        },
    }
    return contracts.get(mode, contracts["unclassified"])


def inspect_trust_contract(
    job: Dict[str, Any],
    persistence_ratchet: Optional[Dict[str, Any]] = None,
    first_proof_point: Optional[Dict[str, Any]] = None,
    geometry_shaping: Optional[Dict[str, Any]] = None,
    value_surfaces: Optional[Dict[str, Any]] = None,
    attention_budget: Optional[Dict[str, Any]] = None,
    aggregate_stewardship: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a compact trust contract for a recurring or one-shot cron loop."""
    normalized_job = _apply_skill_fields(job)
    recurring = should_check_persistence_ratchet(normalized_job)
    ratchet_status = (persistence_ratchet or {}).get("status")
    last_status = str(normalized_job.get("last_status") or normalized_job.get("state") or "unknown")
    degraded = bool(
        last_status in {"error", "failed"}
        or ratchet_status in {"missing", "weak", "drift"}
        or normalized_job.get("last_error")
        or normalized_job.get("last_delivery_error")
    )
    trust_posture = "repeated_trust_bearing" if recurring and not degraded else "one_shot_disconnected"
    if recurring and degraded:
        trust_posture = "repeated_trust_bearing_degraded"

    verification_target = (
        f"saved output in {_job_artifact_path(normalized_job)} and persistence ratchet status"
        if recurring
        else f"saved output in {_job_artifact_path(normalized_job)}"
    )
    visible_outcome_state = last_status
    if degraded and last_status not in {"error", "failed"}:
        if ratchet_status in {"missing", "weak", "drift"}:
            visible_outcome_state = f"{last_status}+{ratchet_status}"
        elif normalized_job.get("last_error") or normalized_job.get("last_delivery_error"):
            visible_outcome_state = f"{last_status}+error"

    fast_slow = _fast_slow_loop_contract(normalized_job)
    operator_checks = _operator_contract_checks(normalized_job)

    contract = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "declared_commitment": _job_commitment(normalized_job),
        "shared_artifact_path": _job_artifact_path(normalized_job),
        "verification_target": verification_target,
        "visible_outcome_state": visible_outcome_state,
        "interaction_mode": "repeated" if recurring else "one_shot",
        "discovery_execution_mode": _discovery_execution_mode(normalized_job),
        "trust_posture": trust_posture,
        "failed_commitment_visible": degraded,
        "dignity_check": operator_checks["dignity_check"],
        "capability_check": operator_checks["capability_check"],
        "viability_check": operator_checks["viability_check"],
        "fast_loop_surfaces": fast_slow["fast_loop_surfaces"],
        "slow_loop_surfaces": fast_slow["slow_loop_surfaces"],
        "escalation_checkpoint": fast_slow["escalation_checkpoint"],
        "first_proof_point": first_proof_point or {
            "status": "required",
            "required_fields": list(FIRST_PROOF_POINT_FIELDS),
            "message": (
                "Meaningful governance or capability shifts must name one bounded "
                "first seed before broad doctrine can count as actionable."
            ),
        },
        "geometry_shaping": geometry_shaping or {
            "status": "required",
            "required_fields": list(GEOMETRY_SHAPING_FIELDS),
            "message": (
                "Governance work must name the concrete path shift: which default changed, "
                "which channel opened, what friction changed, what stale path was pruned, "
                "and how this goes beyond command-style policy language."
            ),
        },
        "value_surfaces": value_surfaces or {
            "status": "required",
            "required_fields": list(VALUE_SURFACE_FIELDS),
            "message": (
                "Recurring control loops must name the durable store of value, the cheap "
                "circulation outputs, and why circulation-only output cannot count as closure."
            ),
        },
        "attention_budget": attention_budget or {
            "status": "required",
            "required_fields": list(ATTENTION_BUDGET_FIELDS),
            "message": (
                "Recurring control loops must price operator attention against decision value and say whether the run shaped useful focus or low-yield alerting."
            ),
        },
        "aggregate_stewardship": aggregate_stewardship or {
            "status": "required",
            "required_fields": list(AGGREGATE_STEWARDSHIP_FIELDS),
            "message": (
                "Recurring control loops must name the shared-provider concentration, dependency choke points, "
                "verification debt, synchronized failure risk, portfolio state, and the shared artifact that carries "
                "the ecosystem view across jobs."
            ),
        },
    }
    if ratchet_status:
        contract["persistence_ratchet_status"] = ratchet_status
    if persistence_ratchet and persistence_ratchet.get("compact_evidence"):
        contract["compact_evidence"] = persistence_ratchet["compact_evidence"]
    error_text = normalized_job.get("last_error") or normalized_job.get("last_delivery_error")
    if error_text:
        contract["visible_failure"] = str(error_text)
    return contract


def _response_text(output_text: str) -> str:
    marker = "\n## Response\n"
    if marker in output_text:
        return output_text.rsplit(marker, 1)[-1]
    return output_text


def _usable_ratchet_value(value: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", value or "").strip(" -*`_")
    if not text:
        return None
    lowered = text.lower().strip(".")
    if lowered in {"none", "n/a", "na", "not applicable", "no", "no change", "nothing"}:
        return None
    return text


_RATCHET_INLINE_KEYS = {
    "evidence": "evidence",
    "decision": "decisions",
    "decisions": "decisions",
    "artifact": "artifacts",
    "artifacts": "artifacts",
    "carry-forward": "carry_forward",
    "carry_forward": "carry_forward",
    "carry forward": "carry_forward",
    "next": "carry_forward",
    "drift": "drift",
}


def _parse_ratchet_categories(text: str) -> Dict[str, List[str]]:
    categories: Dict[str, List[str]] = {
        "evidence": [],
        "decisions": [],
        "artifacts": [],
        "carry_forward": [],
        "drift": [],
    }

    def add(label: str, value: str) -> None:
        category = _RATCHET_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not category:
            category = _RATCHET_INLINE_KEYS.get(label.lower())
        if not category:
            return
        usable = _usable_ratchet_value(value)
        if usable and usable not in categories[category]:
            categories[category].append(usable)

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(evidence|decisions?|artifacts?|carry[-_ ]?forward|next|drift)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    ratchet_lines = [
        line for line in text.splitlines()
        if "ratchet" in line.lower() and any(key in line.lower() for key in ("evidence", "decision", "artifact", "carry", "next", "drift"))
    ]
    inline_re = re.compile(
        r"\b(evidence|decisions?|artifacts?|carry[-_ ]?forward|next|drift)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in ratchet_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return categories


def _ratchet_signal_labels(text: str) -> List[str]:
    lowered = text.lower()
    signals: List[str] = []
    rediscovery_patterns = [
        r"\brediscover(?:ed|ing|y)?\b",
        r"\bre-discover(?:ed|ing|y)?\b",
        r"\bfound again\b",
        r"\bsame (?:gap|issue|failure|finding).*\bagain\b",
        r"\brepeated rediscovery\b",
    ]
    cleanup_patterns = [
        r"\bcleanup drift\b",
        r"\brepeated cleanup\b",
        r"\bdirty checkout\b",
        r"\bstale (?:branch|checkout|worktree)\b",
        r"\buntracked files\b",
    ]
    if any(re.search(pattern, lowered) for pattern in rediscovery_patterns):
        signals.append("repeated_rediscovery")
    if any(re.search(pattern, lowered) for pattern in cleanup_patterns):
        signals.append("cleanup_drift")
    return signals


def _normalize_ratchet_item(item: str) -> str:
    text = re.sub(r"[^a-z0-9#./:_-]+", " ", item.lower())
    return re.sub(r"\s+", " ", text).strip()


def _core_ratchet_items(run: Dict[str, Any]) -> Dict[str, set[str]]:
    categories = run.get("categories", {})
    return {
        name: {_normalize_ratchet_item(item) for item in categories.get(name, [])}
        for name in ("evidence", "decisions", "artifacts")
    }


def _preserved_ratchet_items(latest: Dict[str, Any], previous_runs: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    latest_items = _core_ratchet_items(latest)
    previous_items: Dict[str, set[str]] = {"evidence": set(), "decisions": set(), "artifacts": set()}
    for run in previous_runs:
        for category, items in _core_ratchet_items(run).items():
            previous_items[category].update(items)

    preserved: Dict[str, List[str]] = {}
    for category, items in latest_items.items():
        overlap = sorted(item for item in items if item and item in previous_items[category])
        if overlap:
            preserved[category] = overlap[:3]
    return preserved


def _inspect_ratchet_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    categories = _parse_ratchet_categories(text)
    return {
        "file": path.name,
        "has_section": "persistence ratchet" in text.lower() or bool(categories["carry_forward"]),
        "categories": categories,
        "core_category_count": sum(1 for name in ("evidence", "decisions", "artifacts") if categories[name]),
        "has_carry_forward": bool(categories["carry_forward"]),
        "signals": _ratchet_signal_labels(text),
    }


def load_recent_job_responses(job_id: str, *, limit: int = RATCHET_WINDOW_RUNS) -> List[str]:
    """Return recent saved response bodies for a cron job, oldest to newest."""
    return [
        _response_text(_read_bounded_output(path))
        for path in _recent_output_files(job_id, limit=limit)
    ]


_FIRST_PROOF_POINT_INLINE_KEYS = {
    "seed surface": "seed_surface",
    "seed_surface": "seed_surface",
    "surface": "seed_surface",
    "protected seed": "seed_surface",
    "nucleation site": "seed_surface",
    "protection assumptions": "protection_assumptions",
    "protection_assumptions": "protection_assumptions",
    "protection": "protection_assumptions",
    "assumptions": "protection_assumptions",
    "success signal": "success_signal",
    "success_signal": "success_signal",
    "signal": "success_signal",
    "imitation path": "imitation_path",
    "imitation_path": "imitation_path",
    "expansion path": "imitation_path",
    "replication path": "imitation_path",
    "why first": "why_first",
    "why_first": "why_first",
    "first-site rationale": "why_first",
    "rationale": "why_first",
}

_GEOMETRY_SHAPING_INLINE_KEYS = {
    "default changed": "default_changed",
    "default_changed": "default_changed",
    "default": "default_changed",
    "channel opened": "channel_opened",
    "channel_opened": "channel_opened",
    "channel": "channel_opened",
    "friction changed": "friction_changed",
    "friction_changed": "friction_changed",
    "friction added or removed": "friction_changed",
    "friction": "friction_changed",
    "stale path pruned": "stale_path_pruned",
    "stale_path_pruned": "stale_path_pruned",
    "pruned path": "stale_path_pruned",
    "pruned": "stale_path_pruned",
    "policy-vs-path": "policy_vs_path",
    "policy vs path": "policy_vs_path",
    "policy_vs_path": "policy_vs_path",
    "path shift evidence": "policy_vs_path",
}

_VALUE_SURFACE_INLINE_KEYS = {
    "durable store": "durable_store",
    "durable_store": "durable_store",
    "durable artifact": "durable_store",
    "store of value": "durable_store",
    "circulation": "circulation",
    "circulation outputs": "circulation",
    "circulation-only outputs": "circulation",
    "signals": "circulation",
    "closure rule": "closure_rule",
    "closure_rule": "closure_rule",
    "closure": "closure_rule",
}

_ATTENTION_BUDGET_INLINE_KEYS = {
    "attention cost": "attention_cost",
    "attention_cost": "attention_cost",
    "attention": "attention_cost",
    "attention budget": "attention_cost",
    "decision value": "decision_value",
    "decision_value": "decision_value",
    "judgment value": "decision_value",
    "value": "decision_value",
    "focus effect": "focus_effect",
    "focus_effect": "focus_effect",
    "focus shaping": "focus_effect",
    "focus": "focus_effect",
}

_AGGREGATE_STEWARDSHIP_INLINE_KEYS = {
    "shared provider concentration": "shared_provider_concentration",
    "shared_provider_concentration": "shared_provider_concentration",
    "provider concentration": "shared_provider_concentration",
    "providers": "shared_provider_concentration",
    "dependency choke points": "dependency_choke_points",
    "dependency_choke_points": "dependency_choke_points",
    "choke points": "dependency_choke_points",
    "dependencies": "dependency_choke_points",
    "verification debt": "verification_debt",
    "verification_debt": "verification_debt",
    "debt": "verification_debt",
    "synchronized failure risk": "synchronized_failure_risk",
    "synchronized_failure_risk": "synchronized_failure_risk",
    "sync risk": "synchronized_failure_risk",
    "portfolio state": "portfolio_state",
    "portfolio_state": "portfolio_state",
    "portfolio": "portfolio_state",
    "shared artifact": "shared_artifact",
    "shared_artifact": "shared_artifact",
    "artifact": "shared_artifact",
}


def _parse_first_proof_point_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _FIRST_PROOF_POINT_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not key:
            key = _FIRST_PROOF_POINT_INLINE_KEYS.get(label.lower())
        if not key:
            return
        usable = _usable_ratchet_value(value)
        if usable:
            fields[key] = usable

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(seed[_ ]surface|protected seed|nucleation site|surface|protection[_ ]assumptions|protection|assumptions|success[_ ]signal|signal|imitation[_ ]path|expansion path|replication path|why[_ ]first|first-site rationale|rationale)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    proof_lines = [
        line for line in text.splitlines()
        if ("proof point" in line.lower() or "first seed" in line.lower())
        and any(key in line.lower() for key in ("seed", "surface", "protection", "success", "signal", "imitation", "why"))
    ]
    inline_re = re.compile(
        r"\b(seed[_ ]surface|protected seed|nucleation site|surface|protection[_ ]assumptions|protection|assumptions|success[_ ]signal|signal|imitation[_ ]path|expansion path|replication path|why[_ ]first|first-site rationale|rationale)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in proof_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return fields


def _parse_geometry_shaping_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _GEOMETRY_SHAPING_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not key:
            key = _GEOMETRY_SHAPING_INLINE_KEYS.get(label.lower())
        if not key:
            return
        usable = _usable_ratchet_value(value)
        if usable:
            fields[key] = usable

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(default[_ ]changed|default|channel[_ ]opened|channel|friction[_ ]changed|friction added or removed|friction|stale path pruned|stale_path_pruned|pruned path|pruned|policy[-_ ]vs[-_ ]path|path shift evidence)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    geometry_lines = [
        line for line in text.splitlines()
        if ("geometry shaping" in line.lower() or "path shaping" in line.lower())
        and any(
            key in line.lower()
            for key in ("default", "channel", "friction", "pruned", "policy", "path")
        )
    ]
    inline_re = re.compile(
        r"\b(default[_ ]changed|default|channel[_ ]opened|channel|friction[_ ]changed|friction added or removed|friction|stale path pruned|stale_path_pruned|pruned path|pruned|policy[-_ ]vs[-_ ]path|path shift evidence)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in geometry_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return fields


def _geometry_shaping_generic_signals(fields: Dict[str, str], text: str) -> List[str]:
    joined = " ".join(fields.values()).lower()
    lowered = text.lower()
    signals: List[str] = []
    policy_patterns = [
        r"\bshould\b",
        r"\bmust\b",
        r"\bpolicy\b",
        r"\bprinciple\b",
        r"\bdoctrine\b",
        r"\bgovernance should\b",
    ]
    path_patterns = [
        r"\bdefault\b",
        r"\bchannel\b",
        r"\bfriction\b",
        r"\bprun(?:e|ed|ing)\b",
        r"\bslack\b",
        r"\blinear\b",
        r"\bcron\b",
        r"\bpr\b",
        r"\bcomment\b",
        r"\bbranch\b",
        r"\bissue\b",
    ]
    if any(re.search(pattern, lowered) for pattern in policy_patterns) and not any(
        re.search(pattern, joined or lowered) for pattern in path_patterns
    ):
        signals.append("policy_only_language")
    if fields.get("policy_vs_path") and re.search(r"\b(policy|principle|doctrine)\b", fields["policy_vs_path"].lower()) and not re.search(
        r"\b(default|channel|friction|prun|comment|branch|issue|cron|slack|linear|path)\b",
        fields["policy_vs_path"].lower(),
    ):
        signals.append("policy_vs_path_not_grounded")
    return signals


def _inspect_geometry_shaping_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    fields = _parse_geometry_shaping_fields(text)
    missing = [field for field in GEOMETRY_SHAPING_FIELDS if field not in fields]
    return {
        "file": path.name,
        "has_section": "geometry shaping" in text.lower() or "path shaping" in text.lower(),
        "fields": fields,
        "missing_fields": missing,
        "generic_signals": _geometry_shaping_generic_signals(fields, text),
    }


def inspect_geometry_shaping(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect the latest control-loop output for concrete geometry-shaping fields."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "required_fields": list(GEOMETRY_SHAPING_FIELDS),
        "status": "not_applicable",
        "message": "Geometry-shaping checks apply only to recurring classified control loops.",
        "fields": {},
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")), limit=1)
    if not files:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking geometry-shaping fields.",
            }
        )
        return result

    latest = _inspect_geometry_shaping_output(files[-1])
    result["latest_file"] = latest["file"]
    result["fields"] = latest["fields"]
    result["missing_fields"] = latest["missing_fields"]
    result["generic_signals"] = latest["generic_signals"]

    if not latest["has_section"] or not latest["fields"]:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks a Geometry Shaping block naming the actual path shift.",
            }
        )
        return result

    if latest["missing_fields"]:
        result.update(
            {
                "status": "incomplete",
                "message": "Latest Geometry Shaping block is missing: " + ", ".join(latest["missing_fields"]),
            }
        )
        return result

    if latest["generic_signals"]:
        result.update(
            {
                "status": "policy_only",
                "message": (
                    "Latest Geometry Shaping block still reads like command-style policy instead of a concrete path shift: "
                    + ", ".join(latest["generic_signals"])
                ),
            }
        )
        return result

    result.update(
        {
            "status": "populated",
            "message": "Latest report names the concrete geometry shift in defaults, channels, friction, pruning, and policy-vs-path evidence.",
        }
    )
    return result


def _parse_value_surface_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _VALUE_SURFACE_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not key:
            key = _VALUE_SURFACE_INLINE_KEYS.get(label.lower())
        if not key:
            return
        usable = _usable_ratchet_value(value)
        if usable:
            fields[key] = usable

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(durable store|durable_store|durable artifact|store of value|circulation(?:-only)? outputs?|circulation|signals|closure rule|closure_rule|closure)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    surface_lines = [
        line for line in text.splitlines()
        if ("value surfaces" in line.lower() or "durable store" in line.lower())
        and any(key in line.lower() for key in ("durable", "circulation", "closure", "signal"))
    ]
    inline_re = re.compile(
        r"\b(durable store|durable_store|durable artifact|store of value|circulation(?:-only)? outputs?|circulation|signals|closure rule|closure_rule|closure)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in surface_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return fields


def _parse_attention_budget_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _ATTENTION_BUDGET_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not key:
            key = _ATTENTION_BUDGET_INLINE_KEYS.get(label.lower())
        if not key:
            return
        usable = _usable_ratchet_value(value)
        if usable:
            fields[key] = usable

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(attention cost|attention_cost|decision value|decision_value|judgment value|focus effect|focus_effect|focus shaping)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    budget_lines = [
        line for line in text.splitlines()
        if ("attention budget" in line.lower() or "attention cost" in line.lower())
        and any(key in line.lower() for key in ("attention", "decision", "focus", "value"))
    ]
    inline_re = re.compile(
        r"\b(attention cost|attention_cost|decision value|decision_value|judgment value|focus effect|focus_effect|focus shaping)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in budget_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return fields


def _is_circulation_like(value: str) -> bool:
    lowered = value.lower()
    circulation_patterns = [
        r"\bslack\b",
        r"\bheartbeat\b",
        r"\bstatus ping\b",
        r"\bstatus summary\b",
        r"\btransient\b",
        r"\btrace\b",
        r"\bmessage\b",
        r"\bnotification\b",
        r"\bchat\b",
    ]
    durable_markers = [
        r"\bissue\b",
        r"\bcomment\b",
        r"\bpr\b",
        r"\bbenchmark\b",
        r"\bnote\b",
        r"\boutput\b",
        r"\bfile\b",
        r"\bjson\b",
        r"\bmd\b",
        r"\bstate\b",
        r"\bartifact\b",
        r"\bpath\b",
    ]
    return any(re.search(pattern, lowered) for pattern in circulation_patterns) and not any(
        re.search(pattern, lowered) for pattern in durable_markers
    )


def _is_attention_heavy(value: str) -> bool:
    lowered = value.lower()
    patterns = [
        r"\bhigh\b",
        r"\bheavy\b",
        r"\bexpensive\b",
        r"\binterrupt",
        r"\bspam\b",
        r"\balert flood\b",
        r"\bnoisy\b",
        r"\blong\b",
        r"\bmulti-block\b",
        r"\btoo much\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _is_low_decision_value(value: str) -> bool:
    lowered = value.lower()
    patterns = [
        r"\bnone\b",
        r"\blow\b",
        r"\bunchanged\b",
        r"\bno durable state\b",
        r"\bno decision\b",
        r"\bno judgment\b",
        r"\bno change\b",
        r"\bstatus only\b",
        r"\bheartbeat only\b",
        r"\bsummary only\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _is_low_yield_focus_effect(value: str) -> bool:
    lowered = value.lower()
    patterns = [
        r"\blow-yield\b",
        r"\bspam\b",
        r"\balert flood\b",
        r"\bnoise\b",
        r"\bdrift\b",
        r"\bscatter\b",
        r"\binterrupt\b",
        r"\bno focus\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _inspect_value_surfaces_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    fields = _parse_value_surface_fields(text)
    missing = [field for field in VALUE_SURFACE_FIELDS if field not in fields]
    durable_store = fields.get("durable_store", "")
    circulation_only = bool(durable_store) and _is_circulation_like(durable_store)
    return {
        "file": path.name,
        "has_section": "value surfaces" in text.lower() or bool(fields),
        "fields": fields,
        "missing_fields": missing,
        "circulation_only": circulation_only,
    }


def _inspect_attention_budget_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    fields = _parse_attention_budget_fields(text)
    missing = [field for field in ATTENTION_BUDGET_FIELDS if field not in fields]
    attention_cost = fields.get("attention_cost", "")
    decision_value = fields.get("decision_value", "")
    focus_effect = fields.get("focus_effect", "")
    low_yield = ((_is_attention_heavy(attention_cost) and _is_low_decision_value(decision_value)) or _is_low_yield_focus_effect(focus_effect))
    return {
        "file": path.name,
        "has_section": "attention budget" in text.lower() or bool(fields),
        "fields": fields,
        "missing_fields": missing,
        "low_yield": low_yield,
    }


def _parse_aggregate_stewardship_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _AGGREGATE_STEWARDSHIP_INLINE_KEYS.get(label.lower().replace("_", " "))
        if not key:
            key = _AGGREGATE_STEWARDSHIP_INLINE_KEYS.get(label.lower())
        if not key:
            return
        usable = _usable_ratchet_value(value)
        if usable:
            fields[key] = usable

    line_re = re.compile(
        r"^\s*(?:[-*]\s*)?"
        r"(shared provider concentration|shared_provider_concentration|provider concentration|dependency choke points|dependency_choke_points|verification debt|verification_debt|synchronized failure risk|synchronized_failure_risk|portfolio state|portfolio_state|shared artifact|shared_artifact|providers|choke points|dependencies|debt|sync risk|portfolio|artifact)"
        r"\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    stewardship_lines = [
        line for line in text.splitlines()
        if "aggregate stewardship" in line.lower() or "portfolio state" in line.lower()
    ]
    inline_re = re.compile(
        r"\b(shared provider concentration|shared_provider_concentration|provider concentration|dependency choke points|dependency_choke_points|verification debt|verification_debt|synchronized failure risk|synchronized_failure_risk|portfolio state|portfolio_state|shared artifact|shared_artifact|providers|choke points|dependencies|debt|sync risk|portfolio|artifact)"
        r"\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in stewardship_lines:
        for match in inline_re.finditer(line):
            add(match.group(1), match.group(2))

    return fields


def _aggregate_stewardship_stale_fields(fields: Dict[str, str]) -> List[str]:
    stale_markers = [
        r"\bsame as last run\b",
        r"\bunchanged\b",
        r"\bcarry forward\b",
        r"\bstill healthy\b",
        r"\bstill fine\b",
        r"\bno change\b",
    ]
    artifact_markers = [r"/", r"\.md\b", r"\.json\b", r"topology\b", r"inspect_job_topology\b", r"issue\b", r"comment\b", r"pr\b"]
    stale: List[str] = []
    for key, value in fields.items():
        lowered = value.lower()
        if any(re.search(pattern, lowered) for pattern in stale_markers):
            stale.append(key)
    shared_artifact = fields.get("shared_artifact", "")
    if shared_artifact and not any(re.search(pattern, shared_artifact.lower()) for pattern in artifact_markers):
        stale.append("shared_artifact")
    return sorted(set(stale))


def _inspect_aggregate_stewardship_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    fields = _parse_aggregate_stewardship_fields(text)
    missing = [field for field in AGGREGATE_STEWARDSHIP_FIELDS if field not in fields]
    stale_fields = _aggregate_stewardship_stale_fields(fields)
    return {
        "file": path.name,
        "has_section": "aggregate stewardship" in text.lower() or bool(fields),
        "fields": fields,
        "missing_fields": missing,
        "stale_fields": stale_fields,
    }


def inspect_aggregate_stewardship(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect the latest recurring-loop output for aggregate stewardship fields."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "required_fields": list(AGGREGATE_STEWARDSHIP_FIELDS),
        "status": "not_applicable",
        "message": "Aggregate stewardship checks apply only to recurring classified control loops.",
        "fields": {},
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")), limit=1)
    if not files:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking aggregate stewardship fields.",
            }
        )
        return result

    latest = _inspect_aggregate_stewardship_output(files[-1])
    result["latest_file"] = latest["file"]
    result["fields"] = latest["fields"]
    result["missing_fields"] = latest["missing_fields"]
    result["stale_fields"] = latest["stale_fields"]

    if not latest["has_section"] or not latest["fields"]:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks an Aggregate Stewardship block for the wider recurring-job economy.",
            }
        )
        return result

    if latest["missing_fields"]:
        result.update(
            {
                "status": "incomplete",
                "message": "Latest Aggregate Stewardship block is missing: " + ", ".join(latest["missing_fields"]),
            }
        )
        return result

    if latest["stale_fields"]:
        result.update(
            {
                "status": "stale",
                "message": "Latest Aggregate Stewardship block has stale or non-durable fields: " + ", ".join(latest["stale_fields"]),
            }
        )
        return result

    result.update(
        {
            "status": "populated",
            "message": "Latest report names the portfolio-level provider concentration, choke points, verification debt, synchronized risk, portfolio state, and shared artifact.",
        }
    )
    return result


def inspect_value_surfaces(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect the latest recurring-loop output for durable-vs-circulation value surfaces."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "required_fields": list(VALUE_SURFACE_FIELDS),
        "status": "not_applicable",
        "message": "Value-surface checks apply only to recurring classified control loops.",
        "fields": {},
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")), limit=1)
    if not files:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking durable and circulation value surfaces.",
            }
        )
        return result

    latest = _inspect_value_surfaces_output(files[-1])
    result["latest_file"] = latest["file"]
    result["fields"] = latest["fields"]
    result["missing_fields"] = latest["missing_fields"]

    if not latest["has_section"] or not latest["fields"]:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks a Value Surfaces block naming durable and circulation surfaces.",
            }
        )
        return result

    if latest["missing_fields"]:
        result.update(
            {
                "status": "incomplete",
                "message": "Latest Value Surfaces block is missing: " + ", ".join(latest["missing_fields"]),
            }
        )
        return result

    if latest["circulation_only"]:
        result.update(
            {
                "status": "circulation_only",
                "message": "Latest Value Surfaces block still treats a cheap circulation signal as the durable store of value.",
            }
        )
        return result

    result.update(
        {
            "status": "populated",
            "message": "Latest report distinguishes the durable store of value from circulation outputs and keeps closure tied to durable updates.",
        }
    )
    return result


def inspect_attention_budget(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect the latest recurring-loop output for operator attention budget fields."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "required_fields": list(ATTENTION_BUDGET_FIELDS),
        "status": "not_applicable",
        "message": "Attention-budget checks apply only to recurring classified control loops.",
        "fields": {},
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")), limit=1)
    if not files:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking operator attention budget fields.",
            }
        )
        return result

    latest = _inspect_attention_budget_output(files[-1])
    result["latest_file"] = latest["file"]
    result["fields"] = latest["fields"]
    result["missing_fields"] = latest["missing_fields"]

    if not latest["has_section"] or not latest["fields"]:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks an Attention Budget block naming attention cost, decision value, and focus effect.",
            }
        )
        return result

    if latest["missing_fields"]:
        result.update(
            {
                "status": "incomplete",
                "message": "Latest Attention Budget block is missing: " + ", ".join(latest["missing_fields"]),
            }
        )
        return result

    if latest["low_yield"]:
        result.update(
            {
                "status": "low_yield",
                "message": "Latest Attention Budget block shows attention-heavy or spam-shaped output with too little decision value to count as healthy closure.",
            }
        )
        return result

    result.update(
        {
            "status": "populated",
            "message": "Latest report prices operator attention against decision value and says whether the run sharpened focus or drifted into low-yield alerting.",
        }
    )
    return result


def _first_proof_point_generic_signals(fields: Dict[str, str]) -> List[str]:
    joined = " ".join(fields.values()).lower()
    signals: List[str] = []
    generic_patterns = [
        r"\bglobal rollout\b",
        r"\bbroad rollout\b",
        r"\ball users\b",
        r"\bevery workflow\b",
        r"\bthe whole system\b",
        r"\borganization-wide\b",
        r"\bcompany-wide\b",
        r"\bframework\b",
        r"\bprinciple\b",
        r"\bdoctrine\b",
    ]
    if any(re.search(pattern, joined) for pattern in generic_patterns):
        signals.append("broad_doctrine")
    if not any(re.search(pattern, joined) for pattern in (r"\bhad-\d+\b", r"\b[a-z0-9_.-]+/[a-z0-9_.-]+\b", r"\bcron\b", r"\bjob\b", r"\bpr\b", r"\btest\b")):
        signals.append("no_bounded_surface_marker")
    return signals


def _inspect_first_proof_point_output(path: Path) -> Dict[str, Any]:
    text = _response_text(_read_bounded_output(path))
    fields = _parse_first_proof_point_fields(text)
    missing = [field for field in FIRST_PROOF_POINT_FIELDS if field not in fields]
    return {
        "file": path.name,
        "has_section": "first proof point" in text.lower() or "first seed" in text.lower(),
        "fields": fields,
        "missing_fields": missing,
        "generic_signals": _first_proof_point_generic_signals(fields),
    }


def inspect_first_proof_point(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect the latest control-loop output for a bounded first proof point."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "required_fields": list(FIRST_PROOF_POINT_FIELDS),
        "status": "not_applicable",
        "message": "First proof-point discipline applies only to recurring classified control loops.",
        "fields": {},
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")), limit=1)
    if not files:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking the first proof-point field set.",
            }
        )
        return result

    latest = _inspect_first_proof_point_output(files[-1])
    result["latest_file"] = latest["file"]
    result["fields"] = latest["fields"]
    result["missing_fields"] = latest["missing_fields"]
    result["generic_signals"] = latest["generic_signals"]

    if not latest["has_section"] or not latest["fields"]:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks a First Proof Point block naming one bounded seed.",
            }
        )
        return result

    if latest["missing_fields"]:
        result.update(
            {
                "status": "incomplete",
                "message": "Latest First Proof Point block is missing: " + ", ".join(latest["missing_fields"]),
            }
        )
        return result

    if latest["generic_signals"]:
        result.update(
            {
                "status": "generic",
                "message": (
                    "Latest First Proof Point reads like broad doctrine instead of a bounded nucleation site: "
                    + ", ".join(latest["generic_signals"])
                ),
            }
        )
        return result

    result.update(
        {
            "status": "populated",
            "message": "Latest report names a bounded first seed, its protection assumptions, success signal, and imitation path.",
        }
    )
    return result


def inspect_persistence_ratchet(job: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect recent output for a recurring control-loop job's persistence ratchet."""
    normalized_job = _apply_skill_fields(job)
    result: Dict[str, Any] = {
        "job_id": normalized_job.get("id"),
        "name": normalized_job.get("name", normalized_job.get("id")),
        "role": normalized_job.get("role"),
        "scope": normalized_job.get("scope"),
        "surfaces": list(RATCHET_SURFACES),
        "window_runs": RATCHET_WINDOW_RUNS,
        "checked_runs": 0,
        "status": "not_applicable",
        "compact_evidence": {},
        "message": "Persistence ratchet check applies only to recurring classified control loops.",
    }
    if not should_check_persistence_ratchet(normalized_job):
        return result

    files = _recent_output_files(str(normalized_job.get("id", "")))
    runs = [_inspect_ratchet_output(path) for path in files]
    result["checked_runs"] = len(runs)
    result["recent_outputs"] = [run["file"] for run in runs]

    if len(runs) < 2:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need at least two saved runs before distinguishing durable carry-forward from lucky repetition.",
            }
        )
        return result

    signal_counts: Dict[str, int] = {}
    for run in runs:
        for signal in run["signals"]:
            signal_counts[signal] = signal_counts.get(signal, 0) + 1
    repeated_signals = sorted(signal for signal, count in signal_counts.items() if count >= 2)

    latest = runs[-1]
    preserved = _preserved_ratchet_items(latest, runs[:-1])
    preserved_count = sum(len(items) for items in preserved.values())
    compact_evidence = {
        "latest_file": latest["file"],
        "latest_core_categories": latest["core_category_count"],
        "latest_has_carry_forward": latest["has_carry_forward"],
        "preserved_items": preserved,
        "preserved_item_count": preserved_count,
        "repeated_signals": repeated_signals,
    }
    result["compact_evidence"] = compact_evidence

    if repeated_signals:
        result.update(
            {
                "status": "drift",
                "message": (
                    "Repeated rediscovery or cleanup drift appeared in recent runs: "
                    + ", ".join(repeated_signals)
                ),
            }
        )
        return result

    if not latest["has_section"] or latest["core_category_count"] < 2:
        result.update(
            {
                "status": "missing",
                "message": "Latest report lacks compact usable evidence, decisions, or artifacts for the persistence ratchet.",
            }
        )
        return result

    if not latest["has_carry_forward"] or preserved_count == 0:
        result.update(
            {
                "status": "weak",
                "message": "Latest report names ratchet state but does not preserve usable evidence, decisions, or artifacts from prior runs.",
            }
        )
        return result

    result.update(
        {
            "status": "healthy",
            "message": "Latest report preserves usable evidence, decisions, or artifacts from prior runs.",
        }
    )
    return result


def _secure_dir(path: Path):
    """Set directory to owner-only access (0700). No-op on Windows."""
    try:
        os.chmod(path, 0o700)
    except (OSError, NotImplementedError):
        pass  # Windows or other platforms where chmod is not supported


def _secure_file(path: Path):
    """Set file to owner-only read/write (0600). No-op on Windows."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def ensure_dirs():
    """Ensure cron directories exist with secure permissions."""
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _secure_dir(CRON_DIR)
    _secure_dir(OUTPUT_DIR)


# =============================================================================
# Schedule Parsing
# =============================================================================

def parse_duration(s: str) -> int:
    """
    Parse duration string into minutes.
    
    Examples:
        "30m" → 30
        "2h" → 120
        "1d" → 1440
    """
    s = s.strip().lower()
    match = re.match(r'^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not match:
        raise ValueError(f"Invalid duration: '{s}'. Use format like '30m', '2h', or '1d'")
    
    value = int(match.group(1))
    unit = match.group(2)[0]  # First char: m, h, or d
    
    multipliers = {'m': 1, 'h': 60, 'd': 1440}
    return value * multipliers[unit]


def parse_schedule(schedule: str) -> Dict[str, Any]:
    """
    Parse schedule string into structured format.
    
    Returns dict with:
        - kind: "once" | "interval" | "cron"
        - For "once": "run_at" (ISO timestamp)
        - For "interval": "minutes" (int)
        - For "cron": "expr" (cron expression)
    
    Examples:
        "30m"              → once in 30 minutes
        "2h"               → once in 2 hours
        "every 30m"        → recurring every 30 minutes
        "every 2h"         → recurring every 2 hours
        "0 9 * * *"        → cron expression
        "2026-02-03T14:00" → once at timestamp
    """
    schedule = schedule.strip()
    original = schedule
    schedule_lower = schedule.lower()
    
    # "every X" pattern → recurring interval
    if schedule_lower.startswith("every "):
        duration_str = schedule[6:].strip()
        minutes = parse_duration(duration_str)
        return {
            "kind": "interval",
            "minutes": minutes,
            "display": f"every {minutes}m"
        }
    
    # Check for cron expression (5 or 6 space-separated fields)
    # Cron fields: minute hour day month weekday [year]
    parts = schedule.split()
    if len(parts) >= 5 and all(
        re.match(r'^[\d\*\-,/]+$', p) for p in parts[:5]
    ):
        if not HAS_CRONITER:
            raise ValueError("Cron expressions require 'croniter' package. Install with: pip install croniter")
        # Validate cron expression
        try:
            croniter(schedule)
        except Exception as e:
            raise ValueError(f"Invalid cron expression '{schedule}': {e}")
        return {
            "kind": "cron",
            "expr": schedule,
            "display": schedule
        }
    
    # ISO timestamp (contains T or looks like date)
    if 'T' in schedule or re.match(r'^\d{4}-\d{2}-\d{2}', schedule):
        try:
            # Parse and validate
            dt = datetime.fromisoformat(schedule.replace('Z', '+00:00'))
            # Make naive timestamps timezone-aware at parse time so the stored
            # value doesn't depend on the system timezone matching at check time.
            if dt.tzinfo is None:
                dt = dt.astimezone()  # Interpret as local timezone
            return {
                "kind": "once",
                "run_at": dt.isoformat(),
                "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}"
            }
        except ValueError as e:
            raise ValueError(f"Invalid timestamp '{schedule}': {e}")
    
    # Duration like "30m", "2h", "1d" → one-shot from now
    try:
        minutes = parse_duration(schedule)
        run_at = _hermes_now() + timedelta(minutes=minutes)
        return {
            "kind": "once",
            "run_at": run_at.isoformat(),
            "display": f"once in {original}"
        }
    except ValueError:
        pass
    
    raise ValueError(
        f"Invalid schedule '{original}'. Use:\n"
        f"  - Duration: '30m', '2h', '1d' (one-shot)\n"
        f"  - Interval: 'every 30m', 'every 2h' (recurring)\n"
        f"  - Cron: '0 9 * * *' (cron expression)\n"
        f"  - Timestamp: '2026-02-03T14:00:00' (one-shot at time)"
    )


def _ensure_aware(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in Hermes configured timezone.

    Backward compatibility:
    - Older stored timestamps may be naive.
    - Naive values are interpreted as *system-local wall time* (the timezone
      `datetime.now()` used when they were created), then converted to the
      configured Hermes timezone.

    This preserves relative ordering for legacy naive timestamps across
    timezone changes and avoids false not-due results.
    """
    target_tz = _hermes_now().tzinfo
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).astimezone(target_tz)
    return dt.astimezone(target_tz)


def _recoverable_oneshot_run_at(
    schedule: Dict[str, Any],
    now: datetime,
    *,
    last_run_at: Optional[str] = None,
) -> Optional[str]:
    """Return a one-shot run time if it is still eligible to fire.

    One-shot jobs get a small grace window so jobs created a few seconds after
    their requested minute still run on the next tick. Once a one-shot has
    already run, it is never eligible again.
    """
    if schedule.get("kind") != "once":
        return None
    if last_run_at:
        return None

    run_at = schedule.get("run_at")
    if not run_at:
        return None

    run_at_dt = _ensure_aware(datetime.fromisoformat(run_at))
    if run_at_dt >= now - timedelta(seconds=ONESHOT_GRACE_SECONDS):
        return run_at
    return None


def _compute_grace_seconds(schedule: dict) -> int:
    """Compute how late a job can be and still catch up instead of fast-forwarding.

    Uses half the schedule period, clamped between 120 seconds and 2 hours.
    This ensures daily jobs can catch up if missed by up to 2 hours,
    while frequent jobs (every 5-10 min) still fast-forward quickly.
    """
    MIN_GRACE = 120
    MAX_GRACE = 7200  # 2 hours

    kind = schedule.get("kind")

    if kind == "interval":
        period_seconds = schedule.get("minutes", 1) * 60
        grace = period_seconds // 2
        return max(MIN_GRACE, min(grace, MAX_GRACE))

    if kind == "cron" and HAS_CRONITER:
        try:
            now = _hermes_now()
            cron = croniter(schedule["expr"], now)
            first = cron.get_next(datetime)
            second = cron.get_next(datetime)
            period_seconds = int((second - first).total_seconds())
            grace = period_seconds // 2
            return max(MIN_GRACE, min(grace, MAX_GRACE))
        except Exception:
            pass

    return MIN_GRACE


def compute_next_run(schedule: Dict[str, Any], last_run_at: Optional[str] = None) -> Optional[str]:
    """
    Compute the next run time for a schedule.

    Returns ISO timestamp string, or None if no more runs.
    """
    now = _hermes_now()

    if schedule["kind"] == "once":
        return _recoverable_oneshot_run_at(schedule, now, last_run_at=last_run_at)

    elif schedule["kind"] == "interval":
        minutes = schedule["minutes"]
        if last_run_at:
            # Next run is last_run + interval
            last = _ensure_aware(datetime.fromisoformat(last_run_at))
            next_run = last + timedelta(minutes=minutes)
        else:
            # First run is now + interval
            next_run = now + timedelta(minutes=minutes)
        return next_run.isoformat()

    elif schedule["kind"] == "cron":
        if not HAS_CRONITER:
            return None
        cron = croniter(schedule["expr"], now)
        next_run = cron.get_next(datetime)
        return next_run.isoformat()

    return None


# =============================================================================
# Job CRUD Operations
# =============================================================================

def load_jobs() -> List[Dict[str, Any]]:
    """Load all jobs from storage."""
    ensure_dirs()
    if not JOBS_FILE.exists():
        return []
    
    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("jobs", [])
    except json.JSONDecodeError:
        # Retry with strict=False to handle bare control chars in string values
        try:
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                data = json.loads(f.read(), strict=False)
                jobs = data.get("jobs", [])
                if jobs:
                    # Auto-repair: rewrite with proper escaping
                    save_jobs(jobs)
                    logger.warning("Auto-repaired jobs.json (had invalid control characters)")
                return jobs
        except Exception:
            return []
    except IOError:
        return []


def save_jobs(jobs: List[Dict[str, Any]]):
    """Save all jobs to storage."""
    ensure_dirs()
    fd, tmp_path = tempfile.mkstemp(dir=str(JOBS_FILE.parent), suffix='.tmp', prefix='.jobs_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({"jobs": jobs, "updated_at": _hermes_now().isoformat()}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, JOBS_FILE)
        _secure_file(JOBS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_job(
    prompt: str,
    schedule: str,
    name: Optional[str] = None,
    repeat: Optional[int] = None,
    deliver: Optional[str] = None,
    origin: Optional[Dict[str, Any]] = None,
    skill: Optional[str] = None,
    skills: Optional[List[str]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    script: Optional[str] = None,
    role: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new cron job.

    Args:
        prompt: The prompt to run (must be self-contained, or a task instruction when skill is set)
        schedule: Schedule string (see parse_schedule)
        name: Optional friendly name
        repeat: How many times to run (None = forever, 1 = once)
        deliver: Where to deliver output ("origin", "local", "telegram", etc.)
        origin: Source info where job was created (for "origin" delivery)
        skill: Optional legacy single skill name to load before running the prompt
        skills: Optional ordered list of skills to load before running the prompt
        model: Optional per-job model override
        provider: Optional per-job provider override
        base_url: Optional per-job base URL override
        script: Optional path to a Python script whose stdout is injected into the
                prompt each run

    Returns:
        The created job dict
    """
    parsed_schedule = parse_schedule(schedule)

    # Normalize repeat: treat 0 or negative values as None (infinite)
    if repeat is not None and repeat <= 0:
        repeat = None

    # Auto-set repeat=1 for one-shot schedules if not specified
    if parsed_schedule["kind"] == "once" and repeat is None:
        repeat = 1

    # Default delivery to origin if available, otherwise local
    if deliver is None:
        deliver = "origin" if origin else "local"

    job_id = uuid.uuid4().hex[:12]
    now = _hermes_now().isoformat()

    normalized_skills = _normalize_skill_list(skill, skills)
    normalized_model = str(model).strip() if isinstance(model, str) else None
    normalized_provider = str(provider).strip() if isinstance(provider, str) else None
    normalized_base_url = str(base_url).strip().rstrip("/") if isinstance(base_url, str) else None
    normalized_model = normalized_model or None
    normalized_provider = normalized_provider or None
    normalized_base_url = normalized_base_url or None
    normalized_script = str(script).strip() if isinstance(script, str) else None
    normalized_script = normalized_script or None
    normalized_role = _normalize_taxonomy_value(role)
    normalized_scope = _normalize_taxonomy_value(scope)

    label_source = (prompt or (normalized_skills[0] if normalized_skills else None)) or "cron job"
    job = {
        "id": job_id,
        "name": name or label_source[:50].strip(),
        "prompt": prompt,
        "skills": normalized_skills,
        "skill": normalized_skills[0] if normalized_skills else None,
        "model": normalized_model,
        "provider": normalized_provider,
        "base_url": normalized_base_url,
        "script": normalized_script,
        "role": normalized_role,
        "scope": normalized_scope,
        "schedule": parsed_schedule,
        "schedule_display": parsed_schedule.get("display", schedule),
        "repeat": {
            "times": repeat,  # None = forever
            "completed": 0
        },
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": now,
        "next_run_at": compute_next_run(parsed_schedule),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        # Delivery configuration
        "deliver": deliver,
        "origin": origin,  # Tracks where job was created for "origin" delivery
    }

    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)

    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return _apply_skill_fields(job)
    return None


def list_jobs(include_disabled: bool = False) -> List[Dict[str, Any]]:
    """List all jobs, optionally including disabled ones."""
    jobs = [_apply_skill_fields(j) for j in load_jobs()]
    if not include_disabled:
        jobs = [j for j in jobs if j.get("enabled", True)]
    return jobs


def inspect_job_topology(include_disabled: bool = True) -> Dict[str, Any]:
    """Return a topology snapshot plus overlap diagnostics for cron jobs.

    The inspector is intentionally generic:
    - duplicate names are always flagged because name-based operator workflows
      become ambiguous when multiple jobs share a label
    - implementation conflicts are only checked for jobs that opt into the
      `role=implement` / `scope=*` taxonomy
    """
    jobs = list_jobs(include_disabled=include_disabled)
    active_jobs = [job for job in jobs if job.get("enabled", True)]
    inactive_jobs = [job for job in jobs if not job.get("enabled", True)]

    grouped_active: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    unclassified_active: List[Dict[str, Any]] = []
    for job in active_jobs:
        role = job.get("role")
        scope = job.get("scope")
        if role or scope:
            grouped_active.setdefault(role or "unclassified", {}).setdefault(
                scope or "unscoped", []
            ).append(job)
        else:
            unclassified_active.append(job)

    issues: List[Dict[str, Any]] = []

    jobs_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        jobs_by_name.setdefault(job.get("name", job["id"]), []).append(job)
    for name, named_jobs in sorted(jobs_by_name.items()):
        if len(named_jobs) < 2:
            continue
        active_ids = [job["id"] for job in named_jobs if job.get("enabled", True)]
        paused_ids = [job["id"] for job in named_jobs if not job.get("enabled", True)]
        severity = "error" if len(active_ids) > 1 else "warning"
        state_parts = []
        if active_ids:
            state_parts.append(f"active={', '.join(active_ids)}")
        if paused_ids:
            state_parts.append(f"paused={', '.join(paused_ids)}")
        issues.append(
            {
                "severity": severity,
                "code": "duplicate_job_name",
                "name": name,
                "job_ids": [job["id"] for job in named_jobs],
                "message": (
                    f"Multiple cron jobs share the name '{name}'"
                    + (f" ({'; '.join(state_parts)})" if state_parts else "")
                    + ". Rename or remove legacy entries so operators and agents do not target the wrong job."
                ),
            }
        )

    active_implementers = [job for job in active_jobs if job.get("role") == "implement"]
    implementers_by_scope: Dict[str, List[Dict[str, Any]]] = {}
    for job in active_implementers:
        scope = job.get("scope")
        if scope:
            implementers_by_scope.setdefault(scope, []).append(job)

    for scope, scoped_jobs in sorted(implementers_by_scope.items()):
        if len(scoped_jobs) < 2:
            continue
        issues.append(
            {
                "severity": "error",
                "code": "duplicate_implementation_scope",
                "scope": scope,
                "job_ids": [job["id"] for job in scoped_jobs],
                "message": (
                    f"Multiple active implementation jobs target scope '{scope}': "
                    + ", ".join(f"{job['name']} ({job['id']})" for job in scoped_jobs)
                ),
            }
        )

    global_implementers = [job for job in active_implementers if job.get("scope") == "global"]
    scoped_implementers = [
        job for job in active_implementers if job.get("scope") and job.get("scope") != "global"
    ]
    if global_implementers and scoped_implementers:
        issues.append(
            {
                "severity": "error",
                "code": "global_implementation_overlap",
                "job_ids": [job["id"] for job in global_implementers + scoped_implementers],
                "message": (
                    "A global implementation job is active alongside scoped implementation jobs. "
                    "Pause or retire the global implementer to avoid duplicate autonomous work."
                ),
            }
        )

    global_coordinators = [
        job
        for job in active_jobs
        if job.get("role") == "coordinate" and job.get("scope") == "global"
    ]
    if len(global_coordinators) > 1:
        issues.append(
            {
                "severity": "error",
                "code": "duplicate_global_coordinator",
                "job_ids": [job["id"] for job in global_coordinators],
                "message": (
                    "Multiple global coordinator jobs are active. Keep exactly one "
                    "workspace-wide orchestrator so backlog selection remains deterministic."
                ),
            }
        )

    if global_coordinators and scoped_implementers:
        issues.append(
            {
                "severity": "error",
                "code": "global_coordinator_with_scoped_implementers",
                "job_ids": [job["id"] for job in global_coordinators + scoped_implementers],
                "message": (
                    "A global coordinator is active alongside scoped implementation jobs. "
                    "Pause the scoped implementers or retire the coordinator so Hermes has "
                    "one clear backlog owner."
                ),
            }
        )

    persistence_ratchets: List[Dict[str, Any]] = []
    first_proof_points: List[Dict[str, Any]] = []
    geometry_shaping_checks: List[Dict[str, Any]] = []
    value_surface_checks: List[Dict[str, Any]] = []
    attention_budget_checks: List[Dict[str, Any]] = []
    aggregate_stewardship_checks: List[Dict[str, Any]] = []
    routine_delivery_gates: List[Dict[str, Any]] = []
    trust_contracts: List[Dict[str, Any]] = []
    for job in active_jobs:
        routine_gate = inspect_routine_delivery_gate(
            job,
            load_recent_job_responses(str(job.get("id", "")), limit=RATCHET_WINDOW_RUNS),
        )
        if routine_gate["status"] != "not_applicable":
            routine_delivery_gates.append(routine_gate)
            if routine_gate["status"] in {
                "suppressible_noise",
                "productive_fallback_missing",
                "durable_action_missing",
                "missing",
                "incomplete",
            }:
                issues.append(
                    {
                        "severity": "warning",
                        "code": f"routine_delivery_gate_{routine_gate['status']}",
                        "job_id": job["id"],
                        "job_name": job.get("name", job["id"]),
                        "routine_kind": routine_gate.get("routine_kind"),
                        "fields": routine_gate.get("fields", {}),
                        "message": (
                            f"Routine delivery gate {routine_gate['status']} for recurring routine "
                            f"'{job.get('name', job['id'])}' ({job['id']}): {routine_gate['message']} "
                            "Suppress no-change Slack circulation and route blocked capacity to productive durable work."
                        ),
                    }
                )

        if not should_check_persistence_ratchet(job):
            trust_contracts.append(inspect_trust_contract(job))
            continue
        ratchet = inspect_persistence_ratchet(job)
        proof_point = inspect_first_proof_point(job)
        geometry = inspect_geometry_shaping(job)
        value_surfaces = inspect_value_surfaces(job)
        attention_budget = inspect_attention_budget(job)
        aggregate_stewardship = inspect_aggregate_stewardship(job)
        persistence_ratchets.append(ratchet)
        first_proof_points.append(proof_point)
        geometry_shaping_checks.append(geometry)
        value_surface_checks.append(value_surfaces)
        attention_budget_checks.append(attention_budget)
        aggregate_stewardship_checks.append(aggregate_stewardship)
        trust_contracts.append(inspect_trust_contract(job, ratchet, proof_point, geometry, value_surfaces, attention_budget, aggregate_stewardship))
        if ratchet["status"] not in {"insufficient_history", "healthy"}:
            issue_code = {
                "drift": "persistence_ratchet_drift",
                "missing": "persistence_ratchet_missing",
                "weak": "persistence_ratchet_weak",
            }.get(ratchet["status"], "persistence_ratchet_issue")
            issues.append(
                {
                    "severity": "warning",
                    "code": issue_code,
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "ratchet_status": ratchet["status"],
                    "surfaces": list(RATCHET_SURFACES),
                    "compact_evidence": ratchet.get("compact_evidence", {}),
                    "message": (
                        f"Persistence ratchet {ratchet['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {ratchet['message']} "
                        "This is an operator-value, anti-make-work, and leading-indicator warning."
                    ),
                }
            )

        if proof_point["status"] not in {"insufficient_history", "populated"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"first_proof_point_{proof_point['status']}",
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "proof_point_status": proof_point["status"],
                    "fields": proof_point.get("fields", {}),
                    "missing_fields": proof_point.get("missing_fields", []),
                    "generic_signals": proof_point.get("generic_signals", []),
                    "message": (
                        f"First proof point {proof_point['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {proof_point['message']} "
                        "Name one protected seed surface before treating governance language as actionable."
                    ),
                }
            )

        if geometry["status"] not in {"insufficient_history", "populated"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"geometry_shaping_{geometry['status']}",
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "geometry_status": geometry["status"],
                    "fields": geometry.get("fields", {}),
                    "missing_fields": geometry.get("missing_fields", []),
                    "generic_signals": geometry.get("generic_signals", []),
                    "message": (
                        f"Geometry shaping {geometry['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {geometry['message']} "
                        "Name how the system changed defaults, channels, friction, pruning, and the path-vs-policy distinction."
                    ),
                }
            )

        if value_surfaces["status"] not in {"insufficient_history", "populated"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"value_surfaces_{value_surfaces['status']}",
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "value_surface_status": value_surfaces["status"],
                    "fields": value_surfaces.get("fields", {}),
                    "missing_fields": value_surfaces.get("missing_fields", []),
                    "message": (
                        f"Value surfaces {value_surfaces['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {value_surfaces['message']} "
                        "Name the durable store of value separately from circulation-only signals."
                    ),
                }
            )

        if attention_budget["status"] not in {"insufficient_history", "populated"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"attention_budget_{attention_budget['status']}",
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "attention_budget_status": attention_budget["status"],
                    "fields": attention_budget.get("fields", {}),
                    "missing_fields": attention_budget.get("missing_fields", []),
                    "message": (
                        f"Attention budget {attention_budget['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {attention_budget['message']} "
                        "Show what operator attention bought instead of pricing report spam as closure."
                    ),
                }
            )

        if aggregate_stewardship["status"] not in {"insufficient_history", "populated"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"aggregate_stewardship_{aggregate_stewardship['status']}",
                    "job_id": job["id"],
                    "job_name": job.get("name", job["id"]),
                    "aggregate_stewardship_status": aggregate_stewardship["status"],
                    "fields": aggregate_stewardship.get("fields", {}),
                    "missing_fields": aggregate_stewardship.get("missing_fields", []),
                    "stale_fields": aggregate_stewardship.get("stale_fields", []),
                    "message": (
                        f"Aggregate stewardship {aggregate_stewardship['status']} for recurring control loop "
                        f"'{job.get('name', job['id'])}' ({job['id']}): {aggregate_stewardship['message']} "
                        "Keep the portfolio view visible above single-loop wins."
                    ),
                }
            )

    for job in inactive_jobs:
        trust_contracts.append(inspect_trust_contract(job))

    aggregate_stewardship_summary = _summarize_aggregate_stewardship(active_jobs, aggregate_stewardship_checks, trust_contracts)

    return {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "summary": {
            "total_jobs": len(jobs),
            "active_jobs": len(active_jobs),
            "inactive_jobs": len(inactive_jobs),
            "classified_active_jobs": len(active_jobs) - len(unclassified_active),
            "issue_count": len(issues),
            "persistence_ratchet_checked": len(persistence_ratchets),
            "persistence_ratchet_issue_count": sum(
                1 for ratchet in persistence_ratchets
                if ratchet["status"] not in {"insufficient_history", "healthy"}
            ),
            "first_proof_point_checked": len(first_proof_points),
            "first_proof_point_issue_count": sum(
                1 for proof_point in first_proof_points
                if proof_point["status"] not in {"insufficient_history", "populated"}
            ),
            "geometry_shaping_checked": len(geometry_shaping_checks),
            "geometry_shaping_issue_count": sum(
                1 for geometry in geometry_shaping_checks
                if geometry["status"] not in {"insufficient_history", "populated"}
            ),
            "value_surfaces_checked": len(value_surface_checks),
            "value_surfaces_issue_count": sum(
                1 for value_surfaces in value_surface_checks
                if value_surfaces["status"] not in {"insufficient_history", "populated"}
            ),
            "attention_budget_checked": len(attention_budget_checks),
            "attention_budget_issue_count": sum(
                1 for attention_budget in attention_budget_checks
                if attention_budget["status"] not in {"insufficient_history", "populated"}
            ),
            "aggregate_stewardship_checked": len(aggregate_stewardship_checks),
            "aggregate_stewardship_issue_count": sum(
                1 for stewardship in aggregate_stewardship_checks
                if stewardship["status"] not in {"insufficient_history", "populated"}
            ),
            "routine_delivery_gate_checked": len(routine_delivery_gates),
            "routine_delivery_gate_issue_count": sum(
                1 for gate in routine_delivery_gates
                if gate["status"] in {
                    "suppressible_noise",
                    "productive_fallback_missing",
                    "durable_action_missing",
                    "missing",
                    "incomplete",
                }
            ),
            "trust_contract_checked": len(trust_contracts),
            "trust_contract_degraded_count": sum(
                1 for contract in trust_contracts if contract.get("failed_commitment_visible")
            ),
        },
        "active_jobs": active_jobs,
        "inactive_jobs": inactive_jobs,
        "unclassified_active_jobs": unclassified_active,
        "grouped_active_jobs": grouped_active,
        "persistence_ratchets": persistence_ratchets,
        "first_proof_points": first_proof_points,
        "geometry_shaping": geometry_shaping_checks,
        "value_surfaces": value_surface_checks,
        "attention_budget": attention_budget_checks,
        "aggregate_stewardship": aggregate_stewardship_checks,
        "routine_delivery_gates": routine_delivery_gates,
        "aggregate_stewardship_summary": aggregate_stewardship_summary,
        "trust_contracts": trust_contracts,
        "issues": issues,
    }


def _summarize_aggregate_stewardship(active_jobs: List[Dict[str, Any]], aggregate_checks: List[Dict[str, Any]], trust_contracts: List[Dict[str, Any]]) -> Dict[str, Any]:
    provider_counts: Dict[str, int] = {}
    for job in active_jobs:
        if not should_check_persistence_ratchet(job):
            continue
        provider = str(job.get("provider") or "default").strip() or "default"
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    repeated_providers = [f"{provider}={count}" for provider, count in sorted(provider_counts.items()) if count > 1]
    degraded_contracts = [contract for contract in trust_contracts if contract.get("failed_commitment_visible")]
    incomplete_checks = [check for check in aggregate_checks if check.get("status") not in {"insufficient_history", "populated"}]
    field_sets = [check.get("fields", {}) for check in aggregate_checks if check.get("status") == "populated"]

    choke_points = [fields.get("dependency_choke_points") for fields in field_sets if fields.get("dependency_choke_points")]
    verification_debt = [fields.get("verification_debt") for fields in field_sets if fields.get("verification_debt")]
    sync_risks = [fields.get("synchronized_failure_risk") for fields in field_sets if fields.get("synchronized_failure_risk")]
    portfolio_states = [fields.get("portfolio_state") for fields in field_sets if fields.get("portfolio_state")]

    fragility = "healthy"
    if degraded_contracts or incomplete_checks:
        fragility = "fragile"
    if repeated_providers and (degraded_contracts or incomplete_checks):
        fragility = "concentrated_and_fragile"

    return {
        "shared_artifact": "inspect_job_topology()/hermes cron topology aggregate stewardship summary",
        "shared_provider_concentration": ", ".join(repeated_providers) or "no repeated configured provider concentration detected",
        "dependency_choke_points": choke_points[:3],
        "verification_debt": verification_debt[:3] or [
            f"{len(incomplete_checks)} recurring jobs missing/incomplete/stale aggregate stewardship blocks",
            f"{len(degraded_contracts)} degraded trust contracts visible in topology",
        ],
        "synchronized_failure_risk": sync_risks[:3] or [
            f"{len(degraded_contracts)} degraded recurring jobs across {len(provider_counts) or 1} provider buckets"
        ],
        "portfolio_state": portfolio_states[:3] or [fragility],
        "checked_jobs": len(aggregate_checks),
        "issue_count": len(incomplete_checks),
    }


def update_job(job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a job by ID, refreshing derived schedule fields when needed."""
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] != job_id:
            continue

        updated = _apply_skill_fields({**job, **updates})
        schedule_changed = "schedule" in updates

        if "skills" in updates or "skill" in updates:
            normalized_skills = _normalize_skill_list(updated.get("skill"), updated.get("skills"))
            updated["skills"] = normalized_skills
            updated["skill"] = normalized_skills[0] if normalized_skills else None
        if "role" in updates:
            updated["role"] = _normalize_taxonomy_value(updated.get("role"))
        if "scope" in updates:
            updated["scope"] = _normalize_taxonomy_value(updated.get("scope"))
        if "script" in updates:
            normalized_script = str(updated.get("script")).strip() if isinstance(updated.get("script"), str) else None
            updated["script"] = normalized_script or None

        if schedule_changed:
            updated_schedule = updated["schedule"]
            if isinstance(updated_schedule, str):
                updated_schedule = parse_schedule(updated_schedule)
                updated["schedule"] = updated_schedule
            updated["schedule_display"] = updates.get(
                "schedule_display",
                updated_schedule.get("display", updated.get("schedule_display")),
            )
            if updated.get("state") != "paused":
                updated["next_run_at"] = compute_next_run(updated_schedule)

        if updated.get("enabled", True) and updated.get("state") != "paused" and not updated.get("next_run_at"):
            updated["next_run_at"] = compute_next_run(updated["schedule"])

        jobs[i] = updated
        save_jobs(jobs)
        return _apply_skill_fields(jobs[i])
    return None


def pause_job(job_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pause a job without deleting it."""
    return update_job(
        job_id,
        {
            "enabled": False,
            "state": "paused",
            "paused_at": _hermes_now().isoformat(),
            "paused_reason": reason,
        },
    )


def resume_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Resume a paused job and compute the next future run from now."""
    job = get_job(job_id)
    if not job:
        return None

    next_run_at = compute_next_run(job["schedule"])
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": next_run_at,
        },
    )


def trigger_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Schedule a job to run on the next scheduler tick."""
    job = get_job(job_id)
    if not job:
        return None
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": _hermes_now().isoformat(),
        },
    )


def remove_job(job_id: str) -> bool:
    """Remove a job by ID."""
    jobs = load_jobs()
    original_len = len(jobs)
    jobs = [j for j in jobs if j["id"] != job_id]
    if len(jobs) < original_len:
        save_jobs(jobs)
        return True
    return False


def mark_job_run(
    job_id: str,
    success: bool,
    error: Optional[str] = None,
    delivery_error: Optional[str] = None,
):
    """
    Mark a job as having been run.
    
    Updates last_run_at, last_status, increments completed count,
    computes next_run_at, and auto-deletes if repeat limit reached.

    ``delivery_error`` is tracked separately from the agent error so a run can
    succeed while message delivery still fails.
    """
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] == job_id:
            now = _hermes_now().isoformat()
            job["last_run_at"] = now
            job["last_status"] = "ok" if success else "error"
            job["last_error"] = error if not success else None
            job["last_delivery_error"] = delivery_error
            
            # Increment completed count
            if job.get("repeat"):
                job["repeat"]["completed"] = job["repeat"].get("completed", 0) + 1
                
                # Check if we've hit the repeat limit
                times = job["repeat"].get("times")
                completed = job["repeat"]["completed"]
                if times is not None and times > 0 and completed >= times:
                    # Remove the job (limit reached)
                    jobs.pop(i)
                    save_jobs(jobs)
                    return
            
            # Compute next run
            job["next_run_at"] = compute_next_run(job["schedule"], now)

            # If no next run (one-shot completed), disable
            if job["next_run_at"] is None:
                job["enabled"] = False
                job["state"] = "completed"
            elif job.get("state") != "paused":
                job["state"] = "scheduled"

            save_jobs(jobs)
            return
    
    save_jobs(jobs)


def advance_next_run(job_id: str) -> bool:
    """Preemptively advance next_run_at for a recurring job before execution.

    Call this BEFORE run_job() so that if the process crashes mid-execution,
    the job won't re-fire on the next gateway restart.  This converts the
    scheduler from at-least-once to at-most-once for recurring jobs — missing
    one run is far better than firing dozens of times in a crash loop.

    One-shot jobs are left unchanged so they can still retry on restart.

    Returns True if next_run_at was advanced, False otherwise.
    """
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            kind = job.get("schedule", {}).get("kind")
            if kind not in ("cron", "interval"):
                return False
            now = _hermes_now().isoformat()
            new_next = compute_next_run(job["schedule"], now)
            if new_next and new_next != job.get("next_run_at"):
                job["next_run_at"] = new_next
                save_jobs(jobs)
                return True
            return False
    return False


def get_due_jobs() -> List[Dict[str, Any]]:
    """Get all jobs that are due to run now.

    For recurring jobs (cron/interval), if the scheduled time is stale
    (more than one period in the past, e.g. because the gateway was down),
    the job is fast-forwarded to the next future run instead of firing
    immediately.  This prevents a burst of missed jobs on gateway restart.
    """
    now = _hermes_now()
    raw_jobs = load_jobs()
    jobs = [_apply_skill_fields(j) for j in copy.deepcopy(raw_jobs)]
    due = []
    needs_save = False

    for job in jobs:
        if not job.get("enabled", True):
            continue

        next_run = job.get("next_run_at")
        if not next_run:
            recovered_next = _recoverable_oneshot_run_at(
                job.get("schedule", {}),
                now,
                last_run_at=job.get("last_run_at"),
            )
            if not recovered_next:
                continue

            job["next_run_at"] = recovered_next
            next_run = recovered_next
            logger.info(
                "Job '%s' had no next_run_at; recovering one-shot run at %s",
                job.get("name", job["id"]),
                recovered_next,
            )
            for rj in raw_jobs:
                if rj["id"] == job["id"]:
                    rj["next_run_at"] = recovered_next
                    needs_save = True
                    break

        next_run_dt = _ensure_aware(datetime.fromisoformat(next_run))
        if next_run_dt <= now:
            schedule = job.get("schedule", {})
            kind = schedule.get("kind")

            # For recurring jobs, check if the scheduled time is stale
            # (gateway was down and missed the window). Fast-forward to
            # the next future occurrence instead of firing a stale run.
            grace = _compute_grace_seconds(schedule)
            if kind in ("cron", "interval") and (now - next_run_dt).total_seconds() > grace:
                # Job is past its catch-up grace window — this is a stale missed run.
                # Grace scales with schedule period: daily=2h, hourly=30m, 10min=5m.
                new_next = compute_next_run(schedule, now.isoformat())
                if new_next:
                    logger.info(
                        "Job '%s' missed its scheduled time (%s, grace=%ds). "
                        "Fast-forwarding to next run: %s",
                        job.get("name", job["id"]),
                        next_run,
                        grace,
                        new_next,
                    )
                    # Update the job in storage
                    for rj in raw_jobs:
                        if rj["id"] == job["id"]:
                            rj["next_run_at"] = new_next
                            needs_save = True
                            break
                    continue  # Skip this run

            due.append(job)

    if needs_save:
        save_jobs(raw_jobs)

    return due


def save_job_output(job_id: str, output: str):
    """Save job output to file."""
    ensure_dirs()
    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)
    _secure_dir(job_output_dir)
    
    timestamp = _hermes_now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = job_output_dir / f"{timestamp}.md"
    
    fd, tmp_path = tempfile.mkstemp(dir=str(job_output_dir), suffix='.tmp', prefix='.output_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, output_file)
        _secure_file(output_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    
    return output_file
