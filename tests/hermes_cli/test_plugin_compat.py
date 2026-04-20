"""Tests for plugin runtime compatibility helpers."""

import json
from types import SimpleNamespace

from hermes_cli import plugin_compat
from hermes_cli.plugin_compat import (
    _migrate_hadto_capability_ledger_file,
    migrate_legacy_hadto_capability_ledger_payload,
)


def test_migrate_legacy_hadto_capability_ledger_payload_shapes_current_contract():
    legacy_payload = {
        "version": "legacy-v0",
        "updated_at": "2026-04-20T07:00:00+00:00",
        "capabilities": [
            {
                "id": "capability.legacy.pipeline",
                "title": "Legacy pipeline",
                "lane_affinity": "Growth",
                "status": "active",
                "change_surface": "hadto_hermes_plugin.tools.self_improvement:self_improvement_pipeline",
                "source_tools": ["self_improvement_pipeline"],
                "description": "Legacy capability record",
            }
        ],
        "gaps": [
            {
                "id": "gap.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
                "detail": "Ledger must load before self-improvement runs.",
                "status": "blocked",
            }
        ],
        "competency_questions": [
            {
                "id": "cq.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
                "gap_ids": ["gap.legacy.pipeline"],
                "question": "Can the migrated ledger still drive the loop?",
                "success_criteria": "The loop runs with durable evidence.",
            }
        ],
        "interventions": [
            {
                "id": "intervention.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
                "gap_ids": ["gap.legacy.pipeline"],
                "source_tool": "self_improvement_pipeline",
                "recorded_at": "2026-04-20T07:05:00+00:00",
                "status": "active",
            }
        ],
        "verification_targets": [
            {
                "id": "verification.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
                "gap_ids": ["gap.legacy.pipeline"],
                "title": "self_improvement_pipeline completes",
                "verification_method": "command",
                "command": "python -m hadto pipeline",
                "success_criteria": "Command exits successfully.",
            }
        ],
        "outcomes": [
            {
                "id": "outcome.legacy.pipeline",
                "intervention_id": "intervention.legacy.pipeline",
                "verification_target_ids": ["verification.legacy.pipeline"],
                "classification": "improved",
                "evidence_refs": ["evidence.legacy.pipeline"],
                "notes": "Recovered pipeline execution.",
                "recommended_next_step": "Keep the migration in place.",
            }
        ],
        "evidence_sources": [
            {
                "id": "evidence.legacy.pipeline",
                "source_type": "log",
                "source_location": "/tmp/hadto.log",
                "title": "Ledger recovery log",
                "capability_ids": ["capability.legacy.pipeline"],
                "gap_ids": ["gap.legacy.pipeline"],
                "outcome_ids": ["outcome.legacy.pipeline"],
            }
        ],
        "links": [{"source": "ignored"}],
    }

    migrated = migrate_legacy_hadto_capability_ledger_payload(
        legacy_payload,
        contract_version="v1",
        now_iso="2026-04-20T07:10:00+00:00",
    )

    assert migrated["contract_version"] == "v1"
    assert migrated["updated_at"] == "2026-04-20T07:00:00+00:00"
    assert migrated["indexes"] == {}

    capability = migrated["capabilities"][0]
    assert capability == {
        "capability_id": "capability.legacy.pipeline",
        "name": "Legacy pipeline",
        "lane": "growth",
        "status": "active",
        "owning_surface": "hadto_hermes_plugin.tools.self_improvement:self_improvement_pipeline",
        "upstream_strategy": "legacy_import",
        "source_tools": ["self_improvement_pipeline"],
        "summary": "Legacy capability record",
    }

    gap = migrated["gaps"][0]
    assert gap["gap_id"] == "gap.legacy.pipeline"
    assert gap["capability_id"] == "capability.legacy.pipeline"
    assert gap["urgency"] == "high"
    assert gap["change_surface"] == capability["owning_surface"]

    competency_question = migrated["competency_question_contracts"][0]
    assert competency_question["cq_id"] == "cq.legacy.pipeline"
    assert competency_question["acceptance_rule"] == "The loop runs with durable evidence."

    intervention = migrated["interventions"][0]
    assert intervention["intervention_id"] == "intervention.legacy.pipeline"
    assert intervention["tool_source"] == "self_improvement_pipeline"
    assert intervention["status"] == "active"

    verification_target = migrated["verification_targets"][0]
    assert verification_target["verification_id"] == "verification.legacy.pipeline"
    assert verification_target["target"] == "self_improvement_pipeline completes"
    assert verification_target["command"] == "python -m hadto pipeline"

    outcome = migrated["outcomes"][0]
    assert outcome["outcome_id"] == "outcome.legacy.pipeline"
    assert outcome["verification_ids"] == ["verification.legacy.pipeline"]
    assert outcome["result_status"] == "passed"
    assert outcome["evidence_ref_ids"] == ["evidence.legacy.pipeline"]

    evidence = migrated["evidence_refs"][0]
    assert evidence["evidence_id"] == "evidence.legacy.pipeline"
    assert evidence["source_kind"] == "log"
    assert evidence["source_ref"] == "/tmp/hadto.log"
    assert evidence["summary"] == "Ledger recovery log"


def test_migrate_hadto_capability_ledger_file_uses_active_profile_when_default_path_missing(
    tmp_path,
    monkeypatch,
):
    hermes_home = tmp_path / "profile-home"
    ledger_path = hermes_home / "self_improvement" / "capability_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    legacy_payload = {
        "version": "legacy-v0",
        "updated_at": "2026-04-20T07:00:00+00:00",
        "capabilities": [
            {
                "id": "capability.legacy.pipeline",
                "title": "Legacy pipeline",
                "lane_affinity": "Growth",
                "change_surface": "pipeline",
            }
        ],
        "gaps": [
            {
                "id": "gap.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
            }
        ],
        "interventions": [
            {
                "id": "intervention.legacy.pipeline",
                "capability_ids": ["capability.legacy.pipeline"],
                "target_surface": "pipeline",
            }
        ],
    }
    ledger_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    class FakeCapabilityLedger:
        @classmethod
        def model_validate(cls, payload):
            if payload.get("contract_version") != "v1":
                raise ValueError("legacy payload")
            if "version" in payload:
                raise ValueError("legacy keys still present")
            return payload

    fake_module = SimpleNamespace(
        CapabilityLedger=FakeCapabilityLedger,
        CAPABILITY_LEDGER_CONTRACT_VERSION="v1",
    )

    migrated = _migrate_hadto_capability_ledger_file(fake_module)

    assert migrated is True
    saved = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert saved["contract_version"] == "v1"
    assert "version" not in saved
    assert saved["interventions"][0]["change_surface"] == "pipeline"
