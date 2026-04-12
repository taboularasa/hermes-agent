# Self-Improvement Benchmark

Hermes now has a first-class self-improvement benchmark surface in `tools/self_improvement_tool.py`.
It also has a control-plane wrapper, `self_improvement_pipeline`, which is the preferred
way for the scheduled self-improvement loop to apply the benchmark without relying on
prompt-only discipline.

## Purpose

The benchmark is the answer to a simple question:

> Is Hermes self-evolution moving in a positive direction, or are we just creating more activity?

It complements the existing `self_improvement_evidence_gate`:

- the evidence gate decides whether the reliability floor is degraded right now,
- the benchmark turns that and the surrounding execution/planning signals into a persisted scorecard that can be compared across runs.

## What it reads

- journal evidence
- Codex run metadata
- ctx session bindings
- ontology self-improvement context
- the Hermes self-improvement Linear project
- the live epoch objective / reward policy

## Benchmark checks

The benchmark currently scores these areas:

- `reliability_gate`
- `execution_loop`
- `stale_execution_records`
- `linear_planning_surface`
- `delegate_assignment_hygiene`
- `reward_policy_alignment`
- `recent_delivery_outcomes`
- `ontology_readiness`

Critical failures on `reliability_gate`, `linear_planning_surface`, `delegate_assignment_hygiene`, or `reward_policy_alignment` make the overall direction negative even if other checks are strong.

## Output contract

`self_improvement_benchmark` returns:

- an overall score out of 100
- a current direction: `positive`, `mixed`, or `negative`
- a trend versus the previous recorded run: `baseline`, `improving`, `flat`, or `regressing`
- per-check scores, details, and recommendations
- a persisted history record under `HERMES_HOME/self_improvement/benchmark_history.json`

`self_improvement_pipeline` wraps that benchmark and also returns:

- any safe Linear repairs it applied automatically
- benchmark-generated issue upserts or auto-closures
- the current top benchmark-driven Linear candidate
- a deduplicated status comment on that top candidate

Today those safe Linear repairs include:

- clearing human assignees from Hermes-delegated issues
- demoting self-improvement issues out of active states when no live Codex run or running Hermes ctx session is attached to that issue anymore

## Intended use

Hermes should run `self_improvement_pipeline` before self-improvement prioritization.
Use the raw benchmark directly when you need to inspect detailed check output.

Within the self-improvement project, `In Progress` should mean there is current machine-observable execution behind the issue. If that execution disappears, the pipeline is expected to move the issue back to a non-active state instead of leaving ghost WIP behind.

If the benchmark reports:

- `negative`: fix the highest-weight failing benchmark first
- `mixed`: continue, but prefer work that removes warnings or improves delivery outcomes
- `positive`: normal self-improvement prioritization is allowed

## Verification

- `pytest tests/tools/test_self_improvement_tool.py -q`
- `pytest tests/tools/test_linear_issue_tool.py -q`
- `pytest tests/agent/test_ontology_context.py -q`
