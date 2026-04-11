---
sidebar_position: 11
title: "Cron Internals"
description: "How Hermes stores, schedules, edits, pauses, skill-loads, and delivers cron jobs"
---

# Cron Internals

Hermes cron support is implemented primarily in:

- `cron/jobs.py`
- `cron/scheduler.py`
- `tools/cronjob_tools.py`
- `gateway/run.py`
- `hermes_cli/cron.py`

## Scheduling model

Hermes supports:

- one-shot delays
- intervals
- cron expressions
- explicit timestamps

The model-facing surface is a single `cronjob` tool with action-style operations:

- `create`
- `list`
- `update`
- `pause`
- `resume`
- `run`
- `remove`

## Job storage

Cron jobs are stored in Hermes-managed local state (`~/.hermes/cron/jobs.json`) with atomic write semantics.

Each job can carry:

- prompt
- schedule metadata
- repeat counters
- delivery target
- lifecycle state (`scheduled`, `paused`, `completed`, etc.)
- zero, one, or multiple attached skills
- optional topology metadata (`role`, `scope`)

Backward compatibility is preserved for older jobs that only stored a legacy single `skill` field or none of the newer lifecycle fields.

## Runtime behavior

The scheduler:

- loads jobs
- computes due work
- executes jobs in fresh agent sessions
- optionally injects one or more skills before the prompt
- handles repeat counters
- updates next-run metadata and state

In gateway mode, cron ticking is integrated into the long-running gateway loop.

## Topology metadata and linting

Cron jobs may optionally declare:

- `role` — what the job is doing (`implement`, `report`, `study`, `publish`, `coordinate`, etc.)
- `scope` — what surface it owns (`global`, `ontology`, `pipeline`, `workbench`, `hermes`, etc.)

Hermes does not require these fields for ordinary personal cron jobs. They exist so larger autonomous installations can make scheduling intent machine-readable and inspectable.

The built-in inspector (`inspect_job_topology()` in `cron/jobs.py`) powers:

- `hermes cron topology`
- `hermes cron doctor`
- `cronjob(action="topology")`
- `cronjob(action="doctor")`

Today the linter enforces two conservative invariants:

- duplicate job names are flagged because name-based operations become ambiguous
- overlapping implementation jobs are flagged when:
  - more than one active `role=implement` job shares a `scope`
  - an active `role=implement scope=global` job coexists with scoped implementation jobs

## Skill-backed jobs

A cron job may attach multiple skills. At runtime, Hermes loads those skills in order and then appends the job prompt as the task instruction.

This gives scheduled jobs reusable guidance without requiring the user to paste full skill bodies into the cron prompt.

## Recursion guard

Cron-run sessions disable the `cronjob` toolset. This prevents a scheduled job from recursively creating or mutating more cron jobs and accidentally exploding token usage or scheduler load.

## Delivery model

Cron jobs can deliver to:

- origin chat
- local files
- platform home channels
- explicit platform/chat IDs

## Locking

Hermes uses lock-based protections so overlapping scheduler ticks do not execute the same due-job batch twice.

## Related docs

- [Cron feature guide](../user-guide/features/cron.md)
- [Gateway Internals](./gateway-internals.md)
