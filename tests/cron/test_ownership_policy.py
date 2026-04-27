import pytest

from hadto_patches.ownership_policy import (
    IssueOwnershipFacts,
    build_ownership_decision_audit_records,
    canonical_ownership_dedupe_key,
    evaluate_ownership_policy,
    format_ownership_decision_audit,
    ownership_policy_override,
)


@pytest.mark.parametrize(
    ("name", "facts", "expected"),
    [
        (
            "de_novo_default_deny",
            IssueOwnershipFacts(project_name="De Novo", state_type="backlog"),
            {"selectable": False, "commentable": True, "ownable": False},
        ),
        (
            "human_owned",
            IssueOwnershipFacts(
                project_name="Hadto.co",
                state_type="backlog",
                assignee_name="David",
                assignee_is_human=True,
            ),
            {"selectable": False, "commentable": True, "ownable": False},
        ),
        (
            "hermes_owned",
            IssueOwnershipFacts(
                project_name="Hadto.co",
                state_type="backlog",
                assignee_name="David",
                assignee_is_human=True,
                delegate_name="Hermes",
                delegate_is_hermes=True,
            ),
            {"selectable": True, "commentable": True, "ownable": True},
        ),
        (
            "normal_backlog",
            IssueOwnershipFacts(project_name="Hadto.co", state_type="backlog"),
            {"selectable": True, "commentable": True, "ownable": True},
        ),
    ],
)
def test_issue_ownership_policy_contract_table(name, facts, expected):
    decision = evaluate_ownership_policy(facts)

    assert decision.selectable.allowed is expected["selectable"], name
    assert decision.commentable.allowed is expected["commentable"], name
    assert decision.ownable.allowed is expected["ownable"], name


def test_policy_exposes_stable_operator_reason_strings():
    de_novo = evaluate_ownership_policy(IssueOwnershipFacts(project_name="De Novo", state_type="backlog"))
    human_owned = evaluate_ownership_policy(
        IssueOwnershipFacts(
            project_name="Hadto.co",
            state_type="backlog",
            assignee_name="David",
            assignee_is_human=True,
        )
    )
    normal = evaluate_ownership_policy(IssueOwnershipFacts(project_name="Hadto.co", state_type="backlog"))

    assert de_novo.selectable.reason == "de_novo_block"
    assert de_novo.delegateable.reason == "de_novo_block"
    assert de_novo.assignable.reason == "de_novo_block"
    assert human_owned.selectable.reason == "human_owned"
    assert normal.selectable.reason == "selected"
    assert normal.delegateable.reason == "delegate_allowed"
    assert normal.assignable.reason == "assign_allowed"


def test_issue_text_guard_blocks_selection_and_ownership_but_not_comments():
    decision = evaluate_ownership_policy(
        IssueOwnershipFacts(
            project_name="Hadto.co",
            state_type="backlog",
            issue_text_has_guard=True,
            issue_text_guard_reason="manual owner required",
        )
    )

    assert decision.selectable.allowed is False
    assert decision.commentable.allowed is True
    assert decision.ownable.allowed is False
    assert decision.ownable.reason == "issue_text_guard"


def test_explicit_thread_local_override_allows_de_novo_ownership_temporarily():
    facts = IssueOwnershipFacts(project_name="De Novo", state_type="backlog")

    assert evaluate_ownership_policy(facts).ownable.allowed is False

    with ownership_policy_override("HAD-511 operator-approved De Novo pickup"):
        decision = evaluate_ownership_policy(facts)

    assert decision.selectable.allowed is True
    assert decision.ownable.allowed is True
    assert decision.selectable.reason == "selected"
    assert decision.ownable.detail == "explicit_thread_override"
    assert decision.override_reason == "HAD-511 operator-approved De Novo pickup"
    assert evaluate_ownership_policy(facts).ownable.allowed is False


