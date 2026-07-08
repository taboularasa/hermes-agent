"""Diagnostics for self-improvement Linear planning metadata.

The active self-improvement benchmark is plugin-owned, but the diagnostic
contract is small and repo-owned: a planning surface below 1.0 must name the
missing Linear fields and the candidate issues that caused the partial score.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


LINEAR_PLANNING_SURFACE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "active_status_comment": (
        "active_status_comment",
        "activeStatusComment",
        "latest_status_comment",
        "latestStatusComment",
        "status_comment",
        "statusComment",
        "status_comments",
        "statusComments",
        "comments",
    ),
    "lane": (
        "lane",
        "lane_name",
        "laneName",
        "priority_lane",
        "priorityLane",
        "team",
        "team_name",
        "teamName",
    ),
    "verification": (
        "verification",
        "verification_expectation",
        "verificationExpectation",
        "verification_plan",
        "verificationPlan",
        "verification_targets",
        "verificationTargets",
    ),
}

LINEAR_PLANNING_SURFACE_SAMPLE_LIMIT = 5

_BACKLOG_CANDIDATE_ID_KEYS = (
    "id",
    "candidate_id",
    "candidateId",
    "issue_id",
    "issueId",
    "linear_issue_id",
    "linearIssueId",
    "linear_id",
    "linearId",
    "activeLinearIssueIds",
    "active_linear_issue_ids",
    "identifier",
    "key",
)


def _candidate_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _coerce_candidate_records(backlog_candidates: Any) -> list[dict[str, Any]]:
    if backlog_candidates is None:
        return []
    if isinstance(backlog_candidates, dict):
        for key in ("backlog_candidates", "candidates", "issues", "items"):
            child = backlog_candidates.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
        return [backlog_candidates]
    if isinstance(backlog_candidates, list):
        return [item for item in backlog_candidates if isinstance(item, dict)]
    return []


def _candidate_identifier(candidate: dict[str, Any]) -> str:
    for key in _BACKLOG_CANDIDATE_ID_KEYS:
        value = candidate.get(key)
        if isinstance(value, list):
            value = next((_candidate_string(item) for item in value if _candidate_string(item)), "")
        text = _candidate_string(value)
        if text:
            return text
    return ""


def _candidate_title(candidate: dict[str, Any]) -> str:
    for key in ("title", "summary", "name"):
        text = _candidate_string(candidate.get(key))
        if text:
            return text
    return _candidate_identifier(candidate) or "Untitled backlog candidate"


def _candidate_planning_field_present(
    candidate: dict[str, Any],
    aliases: Iterable[str],
) -> bool:
    for alias in aliases:
        value = candidate.get(alias)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(
            _candidate_planning_field_present({"value": item}, ("value",))
            for item in value
        ):
            return True
        if isinstance(value, dict) and any(
            _candidate_string(value.get(key))
            for key in (
                "body",
                "comment",
                "content",
                "description",
                "name",
                "text",
                "title",
                "value",
            )
        ):
            return True
    return False


def _missing_field_detail(
    field: str,
    missing_candidates: list[dict[str, Any]],
    missing_count: int,
) -> dict[str, Any]:
    return {
        "field": field,
        "expected_aliases": list(LINEAR_PLANNING_SURFACE_FIELD_ALIASES[field]),
        "missing_count": missing_count,
        "sample_candidates": [
            {
                "candidate_id": _candidate_identifier(candidate),
                "title": _candidate_title(candidate),
            }
            for candidate in missing_candidates[:LINEAR_PLANNING_SURFACE_SAMPLE_LIMIT]
        ],
    }


def _actionable_cause(
    field: str,
    missing_candidates: list[dict[str, Any]],
    missing_count: int,
) -> dict[str, Any]:
    aliases = list(LINEAR_PLANNING_SURFACE_FIELD_ALIASES[field])
    sample_candidate_ids = [
        _candidate_identifier(candidate)
        for candidate in missing_candidates[:LINEAR_PLANNING_SURFACE_SAMPLE_LIMIT]
    ]
    sample_candidate_ids = [candidate_id for candidate_id in sample_candidate_ids if candidate_id]
    return {
        "cause": "missing_linear_planning_field",
        "field": field,
        "missing_count": missing_count,
        "sample_candidate_ids": sample_candidate_ids,
        "accepted_aliases": aliases,
        "action": (
            f"Populate one of {', '.join(aliases)} on the Linear backlog "
            "candidate metadata."
        ),
    }


def build_linear_planning_surface(backlog_candidates: Any) -> dict[str, Any]:
    """Build benchmark-ready diagnostics for Linear planning metadata."""

    candidates = _coerce_candidate_records(backlog_candidates)
    required_fields = sorted(LINEAR_PLANNING_SURFACE_FIELD_ALIASES)
    missing_field_counts = {field: 0 for field in required_fields}
    missing_candidates_by_field: dict[str, list[dict[str, Any]]] = {
        field: [] for field in required_fields
    }
    issue_samples: list[dict[str, Any]] = []

    for candidate in candidates:
        missing_fields = [
            field
            for field in required_fields
            if not _candidate_planning_field_present(
                candidate,
                LINEAR_PLANNING_SURFACE_FIELD_ALIASES[field],
            )
        ]
        for field in missing_fields:
            missing_field_counts[field] += 1
            missing_candidates_by_field[field].append(candidate)
        if missing_fields and len(issue_samples) < LINEAR_PLANNING_SURFACE_SAMPLE_LIMIT:
            issue_samples.append(
                {
                    "candidate_id": _candidate_identifier(candidate),
                    "title": _candidate_title(candidate),
                    "missing_fields": missing_fields,
                    "expected_aliases": {
                        field: list(LINEAR_PLANNING_SURFACE_FIELD_ALIASES[field])
                        for field in missing_fields
                    },
                }
            )

    expected_field_count = len(candidates) * len(required_fields)
    missing_field_count = sum(missing_field_counts.values())
    score = (
        1.0
        if expected_field_count == 0
        else round((expected_field_count - missing_field_count) / expected_field_count, 4)
    )
    return {
        "surface": "linear_planning_surface",
        "status": "pass" if missing_field_count == 0 else "partial",
        "score": score,
        "candidate_count": len(candidates),
        "required_fields": required_fields,
        "missing_field_counts": {
            field: count for field, count in missing_field_counts.items() if count
        },
        "missing_field_details": [
            _missing_field_detail(
                field,
                missing_candidates_by_field[field],
                missing_field_counts[field],
            )
            for field in required_fields
            if missing_field_counts[field]
        ],
        "actionable_causes": [
            _actionable_cause(
                field,
                missing_candidates_by_field[field],
                missing_field_counts[field],
            )
            for field in required_fields
            if missing_field_counts[field]
        ],
        "issue_samples": issue_samples,
        "issue_sample_limit": LINEAR_PLANNING_SURFACE_SAMPLE_LIMIT,
        "detail": (
            "No Linear backlog candidates were provided."
            if not candidates
            else (
                "Linear backlog candidates expose lane, verification, and active "
                "status-comment planning fields."
            )
            if missing_field_count == 0
            else (
                "Linear backlog candidates are missing planning fields; inspect "
                "missing_field_details and issue_samples for exact candidates, fields, "
                "and accepted aliases."
            )
        ),
    }


def format_linear_planning_surface_summary(surface: dict[str, Any]) -> str:
    """Return a compact benchmark summary naming missing fields and candidates."""

    missing_fields = surface.get("missing_field_counts") or {}
    if not missing_fields:
        return ""

    details = {
        str(detail.get("field")): detail
        for detail in surface.get("missing_field_details") or []
        if isinstance(detail, dict)
    }
    parts = []
    for field in sorted(missing_fields):
        detail = details.get(field) or {}
        candidates = [
            str(sample.get("candidate_id") or sample.get("title") or "").strip()
            for sample in detail.get("sample_candidates") or []
            if isinstance(sample, dict)
        ]
        candidates = [candidate for candidate in candidates if candidate]
        suffix = f" ({', '.join(candidates)})" if candidates else ""
        parts.append(f"{field}={missing_fields[field]}{suffix}")
    return "- linear_planning_surface_missing=" + ", ".join(parts)
