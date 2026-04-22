---
sidebar_position: 4
title: "Fast loops and slow governance"
description: "Define what Hermes may change during execution loops versus what requires explicit governance revision"
---

# Fast loops and slow governance

Hermes runs on two coupled timescales:

- **Fast loops** execute inside the current ruleset.
- **Slow loops** revise the ruleset itself.

This contract keeps recurring jobs, self-improvement passes, and delegated implementation work from silently rewriting their own guardrails under local pressure.

## The contract

A run is still a **fast loop** when it only:

- selects from already-defined backlog, benchmark, or routing rules
- executes a delegated task against an already-scoped issue or prompt
- reruns checks using existing success criteria
- posts status, evidence, or merge decisions using existing workflow rules
- opens an escalation issue, PR, or review thread without changing policy yet

A run has crossed into a **slow loop** when it proposes or applies a change to:

- system prompts, recurring-job policy, or delegation policy
- reward-policy, benchmark, or verification criteria
- issue-selection heuristics or coordination rules
- model-routing defaults, approval policy, or trust contracts
- the set of control surfaces a fast loop is allowed to modify

## Surface map

| Surface | Fast loop allowed | Slow loop required |
| --- | --- | --- |
| Recurring cron jobs | Run the current prompt, deliver output, pause on existing guardrails, open a follow-up issue | Change job topology, rewrite recurring policy, loosen scheduler safety rules |
| Self-improvement loop | Execute a benchmark-defined candidate, report current evidence, open a policy-review issue | Change benchmark scoring, reward-policy assumptions, or what counts as passing evidence |
| Delegated implementation runs | Implement the selected issue, run existing tests, update the current PR or issue | Change delegation ownership rules, broaden the issue contract, or rewrite acceptance criteria |
| Backlog coordination | Select the next issue from the current policy, report active work, merge already-approved work | Change repo routing, coordinator ranking rules, or the meaning of Hermes-owned work |

## Operator-visible checkpoint

Every loop that might touch governance should emit or write a checkpoint in operator-facing output.

Use this shape:

```text
Fast/slow checkpoint:
- loop: <cron|self-improvement|delegation|coordination>
- mode: <fast|slow>
- reason: <why this stayed in execution mode or escalated>
- governance surfaces touched: <none|list>
- escalation target: <issue/PR/doc or none>
```

A fast loop should usually say `governance surfaces touched: none`.
A slow loop should point at the review surface that now owns the rule change.

## Escalation rule

When a fast loop discovers that success requires changing the rules of the game, it must stop at the boundary and create a slow-loop artifact instead of mutating policy inline.

Acceptable slow-loop artifacts include:

- a Linear issue that scopes the rule change
- a PR that changes the governing document or control surface
- a benchmark or reward-policy revision with explicit before/after criteria
- a workflow note that records who now owns the rule revision

## Worked examples

### Recurring cron job

```text
Fast/slow checkpoint:
- loop: cron
- mode: fast
- reason: Daily report used the existing schedule, prompt, and delivery target.
- governance surfaces touched: none
- escalation target: none
```

If the same job decides the current schedule is wrong or a sibling job should be retired, that proposal becomes a slow-loop change and must land in a review surface instead of being silently rewritten inside the cron run.

### Self-improvement pass

```text
Fast/slow checkpoint:
- loop: self-improvement
- mode: slow
- reason: The candidate fix requires changing benchmark criteria, not just improving delivery under the current benchmark.
- governance surfaces touched: benchmark scoring; reward-policy assumptions
- escalation target: HAD-428 / governance PR
```

### Delegated implementation run

```text
Fast/slow checkpoint:
- loop: delegation
- mode: fast
- reason: The worker implemented the selected issue and verified against existing tests.
- governance surfaces touched: none
- escalation target: none
```

If the worker discovers that the acceptance criteria themselves are unsafe or incomplete, it should stop and open the governing issue or PR rather than silently redefining done.

## Default decision test

Use this test before changing anything:

1. **Am I choosing among existing rules, or rewriting them?**
2. **Would this change alter future selection, scoring, or approval behavior?**
3. **Can I point to an existing issue, PR, or benchmark that already authorized the change?**

If the answer to the second question is yes and the third is no, the run has hit a slow-loop boundary.
