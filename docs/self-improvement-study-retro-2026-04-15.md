# Study-Derived Self-Improvement Retro

_Date: 2026-04-15_

## Why this exists

Hermes self-improvement was enabled before the ontology and E-Myth study loops had a reliable habit of turning durable lessons into Hermes-owned follow-through. The result was real reading, real domain backlog movement, and almost no explicit self-improvement capture.

This note retroactively converts that study body into concrete Hermes self-improvement themes.

It is intentionally repo-visible so the intake is inspectable, reusable on a new machine, and not trapped in chat history.

## Source evidence

Current repo-visible sources:
- `smb-ontology-platform/docs/plans/2026-03-31-keet-ontology-engineering-progress-tracker.md`
- `smb-ontology-platform/docs/plans/2026-03-31-keet-ontology-engineering-heartbeat.md`
- `smb-ontology-platform/docs/issues/ONT-016-run-continuous-e-myth-study-program.md`
- `smb-ontology-platform/docs/issues/ONT-026-add-semantic-lifting-governance-for-source-schema-classification-and-app-boundaries.md`

Historical E-Myth sources recovered from git because current `main` no longer carries the full control files:
- `d169839:docs/plans/2026-04-10-e-myth-progress-tracker.md`
- `d169839:docs/plans/2026-04-11-e-myth-role-balance-operating-note.md`
- `d169839:docs/plans/2026-04-14-e-myth-management-system-scorecard.md`
- `d169839:docs/plans/2026-04-14-e-myth-systems-strategy-register.md`
- `d169839:docs/plans/2026-04-14-e-myth-final-operating-doctrine.md`

## What was already fixed today

These changes improve future behavior, but they do not retroactively capture past study:
- `cron/scheduler.py` now injects execution-oriented guidance for `role=study` jobs.
- `~/.hermes/skills/research/hadto-ontology-research-cycle/SKILL.md` now tells the study loop to take Hermes self-improvement action when the gap is Hermes-owned.
- `~/.hermes/cron/jobs.json` now asks the ontology research cycle for one explicit action taken, not just a summary.

## Retro themes

### 1. Hermes needs a study candidate ledger, not just scattered notes

Repeated signals:
- Keet Chapter 7.1 and 7.2.1 drove `ONT-026`, which says Hadto needs a reviewable semantic-lifting / candidate ledger between source capture and ontology commitments.
- The ontology research loop exposed the same structural problem in Hermes: findings could jump from source material to summaries or backlog notes without a reviewable candidate surface.
- E-Myth Chapters 10-19 repeatedly say improvements need an ordered business-development program, explicit checkpoints, and a default way to convert repeated gaps into system changes.

Hermes-owned implication:
- Study findings need a durable candidate ledger with evidence, lane, owner, disposition, and closure reason.
- The disposition must say whether the finding became domain backlog, Hermes self-improvement, process note, strategic discussion, or no-action with reason.

Desired Hermes control surface:
- one machine-readable ledger for study-derived candidates
- explicit dispositions instead of buried tracker prose
- replayable intake across restarts and new machines

### 2. Hermes needs to own managerial contracts, not just delegation or reporting

Repeated signals:
- The E-Myth role-balance operating note says Hermes should own managerial order.
- The same note says delegation must include a named outcome, proof requirements, review cadence, escalation boundary, and closure rule.
- The management scorecard says repeated exceptions should become checklist, manual, or system changes instead of founder rescue.

Hermes-owned implication:
- A delegated Codex task or recurring cron loop is incomplete unless Hermes creates and maintains the managerial contract around it.
- "Work happened" is not enough. Hermes has to keep the acceptance contract, proof surface, and handoff state current.

Desired Hermes control surface:
- explicit delegation contract template
- required proof surface for recurring loops
- clear rule for when ambiguity stays with Hermes vs escalates to David

### 3. Hermes needs quantified workflow improvement and default promotion

Repeated signals:
- E-Myth Chapter 10 says improvement must follow Innovation -> Quantification -> Orchestration.
- Chapter 15 says the system is the solution; repeated misses should become defaults, checklists, and scorecards.
- Chapter 19 says comfort-driven relapse shows up when rescue or private memory feels easier than the written system.

Hermes-owned implication:
- Hermes should not claim a workflow fix unless it can name the changed behavior, the proof signal, and the rule for promoting the improvement into a default.
- Successful improvisations should be harvested into prompts, checklists, SOP notes, tests, or runtime guardrails.

Desired Hermes control surface:
- before/after signals for workflow changes
- an explicit promotion path from experiment to default
- comfort-zone alarms when rescue or undocumented exceptions repeat

### 4. Hermes needs contract-first ontology review heuristics

Repeated signals from the Keet loop:
- competency questions need template and query-ready contracts (`ONT-010`)
- ontology review needs pitfall/TIPS guidance, not just pass/fail output (`ONT-008`)
- relation semantics need explicit review, not only class-level checks (`ONT-007`)
- taxonomy quality needs role-vs-kind scrutiny, not only logical consistency (`ONT-006`)
- debugging needs explanation-grade evidence, not only flat issue lists (`ONT-005`)
- ontology authoring needs explicit micro-level governance for modeling choices (`ONT-004`)

Hermes-owned implication:
- When Hermes studies, proposes, or reviews ontology work, it should ask contract and governance questions up front instead of treating green validation or free-form notes as sufficient evidence.
- The ontology loop should produce reviewable proposals and operator-facing explanations, not just more findings.

Desired Hermes control surface:
- reusable ontology review heuristics in prompts and skills
- contract-first proposal requirements
- explanation-grade validation expectations

## Immediate self-improvement work to materialize

The retro should land as explicit Hermes Self-Improvement issues, not only as this note:
1. add a study candidate ledger and disposition surface
2. enforce managerial contracts for delegation and recurring loops
3. quantify workflow experiments and promote verified improvements into defaults
4. carry contract-first ontology review heuristics into Hermes prompts and proposal loops

The machine-readable replay surface for those issues lives in:
- `docs/self-improvement-study-retro-2026-04-15.yaml`
- `tools/upsert_study_retro_issues.py`

Current write-back status:
- replay attempted on 2026-04-15
- Linear rejected all four issue upserts with `usage limit exceeded`
- the YAML plus replay script are therefore the current durable control surface until Linear accepts writes again

## Operating rule after this retro

If a study loop names a Hermes-owned gap and no Hermes self-improvement issue, candidate, or explicit no-action reason appears, the loop is incomplete even if the domain tracker was updated.
