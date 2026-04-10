---
name: hermes-self-improvement-loop
description: Review Hermes journal and execution history, maintain a Linear self-improvement project, and delegate implementation work to the local Codex CLI without spawning duplicate runs.
version: 1.0.0
author: Hermes Agent
license: MIT
---

# Hermes Self-Improvement Loop

Use this when Hermes should inspect its own recent work, identify repeated capability gaps, create or update a Linear self-improvement backlog, and push concrete implementation work to the local Codex CLI on the Lenovo host.

## Inputs to read first

- `/home/david/.hermes/notes/hermes-self-improvement-charter.md`
- `/home/david/stacks/hermes-journal/src/data/journal.json`
- `/home/david/.hermes/codex/runs.json`
- `/home/david/.hermes/ctx/session_bindings.json`

## Core operating model

Hermes is the EM.
Local Codex on the Lenovo host is the IC.
Linear is the canonical planning and audit surface.

Do not treat cloud Codex as the delegate for this loop.
Do not create duplicate Linear projects, duplicate Linear issues, or duplicate Codex runs for the same work item.

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
3. `linear_issue(action="project_upsert", ...)`
4. `linear_issue(action="issue_upsert", ...)` for each durable gap
5. `linear_issue(action="comment", ...)` for machine-readable status
6. `linear_issue(action="update_state", ...)` when work starts or finishes

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

Do not create Linear issues for one-off annoyances unless they indicate a systemic gap.

## Project maintenance rules

- Keep at most one standing umbrella project for this loop unless there is a clear reason to split a large program out into its own project.
- Keep the project description human-readable, but include the hidden dedupe marker.
- When the same issue already exists, update it in place instead of creating a near-duplicate.
- Prefer exact, capability-shaped titles over vague retrospectives.

## Issue shaping rules

Each issue should include:

- the capability gap,
- why it matters now,
- evidence from journal entries, Codex runs, or recent execution failures,
- the target repo or execution surface when known,
- a concrete verification expectation.

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
- require verification commands,
- require a concise summary of files changed and results.

If `codex_delegate(action="start", external_key=...)` reports `skipped_existing=true`, do not spawn another run. Reuse the active run and update Linear with the existing run metadata instead.

If a previous run finished but needs correction, use `codex_delegate(action="resume", ...)` from that prior run rather than opening a fresh unrelated worker when possible.

## Suggested cadence behavior

On each loop:

1. Read the charter and the most recent journal entries.
2. Extract up to 3 concrete capability gaps.
3. Upsert the umbrella Linear project.
4. Upsert the corresponding Linear issues.
5. Pick the highest-leverage unblocked implementation issue, if any.
6. Delegate that issue to local Codex if the repo surface is clear.
7. Write a concise status comment back to Linear.

If no clear implementation issue exists, do not force delegation. Leave the project and issues in a clean planned state and report the blocker.

## Output format

Return only a concise operator summary:

- Project status
- Issues created or updated
- One issue chosen for implementation, or why none was chosen
- Codex run id / status if delegation happened
- Next highest-leverage move
