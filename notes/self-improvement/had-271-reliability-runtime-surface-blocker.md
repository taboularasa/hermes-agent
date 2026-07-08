# HAD-271 Reliability Runtime Surface Blocker

Date: 2026-06-01

## Evidence

- `origin/main` includes PR #140 at merge commit `615afe8d6a2ebeba6fd5bf865cbff869ed63fc59`.
- The active cron launcher at `/home/david/.hermes/scripts/hadto_self_improvement_pipeline.py` imports `hadto_hermes_plugin.tools.self_improvement` from `/home/david/.hermes/plugins/hadto`, not the repo-local `tools/self_improvement_tool.py`.
- The latest persisted benchmark history entry at `2026-06-01T07:12:25.362629+00:00` is in the legacy `evaluations` format and reports `score=78.72`, `critical_failures=["reliability_gate","leading_indicator_drift"]`, `reliability_gate=0.45`, `execution_loop=0.6`, and `leading_indicator_drift=0.5`.
- A repo-local `evaluate_self_improvement_pipeline(persist=False)` run from `/home/david/stacks/hermes-agent` on current `origin/main` reports `reliability_gate=1.0 pass`; the remaining local failures are from claimed-work and operator-value evidence, then `leading_indicator_drift`.

## Blocker

HAD-271 cannot be closed from PR #140 alone while the operational cron path continues to execute the installed Hadto plugin surface and write legacy benchmark entries. A repo fix to `tools/self_improvement_tool.py` must either be deployed into the active plugin path or the cron launcher must be switched to the repo-local self-improvement tool before cron benchmark evidence can prove the repaired reliability gate.

## Follow-up

This PR also fixes one repo-local scoring defect found during the reproduction: failed Codex attempts with `completed_at` are no longer counted as completed deliveries or claimed work. That correction keeps failed/stale attempts from inflating execution-loop throughput or dragging operator-value evidence as if they were shipped work.

## 2026-06-05 Follow-up After PRs #154 and #155

### Evidence

- This checkout was fast-forwarded to `origin/main` at merge commit `80b36d74c9437a1245427c0d7c5002635f353c0b` for PR #155.
- Repo-local `tools.self_improvement_tool.self_improvement_benchmark(ontology_root="/home/david/stacks/HAD-1265-smb-ontology-platform-20260605131830", persist=False)` reports `project_score=83.22`, `direction=positive`, `trend=positive`, and `critical_failures=[]`.
- The authoritative manager-owned benchmark still reports `project_score=68.73`, `direction=negative`, `trend=regressing`, and `critical_failures=["anti_make_work_check", "leading_indicator_drift"]`.
- The manager-owned `anti_make_work_check` still names `codex_runs:codex_5e2abf1b3617` and `codex_runs:codex_c3614c2aea99` as `status_language_without_value_category_evidence`.
- Those two Codex aggregate records point at completed Hadto.co blog-delivery runs. Their final messages include durable artifact paths and verification results, but the installed manager-adjacent plugin path does not use the repo-local Codex sidecar hydration added by PR #154.
- The active launcher at `/home/david/.hermes/scripts/hadto_self_improvement_pipeline.py` still imports `hadto_hermes_plugin.tools.self_improvement` from `/home/david/.hermes/plugins/hadto`.
- That installed plugin checkout is `https://github.com/taboularasa/hadto-hermes-plugin.git` on branch `had-1156-runtime-probe-clarity` at `f0a563713d0e71ad14b357148ee5fe860efa6f18`, not this Hermes-agent checkout.
- The installed plugin benchmark implementation builds `anti_make_work_check` through `hadto_hermes_plugin.anti_make_work` and `_anti_make_work_items_for_benchmark`; it lacks the repo-local sidecar hydration path in `tools/self_improvement_tool.py`.
- A delegated-shell attempt to run the installed plugin benchmark with the manager ontology root and `persist=False` stayed CPU-bound for more than four minutes and touched installed self-improvement ledger files, so it was terminated rather than treated as a safe reproducible proof path.

### Blocker

HAD-271 remains blocked in the manager-owned benchmark path because PRs #154 and #155 changed the Hermes-agent repo-local tool, while the live manager-adjacent path is still executing the separate installed Hadto plugin implementation. Treating checkout-local `critical_failures=[]` as recovery would be misleading until the installed plugin/runtime path either imports the Hermes-agent benchmark code or receives an equivalent sidecar-hydration and minor-drift-threshold repair.

This blocker is not solved by adding status text to the named Codex records. The next truthful recovery path is to repair or redeploy the installed `hadto-hermes-plugin` benchmark surface, then rerun the manager-owned `self_improvement_benchmark` with the same ontology root and require `critical_failures=[]`.

## 2026-07-08 Update

Project-manager evidence at `2026-07-08T09:23Z` still reports `self_improvement_evidence_gate` degraded because the durable evidence sources are stale: `journal_entries` latest `2026-07-04T23:15:00+00:00` (about 82h old) and `codex_runs` latest `2026-07-03T23:34:57.411021+00:00` (about 106h old). `ctx` is disabled by config and remains informational. Ontology intelligence is fresh.

This repo no longer contains the self-improvement benchmark implementation. It was extracted to the Hadto plugin in `cfd0c6fc4`. The repo-side repair for this pass is therefore limited to removing the extracted self-improvement tool names from Hermes core platform defaults so a checkout without the Hadto plugin does not advertise stale repo-local providers. The remaining reliability-floor degradation requires real journal and Codex evidence collection in the operational Hadto runtime. It must not be cleared by fabricated freshness.

## 2026-07-08 VM Worker Evidence Follow-up

PR #156 merged at `2026-07-08T09:54:26Z` as `bdd6f88864b4e4a163d80e34a7f1fceed2062993`, and the post-merge repo-local verifier `uv run pytest tests/test_toolsets.py` passed. The next manager benchmark persisted at `2026-07-08T10:06:28Z` still reports `score=76.33`, `direction=negative`, and `critical_failures=["reliability_gate","execution_loop"]`, with `reliability_gate=0.45`, `execution_loop=0.3`, `stale_execution_records=1.0`, and `ontology_readiness=1.0`.

The remaining degradation is now a source-selection problem, not missing repo-local code. The manager evidence gate still evaluates legacy `codex_runs` freshness while current VM worker delivery evidence is recorded in `~/.hermes/vm-workers/runs.json`. A truthful repair must either teach the Hadto plugin's `self_improvement_evidence_gate` / `self_improvement_benchmark` execution-loop checks to count `vm_code_delegate` registry records as execution evidence, or report both sources separately: legacy `codex_runs` stale and VM worker registry fresh. It must not rewrite old Codex records, fabricate journal entries, enable `ctx` by implication, or treat a PR merge alone as operator-outcome evidence.

This Hermes-agent checkout cannot implement that runtime change directly because the benchmark and gate providers are plugin-owned, and this isolated VM does not mount the host's `~/.hermes/vm-workers/runs.json` registry. The repo-backed guard remains that extracted self-improvement tools stay out of all built-in Hermes toolsets; plugin registration is the only correct path for the active Hadto benchmark surface.
