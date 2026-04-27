from hadto_patches.self_improvement_policy import (
    PortfolioHealthSnapshot,
    SelfImprovementWorkItem,
    build_self_improvement_writeback_contract,
    evaluate_self_improvement_policy,
    render_self_improvement_metadata,
)


def test_self_improvement_requires_evidence_and_verification_target():
    decision = evaluate_self_improvement_policy(
        SelfImprovementWorkItem(
            title="Tighten delegation heuristics",
            lane="maintenance",
            kind="issue",
        )
    )

    assert decision.allowed is False
    assert "self_improvement_evidence_required" in decision.reasons
    assert "self_improvement_verification_target_required" in decision.reasons


def test_self_improvement_enforces_lane_wip_cap():
    active_items = [
        SelfImprovementWorkItem(
            title="Existing maintenance item",
            lane="maintenance",
            status="in progress",
            evidence_sources=["HAD-111"],
            verification_target="existing check",
        )
    ]

    decision = evaluate_self_improvement_policy(
        SelfImprovementWorkItem(
            title="Second maintenance item",
            lane="maintenance",
            evidence_sources=["HAD-133"],
            verification_target="cron topology stays clean",
        ),
        active_items=active_items,
    )

    assert decision.allowed is False
    assert decision.active_lane_wip == 1
    assert decision.lane_wip_cap == 1
    assert "self_improvement_lane_wip_cap_reached" in decision.reasons


def test_capability_work_blocked_when_maintenance_or_growth_unhealthy():
    decision = evaluate_self_improvement_policy(
        SelfImprovementWorkItem(
            title="Expand autonomous planning",
            lane="capability",
            evidence_sources=["runbook gap"],
            verification_target="new verifier catches planning regressions",
        ),
        portfolio_health=PortfolioHealthSnapshot(
            maintenance_healthy=False,
            growth_healthy=True,
        ),
    )

    assert decision.allowed is False
    assert decision.budget_rule_applies is True
    assert decision.budget_rule_passed is False
    assert "self_improvement_capability_budget_blocked" in decision.reasons


def test_healthy_maintenance_item_with_evidence_is_allowed():
    decision = evaluate_self_improvement_policy(
        SelfImprovementWorkItem(
            title="Require evidence on self-created work",
            lane="maintenance",
            evidence_sources=["HAD-133", "cron output 2026-04-26"],
            verification_target="self-created work without evidence is rejected",
        )
    )

    assert decision.allowed is True
    assert decision.reasons == []


def test_render_metadata_includes_required_audit_fields():
    candidate = SelfImprovementWorkItem(
        title="Guardrail writeback",
        lane="maintenance",
        evidence_sources=["HAD-133"],
        verification_target="prompt requires guardrail block",
    )

    metadata = render_self_improvement_metadata(candidate)

    assert "Self-Improvement Guardrails:" in metadata
    assert "- Lane: maintenance" in metadata
    assert "- Evidence Sources: HAD-133" in metadata
    assert "- Verification Target: prompt requires guardrail block" in metadata
    assert "- Lane WIP: 0/1" in metadata


def test_writeback_contract_mentions_evidence_wip_and_budget_rules():
    contract = build_self_improvement_writeback_contract()

    assert "Evidence Source" in contract
    assert "Verification Target" in contract
    assert "lane already has 1 active Hermes-owned self-improvement item" in contract
    assert "maintenance and growth are both healthy" in contract
