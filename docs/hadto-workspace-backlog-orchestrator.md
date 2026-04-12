# Hadto Workspace Backlog Orchestrator

Hermes should not decide “there is no work left” from a single project-local executor.
The canonical backlog owner is now one global coordinator that looks across the full HAD Linear team.

## Operating model

- One active cron job with `role=coordinate` and `scope=global` owns backlog selection.
- Scoped `role=implement` jobs stay paused while the coordinator is active.
- Reports, study loops, and publishing jobs may remain active because they do not own backlog selection.

## Tooling

Use `workspace_backlog_orchestrator` before any autonomous implementation selection.

It does four things:

1. Lists open HAD Linear issues across the workspace.
2. Classifies ownership as `hermes`, `unowned`, `human`, or `other-agent`.
3. Detects stale WIP from Linear state age plus local Codex run evidence.
4. Returns one selected issue with repo-root and execution-mode metadata.

The tool persists its last snapshot to:

- `~/.hermes/backlog/workspace_orchestrator_state.json`

Optional operator overrides live at:

- `~/.hermes/notes/hadto-workspace-orchestrator.yaml`

Example shape:

```yaml
team_key: HAD
stale_hours: 24
issue_limit: 200
candidate_limit: 10
project_priority:
  - Hermes Self-Improvement
  - Hermes Field Copilot
project_repo_roots:
  Hermes Self-Improvement: /home/david/stacks/hermes-agent
  Hermes Field Copilot: /home/david/stacks/phoneitin
```

## Selection rules

- Stale Hermes-owned WIP wins first.
- Stale unowned started work wins next.
- Active Hermes-owned WIP beats fresh backlog.
- Unowned actionable backlog is claimable only when human-owned or other-agent-owned work is not being stolen.
- Human-owned and other-agent-owned issues are visible for audit but not selected.

## Cron guidance

The global coordinator cron prompt should:

1. Call `workspace_backlog_orchestrator(...)`.
2. Treat the selected issue as the default next action.
3. Delegate concrete repo work to local Codex with `external_key=linear:<IDENTIFIER>`.
4. Write a deduplicated Linear status comment for the selected issue.
5. Keep scoped implementers paused so there is one backlog owner.

## Verification

After topology changes:

```bash
./venv/bin/python -m hermes_cli.main cron topology --all
./venv/bin/python -m hermes_cli.main cron doctor
```

Healthy output should show:

- one active `coordinate/global` job
- no active scoped implementers
- no topology conflicts
