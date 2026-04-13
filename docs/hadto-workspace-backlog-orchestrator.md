# Hadto Workspace Backlog Orchestrator

Hermes should not decide “there is no work left” from a single project-local executor.
The canonical backlog owner is now one global coordinator that looks across the full HAD Linear team.

## Operating model

- One active cron job with `role=coordinate` and `scope=global` owns backlog selection.
- Scoped `role=implement` jobs stay paused while the coordinator is active.
- Reports, study loops, and publishing jobs may remain active because they do not own backlog selection.
- The coordinator should run at high cadence when the workspace has open work. Hourly cadence is too sparse for a backlog that is expected to move continuously; prefer `every 10m` or tighter unless token pressure forces a slower loop.
- “Current work” reporting must include the selected backlog item from the coordinator state, not just currently-running processes.

## Tooling

Use `workspace_backlog_orchestrator` before any autonomous implementation selection.

It now does five things:

1. Lists open HAD Linear issues across the workspace.
2. Classifies ownership as `hermes`, `unowned`, `human`, or `other-agent`.
3. Detects stale WIP from Linear state age plus local Codex run evidence.
4. Audits managed repo roots and ctx worktrees for dirty state, stale worktrees, and missing ownership provenance.
5. Returns one canonical `selected_work` item with repo-root and execution metadata. `selected_issue` remains for compatibility, but `selected_work` is the object the cron prompt should obey.

The tool persists its last snapshot to:

- `~/.hermes/backlog/workspace_orchestrator_state.json`

Optional operator overrides live at:

- `~/.hermes/notes/hadto-workspace-orchestrator.yaml`

Example shape:

```yaml
team_key: HAD
stale_hours: 24
dirty_stale_hours: 24
issue_limit: 200
candidate_limit: 10
git_hygiene_preemption_limit: 3
git_hygiene_backoff_hours: 6
project_priority:
  - Hermes Self-Improvement
  - Hermes Field Copilot
project_repo_roots:
  Hermes Self-Improvement: /home/david/stacks/hermes-agent
  Hermes Field Copilot: /home/david/stacks/phoneitin
managed_repo_roots:
  - /home/david/stacks/hermes-agent
  - /home/david/stacks/phoneitin
default_branches:
  /home/david/stacks/hermes-agent: main
  /home/david/stacks/hadto-pipeline: master
```

## Selection rules

- Stale Hermes-owned WIP wins first.
- Stale unowned started work wins next.
- Active Hermes-owned WIP beats fresh backlog.
- Unowned actionable backlog is claimable only when human-owned or other-agent-owned work is not being stolen.
- Unowned dirty repo state can preempt backlog when it has no active ctx/Codex owner or when it is linked to an open Linear issue that has gone stale locally.
- Repeated orphaned dirty repo incidents must not monopolize the queue forever. If the same unlinked orphaned dirty worktree preempts several consecutive coordinator cycles without durable linkage, it should enter backoff and yield backlog priority temporarily while remaining visible for later retry.
- Human-owned and other-agent-owned issues are visible for audit but not selected.

## Repo hygiene policy

- ctx bindings plus Codex `external_key=linear:<IDENTIFIER>` are the primary ownership signals for local work.
- Dirty state is never treated as “safe to delete” just because it is local.
- The orchestrator must investigate branch divergence, merge status into the default branch, linked Linear issue state, and active ctx/Codex ownership before cleanup.
- If tracked file modifications still exist, the orchestrator should prefer `investigate` or `resume_or_merge_linked_wip`, not deletion.
- Deletion is only a candidate for stale clean worktrees or post-merge residue after the linked issue is already closed and no blockers remain.

## Cron guidance

 The global coordinator cron prompt should:

1. Call `workspace_backlog_orchestrator(...)`.
2. Treat `selected_work` as the default next action.
3. If `selected_work.kind == "linear_issue"`, delegate concrete repo work to local Codex with `external_key=linear:<IDENTIFIER>`.
4. If `selected_work.kind == "git_hygiene"`, reconcile provenance first:
   - resume or merge linked WIP when the linked issue is still open
   - inspect diff and branch status before deleting anything
   - only remove stale worktrees when the tool marks them as cleanup candidates and blockers are absent
5. Write a deduplicated Linear status comment for the selected work item's linked issue when possible.
   - Use one canonical dedupe key per issue: `workspace-orchestrator:<IDENTIFIER>` for normal selection comments and `workspace-orchestrator:git-hygiene:<IDENTIFIER>` for hygiene comments.
   - Do not create suffix variants like `:inspection` or `:blocker`; update the canonical comment body instead.
6. Keep scoped implementers paused so there is one backlog owner.
7. Do not stop after a lightweight inspection or status write if the workspace still has actionable backlog. Prefer leaving the system with either an active local Codex run, a clearly blocked item recorded in Linear, or a resolved hygiene incident.
8. When reporting status to a user, distinguish:
   - live execution in flight now
   - the selected backlog item Hermes is actively advancing
   - recurring jobs that remain scheduled
   Saying “no active work” is incorrect when `selected_work` is actionable.

## Verification

After topology changes:

```bash
./venv/bin/python -m hermes_cli.main cron topology --all
./venv/bin/python -m hermes_cli.main cron doctor
```

Healthy output should show:

- one active `coordinate/global` job
- the coordinator scheduled `every 10m` or tighter unless an operator has intentionally slowed it down
- no active scoped implementers
- no topology conflicts
