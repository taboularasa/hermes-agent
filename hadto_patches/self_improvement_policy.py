"""Guardrails for Hermes-created self-improvement work."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


def _normalize(value: Optional[str]) -> str:
    return str(value or "").strip().lower().replace("_", "-")


@dataclass(frozen=True)
class SelfImprovementWorkItem:
    """Candidate or active self-improvement issue/project."""

    title: str
    lane: str
    kind: str = "issue"
    evidence_sources: List[str] = field(default_factory=list)
    verification_target: Optional[str] = None
    status: str = "backlog"


@dataclass(frozen=True)
class PortfolioHealthSnapshot:
    """Health signals used by the capability-work budget gate."""

    maintenance_healthy: bool = True
    growth_healthy: bool = True
    maintenance_reason: Optional[str] = None
    growth_reason: Optional[str] = None


@dataclass(frozen=True)
class SelfImprovementPolicyConfig:
    """Configurable guardrails for self-generated work."""

    max_active_per_lane: int = 1
    capability_lane_names: tuple[str, ...] = ("capability",)
    active_status_names: tuple[str, ...] = (
        "backlog",
        "planned",
        "todo",
        "unstarted",
        "started",
        "in-progress",
        "in progress",
    )


@dataclass(frozen=True)
class SelfImprovementPolicyDecision:
    """Machine-checkable decision for self-improvement work creation/update."""

    allowed: bool
    reasons: List[str]
    lane: str
    kind: str
    active_lane_wip: int
    lane_wip_cap: int
    budget_rule_applies: bool
    budget_rule_passed: bool
    required_fields: List[str]


def _normalize_sources(sources: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    for source in sources:
        text = str(source or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _is_active(status: str, config: SelfImprovementPolicyConfig) -> bool:
    return _normalize(status) in {_normalize(item) for item in config.active_status_names}


def _is_capability_lane(lane: str, config: SelfImprovementPolicyConfig) -> bool:
    normalized_lane = _normalize(lane)
    return normalized_lane in {_normalize(item) for item in config.capability_lane_names}


def evaluate_self_improvement_policy(
    candidate: SelfImprovementWorkItem,
    *,
    active_items: Iterable[SelfImprovementWorkItem] = (),
    portfolio_health: Optional[PortfolioHealthSnapshot] = None,
    config: Optional[SelfImprovementPolicyConfig] = None,
) -> SelfImprovementPolicyDecision:
    """Evaluate evidence, WIP, and budget guardrails for self-created work."""

    policy = config or SelfImprovementPolicyConfig()
    health = portfolio_health or PortfolioHealthSnapshot()
    lane = _normalize(candidate.lane)
    kind = _normalize(candidate.kind) or "issue"
    evidence_sources = _normalize_sources(candidate.evidence_sources)
    verification_target = str(candidate.verification_target or "").strip()
    active_lane_wip = sum(
        1
        for item in active_items
        if _normalize(item.lane) == lane and _is_active(item.status, policy)
    )

    reasons: List[str] = []
    required_fields = ["lane", "evidence_sources", "verification_target"]

    if kind not in {"issue", "project"}:
        reasons.append("self_improvement_kind_must_be_issue_or_project")
    if not lane:
        reasons.append("self_improvement_lane_required")
    if not evidence_sources:
        reasons.append("self_improvement_evidence_required")
    if not verification_target:
        reasons.append("self_improvement_verification_target_required")
    if active_lane_wip >= policy.max_active_per_lane:
        reasons.append("self_improvement_lane_wip_cap_reached")

    budget_rule_applies = _is_capability_lane(lane, policy)
    budget_rule_passed = True
    if budget_rule_applies and (not health.maintenance_healthy or not health.growth_healthy):
        budget_rule_passed = False
        reasons.append("self_improvement_capability_budget_blocked")

    return SelfImprovementPolicyDecision(
        allowed=not reasons,
        reasons=reasons,
        lane=lane,
        kind=kind,
        active_lane_wip=active_lane_wip,
        lane_wip_cap=policy.max_active_per_lane,
        budget_rule_applies=budget_rule_applies,
        budget_rule_passed=budget_rule_passed,
        required_fields=required_fields,
    )


def build_self_improvement_writeback_contract(
    config: Optional[SelfImprovementPolicyConfig] = None,
) -> str:
    """Return prompt text for durable Linear writeback guardrails."""

    policy = config or SelfImprovementPolicyConfig()
    cap_names = ", ".join(policy.capability_lane_names)
    return (
        "Any Hermes-created self-improvement issue or project must satisfy all of these hard guardrails before writeback: "
        "1) include at least one concrete Evidence Source, "
        "2) include one explicit Verification Target, "
        "3) include the target Lane, "
        f"4) do not create or expand work when that lane already has {policy.max_active_per_lane} active Hermes-owned self-improvement item(s), and "
        f"5) treat lane(s) [{cap_names}] as capability-budgeted work that may be created only when maintenance and growth are both healthy. "
        "If any guardrail fails, do not create the issue/project; instead report the blocking reason and preserve the evidence in the current durable artifact."
    )


def render_self_improvement_metadata(
    candidate: SelfImprovementWorkItem,
    decision: Optional[SelfImprovementPolicyDecision] = None,
) -> str:
    """Render the minimum audit block required for self-created work."""

    policy_decision = decision or evaluate_self_improvement_policy(candidate)
    evidence = _normalize_sources(candidate.evidence_sources)
    verification_target = str(candidate.verification_target or "").strip() or "[missing]"
    budget_line = "not_applicable"
    if policy_decision.budget_rule_applies:
        budget_line = "passed" if policy_decision.budget_rule_passed else "blocked"

    return "\n".join(
        [
            "Self-Improvement Guardrails:",
            f"- Lane: {candidate.lane or '[missing]'}",
            f"- Evidence Sources: {', '.join(evidence) if evidence else '[missing]'}",
            f"- Verification Target: {verification_target}",
            f"- Lane WIP: {policy_decision.active_lane_wip}/{policy_decision.lane_wip_cap}",
            f"- Budget Rule: {budget_line}",
        ]
    )
