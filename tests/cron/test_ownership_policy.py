import pytest

from hadto_patches.ownership_policy import (
    IssueOwnershipFacts,
    evaluate_ownership_policy,
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
    assert decision.ownable.reason == "explicit_override"
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
    assert decision.ownable.reason == "explicit_override"
    assert decision.override_reason == "HAD-511 manual review"
