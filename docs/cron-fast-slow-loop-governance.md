# Fast-loop versus slow-loop governance for Hermes control loops

Hermes recurring jobs and delegated work now expose two timescales on the same trust-contract surface.

## Fast loop

The fast loop may change the current artifact, issue, PR, or machine-readable status for the run that is already in motion.

Allowed fast-loop behavior depends on the job class:

- `discovery`: collect evidence, refresh durable artifacts, and mark visible evidence gaps
- `execution`: advance the current issue, PR, deployment, or verification step, and record a concrete blocker when execution is unsafe
- `bridge`: reacquire backlog, move the selected work item, and update the canonical issue status with live evidence

Fast loops do not rewrite the governing rules for other runs.

## Slow loop

The slow loop rewrites the game instead of merely playing the current turn. Slow-loop changes include:

- trust contracts, verification targets, or benchmark criteria
- cron/job governance, topology, or durable policy docs
- delegate, assignee, approval, backlog-selection, or rollout rules that extend beyond the current work item

These changes should happen through an explicit governance pass, not as an incidental side effect of a pressured execution run.

## Operator-visible escalation checkpoint

Every trust contract should carry an `Escalate When` checkpoint. That checkpoint means:

- stop treating the run as a fast execution pass
- preserve the current evidence and blocker plainly
- open or advance the slower governance work needed to revise the rule, benchmark, or contract

Examples:

- A recurring bridge loop can merge or resume the selected issue in the fast loop, but changing backlog-preemption policy is slow-loop work.
- An execution loop can block a rollout or fail verification in the fast loop, but changing rollout gates for all future runs is slow-loop work.
- A discovery loop can report missing evidence in the fast loop, but changing study cadence or evidence requirements is slow-loop work.

## Repo-visible surfaces

The contract is exposed in three places:

1. `hadto_patches/cron_jobs.py` trust-contract snapshots now include `fast_loop_surfaces`, `slow_loop_surfaces`, and `escalation_checkpoint`
2. `hadto_patches/cron_scheduler.py` prompts recurring jobs to report those fields in the saved Trust Contract block
3. `hadto_patches/cron_cli.py` prints the fast-loop, slow-loop, and escalation checkpoint in `hermes cron topology`
