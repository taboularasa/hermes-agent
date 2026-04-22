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

## First proof point

Broad governance doctrine is not enough for a meaningful capability shift. Each recurring control-loop report must name one bounded first seed: the protected proof point where the new model is supposed to work before it expands.

The `First Proof Point` block requires:

- `Seed Surface`: one concrete issue, job, repo path, check, or operator workflow
- `Protection Assumptions`: what keeps the seed bounded while tested
- `Success Signal`: the observable result that proves the seed worked
- `Imitation Path`: what another site would copy only after the signal holds
- `Why First`: why this site is the right first nucleation point

This separates broad doctrine from a testable seed system. A report that says "roll this out globally" without naming the protected first site should stay visible as incomplete governance.

## Operator checks

Every trust contract now carries three operator checks before a recurring automation pattern is treated as normal:

- `Dignity`: does the run preserve operator agency, ownership, and visible misses instead of forcing surrender for basic access?
- `Capability`: does the run compound operator capability with durable evidence and steerable context instead of replacing judgment with opaque automation?
- `Viability`: does the run keep the surrounding system stable, bounded, and inspectable enough to rely on over time?

These checks are phrased for recurring loops, delegated execution, and self-improvement review surfaces.

## Repo-visible surfaces

The contract is exposed in three places:

1. `hadto_patches/cron_jobs.py` trust-contract snapshots now include `dignity_check`, `capability_check`, `viability_check`, `fast_loop_surfaces`, `slow_loop_surfaces`, and `escalation_checkpoint`
2. `hadto_patches/cron_jobs.py` also parses recent outputs for the `First Proof Point` field set and attaches it to trust-contract snapshots
3. `hadto_patches/cron_scheduler.py` prompts recurring jobs to report those fields in the saved Trust Contract and First Proof Point blocks
4. `hadto_patches/cron_cli.py` prints the fast-loop, slow-loop, escalation checkpoint, first seed, success signal, and imitation path in `hermes cron topology`
