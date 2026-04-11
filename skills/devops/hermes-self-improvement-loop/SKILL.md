---
name: hermes-self-improvement-loop
description: Review Hermes journal and execution history, maintain a Linear self-improvement project, score work against the current Hadto epoch objective, and delegate implementation work to the local Codex CLI without spawning duplicate runs.
version: 1.0.0
author: Hermes Agent
license: MIT
---

# Hermes Self-Improvement Loop

Use this when Hermes should inspect its own recent work, identify repeated capability gaps, score them against the current Hadto business epoch, create or update a Linear self-improvement backlog, and push concrete implementation work to the local Codex CLI on the Lenovo host.

## Inputs to read first

- `/home/david/.hermes/notes/hermes-self-improvement-charter.md`
- `/home/david/.hermes/notes/hermes-epoch-objective.yaml`
- `references/reward-policy-template.yaml`
- `/home/david/stacks/hermes-journal/src/data/journal.json`
- `/home/david/.hermes/codex/runs.json`
- `/home/david/.hermes/ctx/session_bindings.json`
- `/home/david/stacks/smb-ontology-platform/evolution/metrics.json`
- `/home/david/stacks/smb-ontology-platform/evolution/delta_report.json`
- `/home/david/stacks/smb-ontology-platform/evolution/daily_report.md`
- `/home/david/stacks/smb-ontology-platform/research/manifests/`

## Core operating model

Hermes is the EM.
Local Codex on the Lenovo host is the IC.
Linear is the canonical planning and audit surface.

Hermes must plan through three lanes:

- `Maintenance`: restore or protect operational reliability
- `Growth`: improve Hadto's ability to win contracts, preserve revenue, and create social proof
- `Capability`: build new Hermes abilities that clearly compound Maintenance or Growth

Do not treat cloud Codex as the delegate for this loop.
Do not create duplicate Linear projects, duplicate Linear issues, or duplicate Codex runs for the same work item.

## Reward hierarchy

Hermes should optimize in this order:

1. Reliability floor
2. Current epoch objective
3. Capability investment

Interpretation:

- If the reliability floor is degraded, only `Maintenance` work is eligible.
- If the reliability floor is healthy, prefer `Growth` work that improves contract-winning throughput for the current epoch.
- `Capability` work only survives prioritization when it clearly improves `Maintenance` or `Growth`.

The live epoch objective is defined in `/home/david/.hermes/notes/hermes-epoch-objective.yaml`.
If that file is missing, fall back to the bundled template and assume the current epoch is:

- help Hadto win client contracts,
- maintain revenue continuity,
- build visible social proof through delivery quality.

## Reliability floor triggers

Treat the reliability floor as degraded when any of these are true:

- required CI on `main` is red,
- Slack, Linear, ctx, or local Codex delegation is broken,
- a production automation or scheduled workflow is stalled,
- a client-facing delivery path is broken,
- the evidence sources used by this loop are clearly stale or contradictory.

Always compute an explicit evidence freshness gate using the `self_improvement_evidence_gate` tool.
Also compute `ontology_context(action="self_improvement")` so ontology bottlenecks and business recommendations
show up in the candidate set instead of being treated as blog-only side effects.
If the gate reports contradictions or stale evidence, treat the reliability floor as degraded.

Important distinction:
- stale or missing ontology reports are a reliability-floor problem,
- ontology conversion bottlenecks or weak business recommendations are usually `Growth` or `Capability` candidates, not automatic stop-the-world incidents.

When a reliability trigger is active:

- do not open speculative `Growth` or `Capability` issues ahead of the repair,
- create or update only the `Maintenance` issue that resolves the degraded state,
- explain in the Linear description or status comment which trigger forced the agenda.
- include evidence provenance with source tags (see the provenance contract below).

## Canonical Linear structures

- Team key: `HAD`
- Project name: `Hermes Self-Improvement`
- Project dedupe key: `project:hermes-self-improvement`
- Issue dedupe key pattern: `issue:hermes-self-improvement:<slug>`
- Status comment dedupe key pattern: `status:<IDENTIFIER>`
- Codex external key pattern: `linear:<IDENTIFIER>`

## Required Linear tool usage

Use the built-in `linear_issue` tool instead of raw curl when it can do the job.

Minimum flow:

1. `linear_issue(action="list_users")`
2. `linear_issue(action="list_projects")`
3. `ontology_context(action="self_improvement")`
4. `linear_issue(action="project_upsert", ...)`
5. `linear_issue(action="issue_upsert", ...)` for each durable gap
6. `linear_issue(action="comment", ...)` for machine-readable status
7. `linear_issue(action="update_state", ...)` when work starts or finishes

Prefer `delegateId` for Hermes-owned work.
Leave `assigneeId` empty unless a human operator is explicitly needed.

## What counts as a real improvement candidate

Create or update Linear issues only for shortcomings that are durable and reusable, such as:

- repeated tool gaps,
- repeated planning failures,
- recurring runtime friction,
- missing observability,
- weak verification habits,
- missing integration surfaces,
- missing documentation that blocks execution,
- repeated failure to translate goals into shipped code.
- missing business-side visibility that blocks contract-winning work.

Do not create Linear issues for one-off annoyances unless they indicate a systemic gap.

Every candidate must cite at least one durable evidence source:

- journal entries,
- Codex run metadata,
- ctx session history or bindings,
- ontology metrics, delta reports, source-material manifests, or prompt proposals,
- CI failures or logs,
- Linear delivery history,
- Slack or client-work signals.

## Scoring model

When the reliability floor is healthy, score candidate work with the bundled formula:

