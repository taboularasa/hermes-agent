from pathlib import Path

import yaml

from agent.ontology_cq_governance import (
    build_query_ready_cq_governance_prompt,
    evaluate_query_ready_cq_completion,
    load_query_ready_cq_governance,
    should_apply_query_ready_cq_governance,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_query_ready_cq_governance_yaml_is_repo_visible_and_pinned_to_ontology_sources() -> None:
    contract_path = REPO_ROOT / "docs" / "operations" / "query-ready-cq-governance.yaml"
    assert contract_path.exists()

    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    contract = raw["query_ready_cq_governance"]

    assert contract["contract_id"] == "prefer-query-ready-competency-question-contracts"
    assert contract["source_issue_refs"] == ["HAD-342", "ONT-010"]
    assert contract["supported_template_ids"] == [
        "field_value_lookup",
        "related_entity_lookup",
        "subclass_enumeration",
    ]
    assert contract["query_ready_minimum"]["required_cq_fields"] == [
        "template_id",
        "template_category",
        "query_contract",
    ]
    assert contract["query_ready_minimum"]["required_query_contract_fields"] == [
        "query_family",
        "focus_entity",
        "projection",
        "path_pattern",
        "executable_forms",
    ]
    assert contract["query_ready_minimum"]["required_executable_forms"] == ["dl", "sparql"]

    source_refs = contract["ontology_guidance_sources"]
    assert source_refs["canonical_contract"]["path"] == "docs/operations/cq-authoring-contract.yaml"
    assert source_refs["canonical_contract"]["commit"] == "73f2277"
    assert source_refs["provider_coverage"]["path"] == "docs/operations/ontology-research-provider-coverage.yaml"
    assert source_refs["provider_coverage"]["commit"] == "73f2277"


def test_build_query_ready_cq_governance_prompt_cites_hermes_and_ontology_contracts() -> None:
    prompt = build_query_ready_cq_governance_prompt()

    assert "docs/operations/query-ready-cq-governance.yaml" in prompt
    assert "docs/operations/cq-authoring-contract.yaml" in prompt
    assert "docs/operations/cq-authoring-runbook.md" in prompt
    assert "field_value_lookup" in prompt
    assert "related_entity_lookup" in prompt
    assert "subclass_enumeration" in prompt
    assert "free-form competency questions are incomplete" in prompt
    assert "template_id" in prompt
    assert "template_category" in prompt
    assert "query_contract" in prompt
    assert "executable_forms.dl" in prompt
    assert "executable_forms.sparql" in prompt


def test_should_apply_query_ready_cq_governance_targets_ontology_cq_prompts() -> None:
    assert should_apply_query_ready_cq_governance(
        "Rewrite these ontology competency questions into query-ready CQ YAML."
    )
    assert should_apply_query_ready_cq_governance(
        "Use smb-ontology-platform evidence to evaluate CQ template coverage."
    )
    assert not should_apply_query_ready_cq_governance("Write a changelog for this PR.")


def test_free_form_cq_proposals_are_reported_incomplete() -> None:
    report = evaluate_query_ready_cq_completion(
        {"question": "Why does the insurance claim queue change?"}
    )

    assert report["complete"] is False
    assert "template_id" in report["missing_fields"]
    assert "template_category" in report["missing_fields"]
    assert "query_contract" in report["missing_fields"]
    assert any("free-form competency questions are incomplete" in error for error in report["errors"])


def test_query_ready_cq_completion_accepts_supported_structured_contracts() -> None:
    report = evaluate_query_ready_cq_completion(
        {
            "template_id": "related_entity_lookup",
            "template_category": "retrieval",
            "query_contract": {
                "query_family": "path_lookup",
                "focus_entity": "dental:Appointment",
                "projection": "dental:Dentist",
                "path_pattern": "dental:Appointment -> dental:hasProvider -> dental:Dentist",
                "executable_forms": {
                    "dl": "PathQuery(dental:Appointment -> dental:hasProvider -> dental:Dentist)",
                    "sparql": "SELECT ?value WHERE { ?focus dental:hasProvider ?value . }",
                },
            },
        }
    )

    assert report["complete"] is True
    assert report["missing_fields"] == []
    assert report["errors"] == []


def test_query_ready_cq_completion_rejects_unsupported_template_ids_and_missing_executable_forms() -> None:
    report = evaluate_query_ready_cq_completion(
        {
            "template_id": "causal_analysis",
            "template_category": "retrieval",
            "query_contract": {
                "query_family": "path_lookup",
                "focus_entity": "dental:Appointment",
                "projection": "dental:Dentist",
                "path_pattern": "dental:Appointment -> dental:hasProvider -> dental:Dentist",
                "executable_forms": {"dl": "PathQuery(...)"},
            },
        }
    )

    assert report["complete"] is False
    assert any("supported template family" in error for error in report["errors"])
    assert "query_contract.executable_forms.sparql" in report["missing_fields"]


def test_load_query_ready_cq_governance_returns_mirrored_contract() -> None:
    contract = load_query_ready_cq_governance()

    assert contract["contract_id"] == "prefer-query-ready-competency-question-contracts"
    assert contract["hermes_surface"]["citation_path"] == "docs/operations/query-ready-cq-governance.yaml"
