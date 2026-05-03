"""Recurring routine delivery and productive-fallback policy.

This module keeps Hadto/Hermes recurring loops from turning scarce operator
attention into circulation-only status. It is intentionally text-based because
cron routines are natural-language jobs, but the checks are centralized so the
scheduler, topology inspector, and tests apply the same policy.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


SILENT_MARKER = "[SILENT]"

PRODUCTIVE_FALLBACK_LANES = [
    "vertical opportunity research",
    "source discovery",
    "book-study continuation",
    "ontology enrichment",
    "blog post production",
    "lead research",
    "implementation",
]

HEARTBEAT_MATERIAL_TRIGGERS = [
    "selected issue changed",
    "live worker count changed",
    "stale/blocker/cron-failure appeared or changed",
    "first proof artifact appeared",
    "David-needed decision appeared",
    "daily summary window",
]

PROFILE_INTERVIEW_TRIGGERS = [
    "fresh material question",
    "tagged answer to capture",
    "uncaptured contradiction",
    "uncaptured operator preference",
]

ROUTINE_GATE_FIELDS = [
    "material_trigger",
    "emit",
    "durable_artifact",
    "circulation",
    "productive_fallback",
    "systematic_defect_action",
]

_GATE_INLINE_KEYS = {
    "material trigger": "material_trigger",
    "material_trigger": "material_trigger",
    "trigger": "material_trigger",
    "emit": "emit",
    "delivery": "emit",
    "durable artifact": "durable_artifact",
    "durable_artifact": "durable_artifact",
    "artifact": "durable_artifact",
    "circulation": "circulation",
    "circulation-only": "circulation",
    "productive fallback": "productive_fallback",
    "productive_fallback": "productive_fallback",
    "fallback": "productive_fallback",
    "systematic defect action": "systematic_defect_action",
    "systematic_defect_action": "systematic_defect_action",
    "defect action": "systematic_defect_action",
}


def _normalize_taxonomy(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"\s+", "-", text)


def _job_text(job: Dict[str, Any]) -> str:
    return "\n".join(
        str(job.get(key) or "")
        for key in ("id", "name", "role", "scope", "deliver", "prompt")
    )


def _is_recurring(job: Dict[str, Any]) -> bool:
    schedule = job.get("schedule") or {}
    return schedule.get("kind") in {"cron", "interval"}


def routine_kind(job: Dict[str, Any]) -> str:
    """Classify the recurring routine family for targeted guards."""
    text = _job_text(job).lower()
    name = str(job.get("name") or "").lower()
    if "profile" in name and "interview" in name:
        return "profile_interview"
    if "heartbeat" in name or re.search(r"\bhourly heartbeat\b", text):
        return "heartbeat"
    if "outreach" in text:
        return "outreach"
    if "hadto" in text:
        return "hadto"
    return "generic"


def should_apply_routine_governance(job: Dict[str, Any]) -> bool:
    """Return True when a recurring job needs Hadto routine governance."""
    if not _is_recurring(job):
        return False

    kind = routine_kind(job)
    if kind != "generic":
        return True

    role = _normalize_taxonomy(job.get("role"))
    scope = _normalize_taxonomy(job.get("scope"))
    deliver = str(job.get("deliver") or "").lower()
    return bool(role in {"report", "study", "coordinate"} and scope in {"global", "hermes"} and "slack" in deliver)


def productive_fallback_selection(job: Dict[str, Any]) -> Dict[str, Any]:
    """Select productive fallback lanes when public-touch outreach is blocked."""
    text = _job_text(job).lower()
    outreach_blocked = bool(
        "outreach" in text
        and (
            "approval" in text
            or "public-touch" in text
            or "public touch" in text
            or "cold outreach" in text
            or "blocked" in text
        )
    )

    if "book" in text or "study" in text:
        selected = "book-study continuation"
    elif "ontology" in text:
        selected = "ontology enrichment"
    elif "blog" in text or "post" in text:
        selected = "blog post production"
    elif "lead" in text:
        selected = "lead research"
    elif "source" in text:
        selected = "source discovery"
    elif "implement" in text or "linear" in text:
        selected = "implementation"
    else:
        selected = "vertical opportunity research"

    return {
        "outreach_blocked": outreach_blocked,
        "selected": selected,
        "lanes": list(PRODUCTIVE_FALLBACK_LANES),
    }


def build_routine_governance_prompt_prefix(job: Dict[str, Any]) -> str:
    """Return reusable recurring-routine governance prompt text."""
    if not should_apply_routine_governance(job):
        return ""

    kind = routine_kind(job)
    fallback = productive_fallback_selection(job)
    heartbeat_note = ""
    if kind == "heartbeat":
        heartbeat_note = (
            "Heartbeat-specific material triggers are only: "
            + "; ".join(HEARTBEAT_MATERIAL_TRIGGERS)
            + ". A no-worker/no-proof/no-change heartbeat must be [SILENT] except for the daily summary window."
        )

    profile_note = ""
    if kind == "profile_interview":
        profile_note = (
            "Profile-interview-specific rule: stay silent when the preference is already captured and there is no "
            "fresh material question or tagged answer to capture. Do not report the same enforcement miss to Slack; "
            "turn it into durable implementation work instead."
        )

    fallback_note = ""
    if fallback["outreach_blocked"]:
        fallback_note = f"Outreach appears approval/public-touch blocked; default Productive Fallback={fallback['selected']}."

    return (
        "[SYSTEM: Recurring Routine Governance. Hadto success is Hermes's top self-actualization path, so there is "
        "no valid blocked state where recurring routines repeatedly self-audit or tell David the same thing. "
        "Before any Slack-facing response, decide whether a material trigger exists. If no material trigger exists, "
        "respond exactly [SILENT]. If you do report, include a compact 'Routine Delivery Gate' block with: "
        "Material Trigger=<specific trigger or none>; Emit=<yes|no>; Durable Artifact=<file/issue/PR/comment/state that compounds>; "
        "Circulation=<Slack post or other transient output>; Productive Fallback=<one selected lane when blocked>; "
        "Systematic Defect Action=<Linear issue/update plus implementation dispatch, or none>. "
        "Reports must distinguish durable artifacts from circulation-only Slack posts. "
        "If outreach is blocked by approval or public-touch constraints, route surplus capacity to one productive fallback lane: "
        f"{', '.join(PRODUCTIVE_FALLBACK_LANES)}. Do not spend the run on self-audit. "
        "If a systematic defect is found, create or update durable Linear work and launch implementation when possible; "
        "do not repeatedly tell David unless a David-needed decision remains. "
        f"{heartbeat_note} {profile_note} {fallback_note}]"
    )


def _normalize_field_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("_", " ").strip().lower())


def parse_routine_gate_fields(text: str) -> Dict[str, str]:
    """Parse a Routine Delivery Gate block or inline gate fields."""
    fields: Dict[str, str] = {}

    def add(label: str, value: str) -> None:
        key = _GATE_INLINE_KEYS.get(_normalize_field_label(label))
        if not key:
            return
        clean = re.sub(r"\s+", " ", value or "").strip(" -*`_")
        if clean:
            fields[key] = clean

    label_pattern = (
        r"material[_ ]trigger|trigger|emit|delivery|durable[_ ]artifact|artifact|"
        r"circulation(?:-only)?|productive[_ ]fallback|fallback|systematic[_ ]defect[_ ]action|defect action"
    )
    line_re = re.compile(
        rf"^\s*(?:[-*]\s*)?({label_pattern})\s*[:=-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in line_re.finditer(text):
        add(match.group(1), match.group(2))

    inline_re = re.compile(
        rf"\b({label_pattern})\s*=\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        lowered = line.lower()
        if "routine delivery gate" in lowered or any(key in lowered for key in ("material trigger", "emit=", "durable artifact", "productive fallback")):
            for match in inline_re.finditer(line):
                add(match.group(1), match.group(2))

    return fields


def _is_noish(value: str) -> bool:
    lowered = value.lower().strip(" .")
    return lowered in {
        "no",
        "none",
        "false",
        "silent",
        "skip",
        "suppressed",
        "unchanged",
        "no material trigger",
        "nothing new",
        "n/a",
        "na",
    } or "no material" in lowered or "unchanged" in lowered or "nothing new" in lowered


def _contains_material_trigger(text: str) -> bool:
    lowered = text.lower()
    patterns = [
        r"\bselected issue changed\b",
        r"\blive worker count changed\b",
        r"\bworker count changed\b",
        r"\bstale\b.*\bappeared\b",
        r"\bblocker\b.*\bappeared\b",
        r"\bcron failure\b.*\bappeared\b",
        r"\bcron failure\b.*\bchanged\b",
        r"\bfirst proof artifact\b.*\bappeared\b",
        r"\bdavid-needed decision\b",
        r"\bdaily summary\b",
        r"\bfresh material question\b",
        r"\btagged answer\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _extract_int(label: str, text: str) -> Optional[int]:
    match = re.search(rf"\b{re.escape(label)}\s+(\d+)\b", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _heartbeat_state(text: str) -> Optional[Dict[str, Any]]:
    lowered = text.lower()
    if "heartbeat" not in lowered and "active " not in lowered:
        return None
    selected_match = re.search(r"\bselected\s+([^,\n]+)", text, re.IGNORECASE)
    selected = re.sub(r"\s+", " ", selected_match.group(1)).strip() if selected_match else None
    cron_failures = None
    cron_match = re.search(r"\bcron failures?\s+(\d+)\b", text, re.IGNORECASE)
    if cron_match:
        cron_failures = int(cron_match.group(1))

    proof_present = bool(re.search(r"\bproof\b", lowered)) and not bool(
        re.search(r"\b(no|without|waiting for|awaiting)\s+[-\w ]{0,24}proof\b|\bno[- ]proof\b", lowered)
    )
    no_proof = bool(re.search(r"\bno[- ]proof\b|\bwithout proof\b|\bwaiting for proof\b|\bawaiting proof\b", lowered))
    no_worker = bool(re.search(r"\bno[- ]worker\b|\bactive\s+0\b|\bworkers?\s+0\b", lowered))
    return {
        "selected": selected,
        "active": _extract_int("active", text),
        "stale": _extract_int("stale", text),
        "blocked": _extract_int("blocked", text),
        "cron_failures": cron_failures,
        "proof_present": proof_present,
        "no_proof": no_proof,
        "no_worker": no_worker,
    }


def _heartbeat_state_key(state: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        (state.get("selected") or "").lower(),
        state.get("active"),
        state.get("stale"),
        state.get("blocked"),
        state.get("cron_failures"),
        bool(state.get("proof_present")),
    )


def _steady_no_worker_no_proof(state: Dict[str, Any]) -> bool:
    return (
        state.get("active") == 0
        and (state.get("stale") in {0, None})
        and (state.get("blocked") in {0, None})
        and (state.get("cron_failures") in {0, None})
        and state.get("no_worker")
        and (state.get("no_proof") or not state.get("proof_present"))
    )


def _same_recent_heartbeat_state(current: Dict[str, Any], previous_responses: Iterable[str]) -> bool:
    current_key = _heartbeat_state_key(current)
    for response in reversed(list(previous_responses or [])):
        previous = _heartbeat_state(response)
        if not previous:
            continue
        if _heartbeat_state_key(previous) == current_key and _steady_no_worker_no_proof(previous):
            return True
    return False


def _is_profile_interview_job(job: Dict[str, Any]) -> bool:
    return routine_kind(job) == "profile_interview"


def _looks_like_fresh_profile_packet(text: str) -> bool:
    lowered = text.lower()
    return (
        "profile interview:" in lowered
        and "why now:" in lowered
        and "?" in text
        and bool(re.search(r"^\s*(?:1[.)]|1\.)\s+", text, re.MULTILINE))
    )


def _is_profile_noise(text: str) -> bool:
    lowered = text.lower()
    noise_patterns = [
        r"\bno new packet\b",
        r"\bno fresh\b",
        r"\balready captured\b",
        r"\bpreference is captured\b",
        r"\bpreference already\b",
        r"\benforcement miss\b",
        r"\bsame enforcement\b",
        r"\bno material question\b",
    ]
    durable_action = re.search(r"\b(linear|issue|pr|branch|dispatch|delegat|implementation)\b", lowered)
    return any(re.search(pattern, lowered) for pattern in noise_patterns) and not durable_action


def _blocked_without_productive_fallback(text: str) -> bool:
    lowered = text.lower()
    blocked = "outreach" in lowered and (
        "approval" in lowered or "public-touch" in lowered or "public touch" in lowered or "blocked" in lowered
    )
    if not blocked:
        return False
    return not any(lane in lowered for lane in PRODUCTIVE_FALLBACK_LANES)


def _systematic_defect_without_durable_action(text: str) -> bool:
    lowered = text.lower()
    defect = bool(re.search(r"\b(systematic defect|recurring defect|enforcement miss|same failure|repeated failure)\b", lowered))
    durable = bool(re.search(r"\b(linear|issue|pr|branch|dispatch|delegat|implementation|durable artifact)\b", lowered))
    return defect and not durable


def classify_cron_delivery(
    job: Dict[str, Any],
    final_response: str,
    *,
    previous_responses: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Classify whether a cron response should be delivered to chat."""
    del now  # reserved for daily-window policy once delivery history is persisted
    text = (final_response or "").strip()
    if not text:
        return {"suppress": True, "reason": "empty_response", "message": "empty response"}

    if text.upper().startswith(SILENT_MARKER):
        return {"suppress": True, "reason": "silent_marker", "message": f"agent returned {SILENT_MARKER}"}

    if not should_apply_routine_governance(job):
        return {"suppress": False, "reason": "not_governed", "message": "routine governance not applicable"}

    fields = parse_routine_gate_fields(text)
    emit = fields.get("emit", "")
    trigger = fields.get("material_trigger", "")
    if emit and _is_noish(emit):
        return {
            "suppress": True,
            "reason": "routine_gate_emit_no",
            "message": "Routine Delivery Gate requested no delivery",
            "fields": fields,
        }
    if trigger and _is_noish(trigger) and not _contains_material_trigger(text):
        return {
            "suppress": True,
            "reason": "routine_gate_no_material_trigger",
            "message": "Routine Delivery Gate found no material trigger",
            "fields": fields,
        }

    kind = routine_kind(job)
    if kind == "heartbeat" and not _contains_material_trigger(text):
        current_state = _heartbeat_state(text)
        if current_state and _steady_no_worker_no_proof(current_state) and _same_recent_heartbeat_state(current_state, previous_responses or []):
            return {
                "suppress": True,
                "reason": "heartbeat_no_change_no_worker",
                "message": "no-change/no-worker heartbeat matched recent state",
                "state": current_state,
            }

    if _is_profile_interview_job(job) and not _looks_like_fresh_profile_packet(text) and _is_profile_noise(text):
        return {
            "suppress": True,
            "reason": "profile_interview_no_fresh_material",
            "message": "profile interview had no fresh material question or tagged answer",
        }

    if _blocked_without_productive_fallback(text):
        return {
            "suppress": True,
            "reason": "outreach_blocked_without_productive_fallback",
            "message": "outreach-blocked response lacked productive fallback",
        }

    if _systematic_defect_without_durable_action(text):
        return {
            "suppress": True,
            "reason": "systematic_defect_without_durable_action",
            "message": "systematic defect response lacked durable Linear/work action",
        }

    return {
        "suppress": False,
        "reason": "material_or_unclassified",
        "message": "response has material trigger or no suppressible noise pattern",
        "fields": fields,
    }