`score = 5*epoch_impact + 3*reliability_impact + 2*reuse + 2*urgency + 1*confidence - 3*risk - 2*effort`

Use coarse operator-friendly ratings rather than false precision. A 0-3 scale is enough.

Field meanings:

- `epoch_impact`: how much the work increases the chance of winning contracts or building social proof this epoch
- `reliability_impact`: how much the work reduces breakage or manual intervention
- `reuse`: how often the result should pay off again
- `urgency`: how immediate the pain or opportunity is
- `confidence`: how strong the evidence is that this work will help
- `risk`: how likely the change is to cause distraction or regressions
- `effort`: expected implementation cost

Default lane bias for the current epoch:

- `Growth`: 50%
- `Maintenance`: 30%
- `Capability`: 20%

Do not treat the percentages as quotas. They are tie-break guidance after the reliability floor is healthy.

## Guardrails

- Keep at most one active issue per lane.
- Keep at most one standing umbrella project unless a major theme truly cannot fit inside it.
- Do not let `Capability` work consume more than 20% of active self-improvement effort while `Maintenance` and `Growth` still have higher-scoring items.
- Every self-created issue must include the lane, why now, evidence, target repo or surface, and verification expectation.
- If the top-scoring item is not concrete enough for Codex, keep it in planning state rather than forcing delegation.

## Project maintenance rules

- Keep at most one standing umbrella project for this loop unless there is a clear reason to split a large program out into its own project.
- Keep the project description human-readable, but include the hidden dedupe marker.
- Keep project descriptions compact. Put detailed rationale into issues/comments because Linear rejects oversized project descriptions with an argument validation error.
- When the same issue already exists, update it in place instead of creating a near-duplicate.
- Prefer exact, capability-shaped titles over vague retrospectives.

## Issue shaping rules

Each issue should include:

- the lane (`Maintenance`, `Growth`, or `Capability`),
- the capability gap,
- why it matters now,
- evidence from journal entries, Codex runs, or recent execution failures,
- the target repo or execution surface when known,
- a concrete verification expectation,
- the expected effect on reliability or the current epoch objective.

When ordering is obvious, use `linear_issue(action="issue_relation", relation_type="blocks", ...)` to encode it.

## Codex delegation rules

Use `codex_delegate` only for concrete implementation work in a specific repo.

Before starting a Codex run:

1. Identify the repo and workdir.
2. Ensure the issue is unblocked enough to implement.
3. Move the Linear issue to an active state if appropriate.
4. Post or update a status comment with the chosen repo and intent.

When starting Codex:

- pass `external_key="linear:<IDENTIFIER>"`,
- keep the prompt bounded to one issue,
- tell Codex to work only in the named repo,
- when ctx is enabled, Codex delegation must run in a ctx-managed worktree for that repo; if `codex_delegate` refuses because ctx binding failed, stop and fix ctx instead of falling back to the shared checkout,
- prefer `python` or `pytest` over `./venv/bin/python` because ctx worktrees inherit the repo virtualenv on `PATH` but may not contain a local `venv/` directory,
- require verification commands,
- require a concise summary of files changed and results.

If `codex_delegate(action="start", external_key=...)` reports `skipped_existing=true`, do not spawn another run. Reuse the active run and update Linear with the existing run metadata instead.

If a previous run finished but needs correction, use `codex_delegate(action="resume", ...)` from that prior run rather than opening a fresh unrelated worker when possible.

## Evidence provenance contract

Operator summaries and Linear status comments must include an **Evidence provenance** section with source-tagged bullets.
Use the tags below so operators can see which claims were observed vs inferred:

- `[journal]` — Hermes journal entries
- `[codex]` — Codex run metadata or logs
- `[ctx]` — ctx session bindings or history
- `[ontology]` — ontology metrics, delta reports, manifests, or source-material summaries
- `[repo]` — direct repo inspection (files, diffs, grep results)
- `[tests]` — test or lint output
- `[inference]` — explicit inference or recommendation (no direct source)

If the self-improvement evidence gate was computed, prefer the tool's `provenance.summary_markdown`
verbatim in both the operator summary and Linear status comment.

## Suggested cadence behavior

On each loop:

1. Read the charter, the live epoch objective, and the most recent evidence sources.
2. Run `self_improvement_evidence_gate` and `ontology_context(action="self_improvement")` before scoring anything.
3. Extract up to 3 concrete candidates and classify each into `Maintenance`, `Growth`, or `Capability`.
4. Check reliability-floor triggers. If any are active, discard non-maintenance candidates for this loop.
   If the `self_improvement_evidence_gate` tool reports a degraded gate, list the reasons and
   suppress non-maintenance work for this cycle.
5. Use ontology business recommendations and conversion bottlenecks as candidate evidence when they are grounded in machine-readable artifacts.
6. Score the remaining candidates with the bundled formula.
7. Upsert the umbrella Linear project.
8. Upsert or update the corresponding Linear issues with lane, evidence, and verification context.
9. Pick the highest-leverage unblocked issue that fits the guardrails.
10. Delegate that issue to local Codex if the repo surface is clear.
11. Write a concise status comment back to Linear explaining the chosen issue and why it outranked the others.
   Include an **Evidence provenance** section using the tags above.

If no clear implementation issue exists, do not force delegation. Leave the project and issues in a clean planned state and report the blocker.

## Output format

Return only a concise operator summary:

- Project status
- Issues created or updated
- Reliability gate status (healthy/degraded) and why
- One issue chosen for implementation, its lane, and why it won
- Codex run id / status if delegation happened
- Next highest-leverage move
- Evidence provenance (source-tagged bullets)
