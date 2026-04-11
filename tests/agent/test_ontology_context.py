import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from agent.ontology_context import (
    build_consulting_context,
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
