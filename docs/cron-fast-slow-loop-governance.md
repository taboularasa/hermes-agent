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

## Priority scoring lanes

Recurring agenda review and self-improvement selection must score candidate work from evidence using stable lanes before choosing a next item.

### Lane definitions

- `Maintenance`: reliability repair, broken verification, blocked operator throughput, core infrastructure recovery, and contract-preserving cleanup
- `Growth`: work that improves contract-winning throughput, client pipeline movement, demos, proposals, conversion, or social proof
- `Capability`: new Hermes leverage or automation that only survives if it compounds maintenance or growth instead of serving internal neatness alone

### Weighted inputs

Use a `0-5` scale for each input and compute:

`Weighted Score = 0.30*Epoch Impact + 0.25*Reliability Impact + 0.15*Reuse + 0.15*Urgency + 0.10*Confidence - 0.10*Risk - 0.05*Effort`

- `Epoch Impact`: how directly the work serves the current epoch objective, especially contract-winning throughput
- `Reliability Impact`: how strongly the work reduces breakage, restores trust, or prevents operator stalls
- `Reuse`: how much durable leverage or repeat benefit the work creates
- `Urgency`: how time-sensitive the opportunity, blocker, or degradation is
- `Confidence`: how strong the evidence and execution confidence are
- `Risk`: downside, regression, distraction, or blast radius
- `Effort`: expected implementation and verification cost

### Gating behavior

- Maintenance preempts the other lanes when core infrastructure is degraded, verification is broken, or reliability impact is high enough that growth work would ride on a failing base.
- Growth competes only after maintenance gates are clear. Growth evidence should be grounded in concrete pipeline or social-proof signals, not generic business language.
- Capability work should usually be held. It survives only when it clearly compounds maintenance or growth and still wins on the same weighted evidence.

### Required explanation

When one issue outranks another, the selection artifact should say:

- which lane the winning issue belongs to
- whether the candidate shipped, held, or was preempted by gating
- the weighted score and input breakdown
- why it outranked the next-best alternative, preferably naming the losing issue or lane

## Aggregate stewardship

Loop-level governance can still hide a fragile portfolio. Recurring and delegated Hermes work therefore also needs an `Aggregate Stewardship` block that names the macro condition of the current job economy.

The block should name:

- `Shared Provider Concentration`: where many jobs depend on the same provider, model, auth path, or base URL
- `Dependency Choke Points`: which shared artifacts, routes, repos, or operator surfaces could stall many jobs at once
- `Verification Debt`: where unverified claims or stale checks accumulate across the portfolio
- `Synchronized Failure Risk`: which failure mode could take down many loops together
- `Portfolio State`: whether the portfolio is healthy, fragile, or locally green but globally brittle
- `Shared Artifact`: the durable shared surface carrying the portfolio view across runs

## Repo-visible surfaces

The contract is exposed in four places:

1. `hadto_patches/cron_jobs.py` trust-contract snapshots now include `dignity_check`, `capability_check`, `viability_check`, `fast_loop_surfaces`, and `slow_loop_surfaces`
2. `hadto_patches/cron_jobs.py` parses recent outputs for `First Proof Point`, `Geometry Shaping`, `Value Surfaces`, and `Aggregate Stewardship`, then rolls them into the topology snapshot
3. `hadto_patches/cron_jobs.py` also parses `Priority Scoring` blocks so lane choice, gating, weighted inputs, and outrank reasons stay visible in topology
4. `hadto_patches/cron_scheduler.py` prompts recurring jobs to report those field sets in saved outputs
5. `hadto_patches/cron_cli.py` prints the aggregate stewardship portfolio summary alongside trust contracts in `hermes cron topology`