def test_explicit_input_override_allows_de_novo_ownership():
    decision = evaluate_ownership_policy(
        IssueOwnershipFacts(
            project_name="DeNovo",
            state_type="backlog",
            explicit_override=True,
            explicit_override_reason="HAD-511 manual review",
        )
    )

    assert decision.selectable.allowed is True
    assert decision.ownable.allowed is True
    assert decision.selectable.reason == "selected"
    assert decision.ownable.detail == "explicit_thread_override"
    assert decision.override_reason == "HAD-511 manual review"


def test_audit_records_distinguish_selected_execution_comment_and_denied_delegation():
    facts = IssueOwnershipFacts(
        issue_id="lin-1",
        issue_key="HAD-514",
        project_name="De Novo",
        state_type="backlog",
        planning_only=True,
    )

    records = build_ownership_decision_audit_records(
        facts,
        selected=False,
        comment_attempted=True,
        comment_written=True,
        delegate_attempted=True,
    )
    payloads = [record.to_dict() for record in records]

    assert payloads[0]["action"] == "select"
    assert payloads[0]["outcome"] == "denied"
    assert payloads[0]["reason"] == "de_novo_block"
    assert payloads[1]["action"] == "execute"
    assert payloads[1]["outcome"] == "skipped"
    assert payloads[1]["reason"] == "planning_only"
    assert payloads[2]["action"] == "comment"
    assert payloads[2]["outcome"] == "commented"
    assert payloads[2]["reason"] == "commented"
    assert payloads[3]["action"] == "delegate"
    assert payloads[3]["outcome"] == "skipped"
    assert payloads[3]["reason"] == "planning_only"
    assert payloads[3]["policy_reason"] == "delegate_denied"

    output = format_ownership_decision_audit(records)
    assert "issue=HAD-514" in output
    assert "dedupe_key=workspace-orchestrator:HAD-514" in output
    assert "action=select outcome=denied reason=de_novo_block" in output
    assert "action=execute outcome=skipped reason=planning_only" in output
    assert "action=delegate outcome=skipped reason=planning_only policy_reason=delegate_denied" in output


def test_audit_records_capture_allowed_delegate_assign_and_writeback_skip():
    facts = IssueOwnershipFacts(issue_key="had-515", project_name="Hadto.co", state_type="backlog")

    records = build_ownership_decision_audit_records(
        facts,
        selected=True,
        live_execution_started=True,
        delegate_attempted=True,
        delegated=True,
        assign_attempted=True,
    )

    assert canonical_ownership_dedupe_key("had-515") == "workspace-orchestrator:HAD-515"
    assert records[0].outcome == "selected"
    assert records[0].reason == "selected"
    assert records[1].action == "execute"
    assert records[1].reason == "live_execution"
    assert records[2].action == "delegate"
    assert records[2].outcome == "delegated"
    assert records[2].reason == "delegate_allowed"
    assert records[3].action == "assign"
    assert records[3].outcome == "skipped"
    assert records[3].reason == "writeback_skipped"
    assert records[3].policy_reason == "assign_allowed"


def test_audit_records_capture_repo_unresolved_and_already_undelegated():
    facts = IssueOwnershipFacts(
        issue_key="HAD-516",
        project_name="Hadto.co",
        state_type="backlog",
        repo_resolved=False,
        already_undelegated=True,
    )

    records = build_ownership_decision_audit_records(
        facts,
        selected=True,
        delegate_attempted=True,
        assign_attempted=True,
        undelegate_attempted=True,
    )

    assert records[1].action == "execute"
    assert records[1].reason == "repo_unresolved"
    assert records[2].action == "delegate"
    assert records[2].outcome == "denied"
    assert records[2].reason == "repo_unresolved"
    assert records[2].policy_reason == "delegate_denied"
    assert records[3].action == "assign"
    assert records[3].reason == "repo_unresolved"
    assert records[3].policy_reason == "assign_denied"
    assert records[4].action == "undelegate"
    assert records[4].outcome == "skipped"
    assert records[4].reason == "already_undelegated"
