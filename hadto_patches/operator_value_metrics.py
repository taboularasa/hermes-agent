"""Operator-value metrics for Hermes self-improvement control loops."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class OperatorValueMetric:
    """A compact metric that measures operator decision support."""

    key: str
    label: str
    question: str
    evidence: str
    verification: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "question": self.question,
            "evidence": self.evidence,
            "verification": self.verification,
            "operator_value": True,
        }


OPERATOR_VALUE_METRICS: Tuple[OperatorValueMetric, ...] = (
    OperatorValueMetric(
        key="decision_readiness",
        label="Decision readiness",
        question="Does the result make the operator's next decision explicit and easy to approve, reject, or defer?",
        evidence="State the recommended decision, the rejected alternatives, and the reason each alternative lost.",
        verification="The report includes a concrete next_decision plus a pass/fail/partial status for the decision gate.",
    ),
    OperatorValueMetric(
        key="evidence_traceability",
        label="Evidence traceability",
        question="Can the operator trace important claims to artifacts instead of trusting narrative summary?",
        evidence="Link or name the command, test, log, PR, issue, benchmark, or file that supports each key claim.",
        verification="Every material claim has an artifact reference or is marked as inference/unknown.",
    ),
    OperatorValueMetric(
        key="risk_visibility",
        label="Risk visibility",
        question="Are unresolved risks, blockers, and uncertainty visible before the operator commits to action?",
        evidence="List regressions, missing checks, stale assumptions, failed commands, or ambiguous ownership.",
        verification="Each unresolved risk has a next check, owner, or explicit reason it is acceptable.",
    ),
    OperatorValueMetric(
        key="follow_through_control",
        label="Follow-through control",
        question="Did the loop close the operational handoff instead of only producing work volume?",
        evidence="Record branch, commit, PR, CI/check status, and any follow-up owner or scheduled action.",
        verification="The report shows whether the handoff artifact exists and whether required checks passed.",
    ),
)


def operator_value_metric_dicts() -> Tuple[Dict[str, Any], ...]:
    """Return JSON-serializable metric definitions."""

    return tuple(metric.to_dict() for metric in OPERATOR_VALUE_METRICS)


def operator_value_metric_docs() -> str:
    """Return markdown documentation for the operator-value metric set."""

    lines = [
        "Operator-value metrics:",
        "",
        "| Metric | Evidence requirement | Verification requirement |",
        "| --- | --- | --- |",
    ]
    for metric in OPERATOR_VALUE_METRICS:
        lines.append(
            f"| `{metric.key}` ({metric.label}) | {metric.evidence} | {metric.verification} |"
        )
    return "\n".join(lines)


def operator_value_scorecard_template() -> str:
    """Return the structured scorecard template expected from control-loop runs."""

    scorecard = {
        "operator_value_scorecard": {
            metric.key: {
                "score": 0,
                "evidence": "",
                "verification": "",
                "notes": "",
            }
            for metric in OPERATOR_VALUE_METRICS
        },
        "throughput_diagnostic": {"value": "", "evidence": ""},
        "next_decision": "",
    }
    return (
        "OPERATOR_VALUE_SCORECARD\n"
        "Use scores 0=missing, 1=partial, 2=strong. Keep output compact, but include evidence and verification for each metric.\n"
        "Report throughput_diagnostic separately for volume counters such as tasks touched, lines changed, token counts, issue count, or elapsed time; these do not count as operator value.\n"
        "```json\n"
        f"{json.dumps(scorecard, indent=2)}\n"
        "```"
    )


def format_operator_value_prompt_block(context: str) -> str:
    """Return a system prompt block that asks an agent to emit the scorecard."""

    context = context.strip() or "control loop"
    return (
        f"[SYSTEM: Add operator-value metrics to this {context}. "
        "Treat output quantity as a diagnostic, not as a proxy for operator value. "
        "Before final reporting, include the scorecard below so the operator can distinguish work volume "
        "from decision support.]\n\n"
        f"{operator_value_metric_docs()}\n\n"
        f"{operator_value_scorecard_template()}"
    )
