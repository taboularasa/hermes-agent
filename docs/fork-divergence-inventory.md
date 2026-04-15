# Fork Divergence Inventory

Generated on 2026-04-15 from:

```bash
git diff --stat upstream/main...HEAD
git diff --name-only upstream/main...HEAD
```

Baseline before the first sealing pass: `98 files changed, 6680 insertions, 498 deletions`.

Current state after the second sealing pass:
- fork-facing `HADTO-PATCH` markers remain at `46`
- cron, toolset/registry, and most gateway transport modules now sit behind
  compatibility wrappers backed by `hadto_patches/*`
- raw diff file count is `105` because the sealed package and documentation are
  tracked files, even though the public patch surface is narrower

## Inventory

| file | category | sealable? | notes |
| --- | --- | --- | --- |
| `.dockerignore` | policy/config | yes | Local packaging and build hygiene. |
| `.github/dependabot.yml` | policy/config | yes | Local dependency and action pinning policy. |
| `.github/workflows/deploy-site.yml` | policy/config | yes | Local deploy workflow adjustments. |
| `.github/workflows/docker-publish.yml` | policy/config | yes | Local registry and publish policy. |
| `.github/workflows/docs-site-checks.yml` | policy/config | yes | CI policy only. |
| `.github/workflows/nix.yml` | policy/config | yes | Nix CI hardening. |
| `.github/workflows/supply-chain-audit.yml` | policy/config | yes | Supply-chain policy only. |
| `.github/workflows/tests.yml` | policy/config | yes | CI defaults and coverage policy. |
| `AGENTS.md` | policy/config | yes | Local operator rules for this host. |
| `Dockerfile` | policy/config | yes | Local build and security defaults. |
| `acp_adapter/entry.py` | adapter | yes | Thin Doppler bootstrap at process start. |
| `agent/anthropic_adapter.py` | upstream candidate | no | Generic Anthropic auth/output-limit improvements. |
| `agent/redact.py` | upstream candidate | no | Generic secret redaction hardening. |
| `agent/trajectory.py` | upstream candidate | no | Redacts trajectories before persistence. |
| `cli.py` | adapter | yes | CLI Doppler bootstrap and local startup policy. |
| `cron/jobs.py` | adapter | yes | Wrapper onto sealed cron job storage and topology logic. |
| `cron/scheduler.py` | adapter | yes | Wrapper onto sealed cron scheduler logic. |
| `gateway/builtin_hooks/boot_md.py` | inline patch | no | BOOT.md integrity verification. |
| `gateway/channel_directory.py` | adapter | yes | Wrapper onto sealed channel-directory behavior. |
| `gateway/config.py` | adapter | yes | Wrapper onto sealed gateway config policy. |
| `gateway/host_apps.py` | adapter | yes | Wrapper onto sealed host-app discovery. |
| `gateway/platforms/api_server.py` | adapter | yes | Wrapper onto sealed API-server adapter behavior. |
| `gateway/platforms/slack.py` | adapter | yes | Wrapper onto sealed Slack adapter behavior. |
| `gateway/platforms/sms.py` | adapter | yes | Wrapper onto sealed SMS adapter behavior. |
| `gateway/platforms/webhook.py` | adapter | yes | Wrapper onto sealed webhook adapter behavior. |
| `gateway/run.py` | adapter | yes | Wrapper onto sealed gateway composition root. |
| `gateway/session.py` | inline patch | no | Secure permissions for session artifacts. |
| `hermes_cli/commands.py` | adapter | yes | Wrapper onto sealed command-registry behavior. |
| `hermes_cli/config.py` | adapter | yes | Calls sealed env resolver plus local config defaults. |
| `hermes_cli/cron.py` | adapter | yes | Wrapper onto sealed cron CLI behavior. |
| `hermes_cli/ctx_runtime.py` | adapter | yes | Wrapper for sealed ctx.rs domain. |
| `hermes_cli/env_loader.py` | adapter | yes | Wrapper for sealed Doppler domain. |
| `hermes_cli/gateway.py` | adapter | yes | Wrapper onto sealed gateway CLI behavior. |
| `hermes_cli/main.py` | policy/config | yes | Env-aware provider setup flows and local CLI UX. |
| `hermes_cli/status.py` | inline patch | no | ctx/runtime-specific status reporting. |
| `hermes_state.py` | upstream candidate | no | Secret redaction before DB persistence. |
| `rl_cli.py` | policy/config | yes | RL startup uses Doppler and local cwd policy. |
| `run_agent.py` | inline patch | no | Mixed ctx.rs binding, provider, and runtime behavior. |
| `slack-manifest.json` | policy/config | yes | Local Slack app surface definition. |
| `slack-manifest.yaml` | policy/config | yes | Local Slack app surface definition. |
| `tests/agent/test_subagent_progress.py` | policy/config | yes | Regression coverage for delegate behavior. |
| `tests/conftest.py` | policy/config | yes | Test-time Doppler isolation. |
| `tests/cron/test_jobs.py` | policy/config | yes | Cron taxonomy and persistence coverage. |
| `tests/cron/test_scheduler.py` | policy/config | yes | Scheduler regression coverage. |
| `tests/cron/test_scheduler_fallback.py` | policy/config | yes | Fallback-model regression coverage. |
| `tests/gateway/test_api_server_jobs.py` | policy/config | yes | API-server metadata regression coverage. |
| `tests/gateway/test_apps_command.py` | policy/config | yes | Host-apps coverage. |
| `tests/gateway/test_home_channel_prompt.py` | policy/config | yes | Gateway UX regression coverage. |
| `tests/gateway/test_runner_startup_failures.py` | policy/config | yes | Restart-recovery regression coverage. |
| `tests/gateway/test_slack.py` | policy/config | yes | Slack patch regression coverage. |
| `tests/hermes_cli/test_config.py` | policy/config | yes | Env resolver regression coverage. |
| `tests/hermes_cli/test_cron.py` | policy/config | yes | Cron CLI regression coverage. |
| `tests/hermes_cli/test_ctx_runtime.py` | policy/config | yes | ctx seam regression coverage. |
| `tests/hermes_cli/test_env_loader.py` | policy/config | yes | Doppler seam regression coverage. |
| `tests/hermes_cli/test_gateway_service.py` | policy/config | yes | Gateway service regression coverage. |
| `tests/test_agent_guardrails.py` | policy/config | yes | Security hardening regression coverage. |
| `tests/test_cli_ctx_mode.py` | policy/config | yes | ctx CLI regression coverage. |
| `tests/test_cli_provider_resolution.py` | policy/config | yes | Provider/TTY regression coverage. |
| `tests/test_fallback_model.py` | policy/config | yes | Fallback-model regression coverage. |
| `tests/test_quick_commands.py` | policy/config | yes | Quick-command policy coverage. |
| `tests/test_run_agent.py` | policy/config | yes | run-agent regression coverage. |
| `tests/test_run_agent_ctx_runtime.py` | policy/config | yes | ctx run-agent coverage. |
| `tests/test_toolsets.py` | policy/config | yes | Plugin/toolset regression coverage. |
| `tests/tools/test_browser_camofox.py` | policy/config | yes | Web-provider config regression coverage. |
| `tests/tools/test_command_guards.py` | policy/config | yes | Approval/security regression coverage. |
| `tests/tools/test_cronjob_tools.py` | policy/config | yes | Cron tool regression coverage. |
| `tests/tools/test_delegate.py` | policy/config | yes | Delegate behavior regression coverage. |
| `tests/tools/test_local_persistent.py` | policy/config | yes | Local-env and approval coverage. |
| `tests/tools/test_send_message_tool.py` | policy/config | yes | Messaging regression coverage. |
| `tests/tools/test_skill_env_passthrough.py` | policy/config | yes | Env propagation coverage. |
| `tests/tools/test_skills_tool.py` | policy/config | yes | Skill/plugin regression coverage. |
| `tests/tools/test_todo_tool.py` | policy/config | yes | Todo behavior regression coverage. |
| `tests/tools/test_transcription.py` | policy/config | yes | Env bootstrap regression coverage. |
| `tests/tools/test_web_tools_config.py` | policy/config | yes | Web routing regression coverage. |
| `tests/tools/test_yolo_mode.py` | policy/config | yes | Security regression coverage. |
| `tools/__init__.py` | adapter | yes | Import-time env bootstrap seam. |
| `tools/approval.py` | adapter | yes | Wrapper for sealed command-approval domain. |
| `tools/cronjob_tools.py` | adapter | yes | Wrapper onto sealed cron tool behavior. |
| `tools/delegate_tool.py` | inline patch | no | Dynamic toolset listing and concurrency behavior. |
| `tools/environments/docker.py` | adapter | yes | Security/approval config passthrough. |
| `tools/environments/local.py` | inline patch | no | Writable temp root and shell hygiene. |
| `tools/environments/singularity.py` | adapter | yes | Security/approval config passthrough. |
| `tools/environments/ssh.py` | adapter | yes | Security/approval config passthrough. |
| `tools/file_tools.py` | inline patch | no | Sensitive read deny-list. |
| `tools/mcp_tool.py` | adapter | yes | Env bootstrap before MCP discovery. |
| `tools/process_registry.py` | adapter | yes | Crash-recovery/process checkpoint behavior. |
| `tools/registry.py` | adapter | yes | Wrapper onto sealed tool-registry behavior. |
| `tools/send_message_tool.py` | adapter | yes | Wrapper onto sealed messaging transport behavior. |
| `tools/terminal_tool.py` | adapter | yes | Bridge from core terminal tool into ctx/security seams. |
| `tools/tirith_security.py` | upstream candidate | no | Generic fail-closed scanner behavior. |
| `tools/todo_tool.py` | upstream candidate | no | Todo behavior divergence is generic, not Hadto-specific. |
| `tools/url_safety.py` | upstream candidate | no | Generic URL scheme restriction. |
| `tools/web_tools.py` | inline patch | no | Provider routing and availability inspection. |
| `toolsets.py` | adapter | yes | Wrapper onto sealed toolset synthesis and resolution. |
| `trajectory_compressor.py` | upstream candidate | no | Generic async client injection. |
| `website/docs/developer-guide/cron-internals.md` | policy/config | yes | Docs only. |
| `website/docs/user-guide/configuration.md` | policy/config | yes | Docs only. |
| `website/docs/user-guide/features/cron.md` | policy/config | yes | Docs only. |