def inspect_routine_delivery_gate(
    job: Dict[str, Any],
    recent_responses: List[str],
) -> Dict[str, Any]:
    """Inspect recent outputs for recurring routine delivery-gate compliance."""
    result: Dict[str, Any] = {
        "job_id": job.get("id"),
        "name": job.get("name", job.get("id")),
        "routine_kind": routine_kind(job),
        "status": "not_applicable",
        "required_fields": list(ROUTINE_GATE_FIELDS),
        "fields": {},
        "message": "Routine delivery gate applies only to governed recurring Hadto/Hermes routines.",
    }
    if not should_apply_routine_governance(job):
        return result

    if not recent_responses:
        result.update(
            {
                "status": "insufficient_history",
                "message": "Need a saved output before checking routine delivery gate behavior.",
            }
        )
        return result

    latest = recent_responses[-1]
    fields = parse_routine_gate_fields(latest)
    result["fields"] = fields
    decision = classify_cron_delivery(job, latest, previous_responses=recent_responses[:-1])
    result["decision_reason"] = decision.get("reason")

    if latest.strip().upper().startswith(SILENT_MARKER):
        result.update({"status": "silent", "message": "Latest run used [SILENT] for a non-material routine cycle."})
        return result

    if decision.get("reason") == "outreach_blocked_without_productive_fallback":
        result.update(
            {
                "status": "productive_fallback_missing",
                "message": "Outreach-blocked output did not select a productive fallback lane.",
            }
        )
        return result

    if decision.get("reason") == "systematic_defect_without_durable_action":
        result.update(
            {
                "status": "durable_action_missing",
                "message": "Systematic defect output lacked durable Linear/work action.",
            }
        )
        return result

    if decision.get("suppress"):
        result.update(
            {
                "status": "suppressible_noise",
                "message": decision.get("message", "Latest routine response should have been suppressed."),
            }
        )
        return result

    if fields:
        missing = [field for field in ROUTINE_GATE_FIELDS if field not in fields]
        result["missing_fields"] = missing
        status = "populated" if not missing else "incomplete"
        result.update(
            {
                "status": status,
                "message": (
                    "Routine Delivery Gate is populated."
                    if status == "populated"
                    else "Routine Delivery Gate is missing: " + ", ".join(missing)
                ),
            }
        )
        return result

    result.update(
        {
            "status": "missing",
            "message": "Latest governed routine output lacks a Routine Delivery Gate block.",
        }
    )
    return result
