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