## Extension Points

| interface | methods / signatures | upstream call sites | replaces current fork files | existing? |
| --- | --- | --- | --- | --- |
| `EnvResolver` | `load_runtime_env(*, hermes_home=None, project_env=None, strict=None) -> dict[str, str]`; `ensure_env_write_allowed(key: str) -> None`; `get_runtime_env_value(key: str) -> str | None` | process entrypoints, config helpers, MCP/tool bootstrap | `hermes_cli/env_loader.py`, env-related pieces of `hermes_cli/config.py`, `cli.py`, `rl_cli.py`, `acp_adapter/entry.py`, `tools/__init__.py`, `tools/mcp_tool.py` | new sealed seam in `hadto_patches.env` |
| `CtxBindingPort` | `maybe_bind_ctx_session(...) -> CtxBinding`; `normalize_ctx_bindings(...) -> dict[str, str]`; `retire_ctx_binding(...) -> bool`; `get_task_cwd(task_id, default=None) -> str | None`; `register_task_env_overrides(task_id, overrides)` | `run_agent.py`, `tools/terminal_tool.py`, scheduler cleanup | `hermes_cli/ctx_runtime.py`, ctx bits in `run_agent.py`, task-override logic in `tools/terminal_tool.py` | new sealed seam in `hadto_patches.ctx`; plugin hooks already consume ctx state indirectly |
| `CommandApprovalPolicy` | `check_dangerous_command(command, env_type, approval_callback=None, container_config=None) -> dict`; `check_all_command_guards(...) -> dict`; `approve_session(...)`; `approve_permanent(...)` | terminal and gateway approval paths | `tools/approval.py`, security-sensitive call sites in `tools/terminal_tool.py`, `gateway/run.py` | new sealed seam in `hadto_patches.security` |
| `CronLifecycleHooks` | `before_tick(job) -> None`; `after_tick(job, result) -> None`; `resolve_job_metadata(job) -> dict`; `retire_session_bindings(job) -> None` | `cron/scheduler.py`, `cron/jobs.py`, `tools/cronjob_tools.py`, `hermes_cli/cron.py` | current role/scope/topology/fallback logic spread across cron modules | new |
| `GatewayTransportHooks` | `before_send(platform, payload) -> payload`; `validate_inbound(platform, request) -> ValidationResult`; `list_host_apps() -> list[HostApp]`; `augment_commands(commands) -> commands` | `gateway/run.py`, `gateway/platforms/*`, `gateway/channel_directory.py` | `gateway/platforms/sms.py`, `gateway/platforms/webhook.py`, `gateway/platforms/api_server.py`, `gateway/host_apps.py`, parts of `hermes_cli/commands.py` | plugin system covers hooks broadly, but not transport-specific validation yet |
| `SecurityPolicyHooks` | `check_file_read(path) -> bool`; `check_boot_integrity() -> None`; `sanitize_persisted_content(text) -> str`; `normalize_url(url) -> bool` | file tools, gateway boot hooks, persistence, web tools | `tools/file_tools.py`, `gateway/builtin_hooks/boot_md.py`, `hermes_state.py`, `agent/redact.py`, `tools/url_safety.py` | partially present via existing hooks; new explicit port needed |
| `ToolsetResolver` | `register_toolset(name, tools, description, aliases=None)`; `resolve_toolset(name) -> list[str]`; `list_toolsets() -> dict[str, dict]` | `toolsets.py`, `tools/registry.py`, CLI toolset UX | current plugin-registry and toolset synthesis logic | plugin system registers tools, but not first-class toolsets |
| `ProviderRouter` | `resolve_provider(config, env) -> ProviderSelection`; `describe_provider_status() -> dict`; `resolve_web_backend() -> str` | `run_agent.py`, status CLI, web tools, Anthropic adapter | mixed provider logic in `run_agent.py`, `hermes_cli/status.py`, `tools/web_tools.py`, `agent/anthropic_adapter.py` | partially exists; needs explicit seam |
| `TelemetryStatusHooks` | `describe_runtime_status() -> list[StatusItem]`; `describe_cron_topology() -> dict`; `describe_ctx_status() -> dict` | `hermes_cli/status.py`, gateway status surfaces, host diagnostics | status and topology logic currently spread across CLI + gateway | new |

## Existing vs New

- Already covered by the Hermes plugin system:
  - tool registration
  - session lifecycle hooks
  - pre/post LLM hooks
  - skill registration
  - plugin-provided slash commands
- Not yet covered cleanly and still causing inline fork patches:
  - env/config resolution
  - security policy hooks
  - cron scheduler/job lifecycle hooks
  - toolset resolution for plugin-defined tool groups
  - transport/platform validation hooks
  - provider/router status hooks
  - ctx.rs task-binding port

## Recommended Next Upstream Candidates

- `agent/anthropic_adapter.py`
- `agent/redact.py`
- `agent/trajectory.py`
- `hermes_state.py`
- `tools/tirith_security.py`
- `tools/url_safety.py`
- `trajectory_compressor.py`
