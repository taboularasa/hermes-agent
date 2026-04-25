"""Canonical ownership policy for backlog selection and Linear writeback."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
import threading
from typing import Iterator, Optional


_DENIED_PROJECTS = {"de novo", "denovo"}
_TERMINAL_STATES = {"done", "completed", "closed", "canceled", "cancelled"}
_THREAD_LOCAL = threading.local()


@dataclass(frozen=True)
class OwnershipPolicyOverride:
    """Explicit operator override scoped to the current thread."""

    reason: str


@dataclass(frozen=True)
class IssueOwnershipFacts:
    """Facts needed to decide issue selection and ownership writes."""

    project_name: Optional[str] = None
    state_name: Optional[str] = None
    state_type: Optional[str] = None
    issue_type: Optional[str] = None
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_email: Optional[str] = None
    assignee_is_human: bool = False
    delegate_id: Optional[str] = None
    delegate_name: Optional[str] = None
    delegate_is_hermes: bool = False
    issue_text_has_guard: bool = False
    issue_text_guard_reason: Optional[str] = None
    explicit_override: bool = False
    explicit_override_reason: Optional[str] = None


@dataclass(frozen=True)
class OwnershipDimensionDecision:
    allowed: bool
    reason: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class OwnershipPolicyDecision:
    selectable: OwnershipDimensionDecision
    commentable: OwnershipDimensionDecision
    ownable: OwnershipDimensionDecision
    override_reason: Optional[str] = None


def _normalize(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _override_stack() -> list[OwnershipPolicyOverride]:
    stack = getattr(_THREAD_LOCAL, "override_stack", None)
    if stack is None:
        stack = []
        _THREAD_LOCAL.override_stack = stack
    return stack


def current_ownership_policy_override() -> Optional[OwnershipPolicyOverride]:
    stack = _override_stack()
    return stack[-1] if stack else None


@contextmanager
def ownership_policy_override(reason: str) -> Iterator[OwnershipPolicyOverride]:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("ownership policy override requires a reason")
    override = OwnershipPolicyOverride(reason=clean_reason)
    stack = _override_stack()
    stack.append(override)
    try:
        yield override
    finally:
        stack.pop()


def _input_override(facts: IssueOwnershipFacts) -> Optional[OwnershipPolicyOverride]:
    if not facts.explicit_override:
        return None
    reason = str(facts.explicit_override_reason or "").strip()
    if not reason:
        reason = "explicit ownership policy override"
    return OwnershipPolicyOverride(reason=reason)


def _active_override(facts: IssueOwnershipFacts) -> Optional[OwnershipPolicyOverride]:
    return _input_override(facts) or current_ownership_policy_override()


def evaluate_ownership_policy(facts: IssueOwnershipFacts) -> OwnershipPolicyDecision:
    """Return independent selectable/commentable/ownable decisions for an issue."""

    override = _active_override(facts)
    project = _normalize(facts.project_name)
    state_values = [
        value
        for value in (
            _normalize(facts.state_type),
            _normalize(facts.state_name),
            _normalize(facts.issue_type),
        )
        if value
    ]
    terminal_state = next(
        (value for value in state_values if value in _TERMINAL_STATES),
        None,
    )
    is_de_novo = project in _DENIED_PROJECTS
    human_owned = facts.assignee_is_human and not facts.delegate_is_hermes

    commentable = OwnershipDimensionDecision(True, "status_comments_allowed")

    if facts.issue_text_has_guard:
        detail = facts.issue_text_guard_reason
        return OwnershipPolicyDecision(
            selectable=OwnershipDimensionDecision(False, "issue_text_guard", detail),
            commentable=commentable,
            ownable=OwnershipDimensionDecision(False, "issue_text_guard", detail),
            override_reason=override.reason if override else None,
        )

    if terminal_state:
        return OwnershipPolicyDecision(
            selectable=OwnershipDimensionDecision(False, "terminal_state", terminal_state),
            commentable=commentable,
            ownable=OwnershipDimensionDecision(False, "terminal_state", terminal_state),
            override_reason=override.reason if override else None,
        )

    if human_owned:
        return OwnershipPolicyDecision(
            selectable=OwnershipDimensionDecision(False, "human_owned", facts.assignee_name),
            commentable=commentable,
            ownable=OwnershipDimensionDecision(False, "human_owned", facts.assignee_name),
            override_reason=override.reason if override else None,
        )

    if is_de_novo and override is None:
        return OwnershipPolicyDecision(
            selectable=OwnershipDimensionDecision(False, "de_novo_project", facts.project_name),
            commentable=commentable,
            ownable=OwnershipDimensionDecision(False, "de_novo_project", facts.project_name),
            override_reason=None,
        )

    reason = "explicit_override" if is_de_novo and override else "normal_backlog"
    if facts.delegate_is_hermes:
        reason = "hermes_delegate"
    return OwnershipPolicyDecision(
        selectable=OwnershipDimensionDecision(True, reason),
        commentable=commentable,
        ownable=OwnershipDimensionDecision(True, reason),
        override_reason=override.reason if override else None,
    )
