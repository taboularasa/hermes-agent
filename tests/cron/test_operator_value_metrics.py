import json

from hadto_patches.operator_value_metrics import (
    OPERATOR_VALUE_METRICS,
    format_operator_value_prompt_block,
    operator_value_metric_docs,
    operator_value_scorecard_template,
)


def test_operator_value_metrics_are_compact_and_verifiable():
    assert len(OPERATOR_VALUE_METRICS) == 4

    keys = {metric.key for metric in OPERATOR_VALUE_METRICS}
    assert keys == {
        "decision_readiness",
        "evidence_traceability",
        "risk_visibility",
        "follow_through_control",
    }

    for metric in OPERATOR_VALUE_METRICS:
        serialized = metric.to_dict()
        assert serialized["operator_value"] is True
        assert serialized["evidence"]
        assert serialized["verification"]
        assert "throughput" not in metric.question.lower()


def test_scorecard_template_distinguishes_operator_value_from_throughput():
    template = operator_value_scorecard_template()

    assert "OPERATOR_VALUE_SCORECARD" in template
    assert "throughput_diagnostic" in template
    assert "do not count as operator value" in template
    payload = template.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed = json.loads(payload)
    scorecard = parsed["operator_value_scorecard"]
    for metric in OPERATOR_VALUE_METRICS:
        assert metric.key in template
        assert scorecard[metric.key]["evidence"] == ""


def test_prompt_block_and_docs_include_evidence_and_verification():
    block = format_operator_value_prompt_block(context="self-improvement control loop")
    docs = operator_value_metric_docs()

    assert "self-improvement control loop" in block
    assert "score" in block
    assert "evidence" in block
    assert "verification" in block

    for metric in OPERATOR_VALUE_METRICS:
        assert metric.key in docs
        assert metric.evidence in docs
        assert metric.verification in docs
