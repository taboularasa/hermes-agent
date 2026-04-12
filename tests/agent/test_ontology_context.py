import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest.mock import patch

import yaml

from agent.ontology_context import (
    build_consulting_context,
    build_ontology_engineering_context,
    build_self_improvement_context,
    build_sales_context,
    load_ontology_snapshot,
    summarize_ontology_reliability,
)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_yaml(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_ontology_repo(tmp_path: Path, *, now: datetime) -> Path:
    repo = tmp_path / "ontology"
    generated = (now - timedelta(hours=2)).isoformat()
    _write_json(
        repo / "evolution" / "metrics.json",
        {
            "generated_at": generated,
            "verticals": {
                "home_services": {
                    "cq_total": 90,
                    "cq_answered": 90,
                    "cq_open": 0,
                    "cq_coverage": 1.0,
                    "foundation_reuse_ratio": 0.82,
                    "proposals_generated": 0,
                    "cqs_added": 2,
                },
                "professional_services": {
                    "cq_total": 135,
                    "cq_answered": 135,
                    "cq_open": 0,
                    "cq_coverage": 1.0,
                    "foundation_reuse_ratio": 0.39,
                    "proposals_generated": 0,
                    "cqs_added": 3,
                },
            },
            "platform": {
                "total_cqs": 225,
                "total_answered": 225,
                "total_cqs_added": 5,
                "total_proposals_generated": 0,
            },
        },
    )
    _write_json(
        repo / "evolution" / "delta_report.json",
        {
            "generated_at": generated,
            "business_recommendations": [
                "Prioritize outbound discovery in home_services.",
                "Use professional_services for differentiated niche research.",
            ],
            "learnings": [
                "Research signals are crossing vertical boundaries.",
            ],
            "current": {
                "verticals": {
                    "home_services": {"foundation_reuse_ratio": 0.82},
                    "professional_services": {"foundation_reuse_ratio": 0.39},
                }
            },
        },
    )
    (repo / "evolution").mkdir(parents=True, exist_ok=True)
    (repo / "evolution" / "daily_report.md").write_text(
        "# Ontology Evolution Daily Report\n\n"
        f"Generated at: `{generated}`\n\n"
        "## Business recommendations\n"
        "- Prioritize outbound discovery in home_services.\n",
        encoding="utf-8",
    )
    _write_json(
        repo / "evolution" / "logs" / "home_services_2026-04-11_0001.json",
        {
            "timestamp": generated,
            "vertical": "home_services",
            "research_cqs_added": 2,
            "proposals_generated": 0,
        },
    )
    _write_yaml(
        repo / "research" / "manifests" / "home-services.yaml",
        {
            "manifest_id": "home-services",
            "prepared_at": generated,
            "sources": [
                {
                    "source_id": "home-services-site",
                    "title": "HVAC dispatch site",
                    "captured_at": generated,
                }
            ],
        },
    )
    source_store_file = repo / "research" / "source_store" / "sha256" / "aa" / "blob.txt"
    source_store_file.parent.mkdir(parents=True, exist_ok=True)
    source_store_file.write_text("captured source", encoding="utf-8")
    _write_yaml(
        repo / "research" / "prompt_proposals" / "professional_services" / "cycle-001.yaml",
        {
            "generated_at": generated,
            "vertical": "professional_services",
            "status": "review_required",
        },
    )
    _write_yaml(
        repo / "orsd" / "home_services.yaml",
        {
            "vertical": "home_services",
            "last_evolved": generated,
            "purpose": "Model dispatch, permits, inspections, and customer scheduling for field service operators.",
            "scope": "HVAC, plumbing, and electrical dispatch with property workflows.",
            "system_problems": [
                {"title": "Manual dispatch coordination"},
                {"title": "Permit and inspection bottlenecks"},
            ],
            "ontology_use_cases": [
                {"title": "Dispatch orchestration"},
                {"title": "Property service history"},
            ],
            "competency_questions": [
                {"group": "service_call", "source": "bootstrap"},
                {"group": "permit", "source": "bootstrap"},
                {"group": "inspection", "source": "research_discovery"},
            ],
        },
    )
    _write_yaml(
        repo / "orsd" / "professional_services.yaml",
        {
            "vertical": "professional_services",
            "last_evolved": generated,
            "purpose": "Model engagements, time entry, client intake, and compliance-heavy delivery.",
            "scope": "Law firms, accounting firms, and consulting practices.",
            "system_problems": [
                {"title": "Matter and engagement sprawl"},
                {"title": "Billable time leakage"},
            ],
            "ontology_use_cases": [
                {"title": "Client intake and matter setup"},
                {"title": "Time entry and filing deadlines"},
            ],
            "competency_questions": [
                {"group": "engagement", "source": "bootstrap"},
                {"group": "time_entry", "source": "research_discovery"},
                {"group": "matter", "source": "bootstrap"},
            ],
        },
    )
    _write_yaml(
        repo / "docs" / "operations" / "ontology-backlog" / "ONT-009-ontology-engineering-textbook-study-program.md",
        {
            "id": "ONT-009",
            "title": "Run continuous ontology-engineering textbook study program",
            "status": "in_progress",
            "owner": "Hermes",
            "type": "ops/research/no-code",
        },
    )
    _write_yaml(
        repo / "docs" / "operations" / "ontology-backlog" / "ONT-004-add-micro-level-ontology-authoring-contract.md",
        {
            "id": "ONT-004",
            "title": "Add micro-level ontology authoring contract",
            "status": "done",
            "owner": "Hermes",
            "type": "code",
        },
    )
    _write_yaml(
        repo / "docs" / "operations" / "ontology-backlog" / "ONT-010-add-competency-question-authoring-templates.md",
        {
            "id": "ONT-010",
            "title": "Add competency-question authoring templates",
            "status": "in_progress",
            "owner": "Hermes",
            "type": "code",
        },
    )
    _write_yaml(
        repo / "docs" / "operations" / "ontology-backlog" / "ONT-017-add-foundational-ontology-contract.md",
        {
            "id": "ONT-017",
            "title": "Add foundational ontology contract",
            "status": "todo",
            "owner": "Hermes",
            "type": "code",
        },
    )
    (repo / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "plans" / "2026-03-31-keet-ontology-engineering-progress-tracker.md").write_text(
        "# Keet Ontology Engineering Progress Tracker\n\n"
        "## Progress summary\n"
        "- chapters_completed: 5 / 11\n"
        "- current_chapter: 6 — Top-down Ontology Development\n"
        "- current_subsection: 6.1.2 — Foundational ontology choices\n"
        "- latest_backlog_items: ONT-004, ONT-010, ONT-017\n"
        "- total_book_progress_note: Hermes translated textbook lessons into ontology backlog items and needs runtime support for those capabilities.\n\n"
        "## Study log\n"
        "### 2026-04-11T16:54:22Z\n"
        "- Key lesson: Hermes should reason explicitly about micro-level ontology authoring governance and foundational ontology posture.\n"
        "- Repo evidence: ONT-004 and ONT-017 capture the need for explicit modeling posture and foundational alignment.\n"
        "- Backlog intake: reinforced ONT-004, ONT-010, and ONT-017 as durable ontology-engineering capabilities.\n"
        "- Immediate rollover: continue Chapter 6 with foundational ontology comparison.\n",
        encoding="utf-8",
    )
    (repo / "docs" / "plans" / "2026-03-31-keet-ontology-engineering-heartbeat.md").write_text(
        "- timestamp: 2026-04-11T16:54:22Z\n"
        "- cadence: every 10 minutes\n"
        "- current_chapter: 6 — Top-down Ontology Development\n"
        "- current_subsection: 6.1.2 — Foundational ontology choices\n"
        "- status: in_progress\n"
        "- last_meaningful_finding: Hermes needs explicit foundational ontology posture and micro-level authoring governance.\n"
        "- next_action: Continue Chapter 6 and map findings into runtime context.\n",
        encoding="utf-8",
    )
    return repo


def test_load_ontology_snapshot_summarizes_candidates_and_assets(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    snapshot = load_ontology_snapshot(repo)

    assert snapshot["platform"]["total_cqs"] == 225
    assert snapshot["research_assets"]["manifest_count"] == 1
    assert snapshot["research_assets"]["source_store_files"] == 1
    assert snapshot["candidates"]["productization"]["vertical"] == "home_services"
    assert snapshot["candidates"]["differentiation"]["vertical"] == "professional_services"


def test_consulting_context_ranks_home_services_for_dispatch_query(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    context = build_consulting_context(
        query="HVAC dispatch permit inspection scheduling for field technicians",
        repo_root=repo,
    )

    assert context["matched_verticals"][0]["vertical"] == "home_services"
    core_names = [item["name"] for item in context["core_contexts"]]
    assert "Scheduling" in core_names
    assert "Compliance" in core_names
    assert context["discovery_questions"]


def test_sales_context_ranks_professional_services_for_law_firm_query(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    context = build_sales_context(
        query="law firm client intake billable time filing compliance",
        repo_root=repo,
    )

    assert context["matched_verticals"][0]["vertical"] == "professional_services"
    assert context["outreach_angles"]
    assert context["discovery_prompts"]


def test_self_improvement_context_surfaces_conversion_bottleneck_without_staleness(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    context = build_self_improvement_context(repo, now=now, freshness_hours=72)
    reliability = summarize_ontology_reliability(load_ontology_snapshot(repo), now=now, freshness_hours=72)

    assert reliability["status"] == "fresh"
    assert reliability["conversion_bottleneck"]["active"] is True
    growth_titles = [item["title"] for item in context["candidates"]["growth"]]
    assert any("proposals" in title.lower() for title in growth_titles)
    assert context["textbook_study"]["progress_summary"]["current_subsection"] == "6.1.2 — Foundational ontology choices"


def test_ontology_engineering_context_reads_textbook_study_and_provider_policy(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    with patch.dict(os.environ, {"EXA_API_KEY": "exa-test", "TAVILY_API_KEY": "tvly-test"}, clear=False):
        context = build_ontology_engineering_context(repo, limit=4)

    assert context["study"]["governing_issue"]["id"] == "ONT-009"
    assert context["study"]["progress_summary"]["latest_backlog_items"] == ["ONT-004", "ONT-010", "ONT-017"]
    assert context["hermes_upgrade_targets"]
    assert any(target["issue_id"] == "ONT-004" for target in context["hermes_upgrade_targets"])
    assert context["research_protocol"]["provider_status"]["available_providers"] == ["exa", "tavily"]


def test_ontology_engineering_context_falls_back_to_issue_metadata_when_yaml_parse_fails(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)
    (repo / "docs" / "operations" / "ontology-backlog" / "ONT-009-ontology-engineering-textbook-study-program.md").write_text(
        "id: ONT-009\n"
        "title: Run continuous ontology-engineering textbook study program\n"
        "status: in_progress\n"
        "type: ops/research/no-code\n"
        "owner: Hermes\n"
        "broken_yaml: [unterminated\n\n"
        "## Notes\n"
        "This file should still be discoverable from metadata.\n",
        encoding="utf-8",
    )
    (repo / "docs" / "operations" / "ontology-backlog" / "ONT-010-add-competency-question-authoring-templates.md").write_text(
        "id: ONT-010\n"
        "title: Add competency-question authoring templates\n"
        "status: in_progress\n"
        "type: code\n"
        "owner: Hermes\n"
        "broken_yaml: [unterminated\n\n"
        "## Notes\n"
        "This file should still seed a Hermes upgrade target.\n",
        encoding="utf-8",
    )

    context = build_ontology_engineering_context(repo, limit=4)

    assert context["study"]["governing_issue"]["id"] == "ONT-009"
    assert any(target["issue_id"] == "ONT-010" for target in context["hermes_upgrade_targets"])


def test_ontology_engineering_context_supports_legacy_issue_doc_paths(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    backlog_dir = repo / "docs" / "operations" / "ontology-backlog"
    legacy_dir = repo / "docs" / "issues"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    for path in backlog_dir.glob("ONT-*.md"):
        path.replace(legacy_dir / path.name)

    context = build_ontology_engineering_context(repo, limit=4)

    assert context["study"]["governing_issue"]["id"] == "ONT-009"
    assert any(target["issue_id"] == "ONT-017" for target in context["hermes_upgrade_targets"])


def test_ontology_engineering_context_uses_provider_inventory_when_env_missing(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)
    _write_yaml(
        repo / "docs" / "operations" / "ontology-research-provider-coverage.yaml",
        {
            "configured_backend": "tavily",
            "providers": [
                {"id": "exa", "status": "ready"},
                {"id": "tavily", "status": "declared"},
            ],
        },
    )

    with patch.dict(os.environ, {}, clear=True):
        context = build_ontology_engineering_context(repo, limit=4)

    provider_status = context["research_protocol"]["provider_status"]
    assert provider_status["available_providers"] == ["exa", "tavily"]
    assert provider_status["configured_backend"] == "tavily"
    assert provider_status["coverage_source"] == "inventory"
    assert provider_status["inventory_path"].endswith(
        "docs/operations/ontology-research-provider-coverage.yaml"
    )
    assert any(
        "Provider coverage is declared in the ontology repo" in step
        for step in context["research_protocol"]["steps"]
    )


def test_consulting_context_includes_multi_provider_research_protocol(tmp_path):
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    repo = _seed_ontology_repo(tmp_path, now=now)

    with patch.dict(os.environ, {"EXA_API_KEY": "exa-test", "PARALLEL_API_KEY": "par-test"}, clear=False):
        context = build_consulting_context(
            query="HVAC dispatch permit inspection scheduling for field technicians",
            repo_root=repo,
        )

    assert context["research_protocol"]["tool"] == "web_search_matrix"
    assert context["research_protocol"]["provider_status"]["summary"]["available_provider_count"] == 2
