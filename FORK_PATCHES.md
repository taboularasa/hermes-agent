# Fork Patch Manifest

This manifest tracks every current `HADTO-PATCH` marker in the fork after the
second sealing pass. `line range` is intentionally approximate for module-wide
patches and exact only for localized blocks that matter during upstream merges.

| file | line range | patch category | description | upstream-able? |
| --- | --- | --- | --- | --- |
| `hadto_patches/security.py` | `1-EOF` | sealed-local-domain | Sealed copy of the command approval/security policy. | no |
| `run_agent.py` | `2-EOF` | ctx.rs integration | Session binding, ctx note injection, and local runtime behavior. | partial |
| `toolsets.py` | `2-EOF` | plugin registry | Compatibility wrapper onto the sealed toolset resolution domain. | no |
| `rl_cli.py` | `2-EOF` | env | RL CLI startup loads Doppler-managed runtime secrets. | no |
| `cli.py` | `2-EOF` | env | Main CLI startup loads Doppler-managed runtime secrets. | no |
| `hermes_cli/env_loader.py` | `1-EOF` | env | Compatibility wrapper onto the sealed Doppler resolver. | no |
| `trajectory_compressor.py` | `2-EOF` | trajectory compression | Async-client and compression behavior diverges from upstream. | yes |
| `hermes_cli/ctx_runtime.py` | `1-EOF` | ctx.rs integration | Compatibility wrapper onto the sealed ctx.rs adapter. | no |
| `hermes_state.py` | `2-EOF` | security | Redacts persisted content before DB writes. | yes |
| `hermes_cli/config.py` | `1-EOF` | env | Env helpers delegate to sealed Doppler resolver. | no |
| `hermes_cli/cron.py` | `1-EOF` | cron topology | Compatibility wrapper onto the sealed cron CLI domain. | no |
| `hermes_cli/status.py` | `1-EOF` | ctx.rs integration | Status output includes local ctx/runtime details. | partial |
| `hermes_cli/gateway.py` | `1-EOF` | gateway composition | Compatibility wrapper onto the sealed gateway CLI domain. | no |
| `hermes_cli/commands.py` | `1-EOF` | gateway composition | Compatibility wrapper onto the sealed command-registry domain. | no |
| `gateway/session.py` | `1-EOF` | misc | Session artifacts are chmod’d owner-only. | yes |
| `tools/approval.py` | `1-EOF` | security | Compatibility wrapper onto the sealed approval policy. | no |
| `tools/todo_tool.py` | `2-EOF` | todo behavior | Local todo behavior diverges from upstream planner semantics. | yes |
| `tools/registry.py` | `1-EOF` | plugin registry | Compatibility wrapper onto the sealed tool-registry domain. | no |
| `tools/delegate_tool.py` | `2-EOF` | delegation | Dynamic toolset listing and concurrency policy. | yes |
| `tools/web_tools.py` | `2-EOF` | web provider routing | Multi-provider status and routing behavior. | yes |
| `tools/terminal_tool.py` | `2-EOF` | ctx.rs integration + security | Terminal bridges to ctx task cwd resolution and security policy. | partial |
| `tools/process_registry.py` | `1-EOF` | process registry | Background process checkpoint recovery. | partial |
| `gateway/run.py` | `1-EOF` | gateway composition | Compatibility wrapper onto the sealed gateway runner domain. | no |
| `hadto_patches/gateway_run.py` | `1202-1229` | codex restart recovery | Restart-time import shim for interrupted Codex run recovery. | no |
| `tools/file_tools.py` | `2-EOF` | security | Sensitive read deny-list for local credentials and secrets. | yes |
| `gateway/host_apps.py` | `1-EOF` | misc | Compatibility wrapper onto the sealed host-app discovery domain. | no |
| `tools/cronjob_tools.py` | `1-EOF` | security | Compatibility wrapper onto the sealed cron tool domain. | no |
| `tools/__init__.py` | `2-EOF` | env | Tool package import path preloads runtime env via Doppler. | no |
| `gateway/channel_directory.py` | `1-EOF` | security | Compatibility wrapper onto the sealed channel-directory domain. | no |
| `tools/mcp_tool.py` | `2-EOF` | env | MCP tool bootstrap ensures runtime secrets are loaded. | no |
| `gateway/config.py` | `1-EOF` | security | Compatibility wrapper onto the sealed gateway-config domain. | no |
| `tools/url_safety.py` | `1-EOF` | security | Restricts URL schemes to `http`/`https` before fetch. | yes |
| `tools/send_message_tool.py` | `1-EOF` | messaging | Compatibility wrapper onto the sealed messaging transport domain. | no |
| `tools/tirith_security.py` | `1-EOF` | security | Fail-closed Tirith scanner defaults. | yes |
| `gateway/builtin_hooks/boot_md.py` | `1-EOF` | security | Optional SHA-256 BOOT.md integrity check. | yes |
| `gateway/platforms/sms.py` | `1-EOF` | security | Compatibility wrapper onto the sealed SMS adapter domain. | no |
| `gateway/platforms/api_server.py` | `1-EOF` | security | Compatibility wrapper onto the sealed API-server adapter domain. | no |
| `gateway/platforms/slack.py` | `1-EOF` | security | Compatibility wrapper onto the sealed Slack adapter domain. | no |
| `tools/environments/local.py` | `1-EOF` | security | Writable temp-root and clean shell handling. | partial |
| `gateway/platforms/webhook.py` | `1-EOF` | security | Compatibility wrapper onto the sealed webhook adapter domain. | no |
| `cron/scheduler.py` | `1-EOF` | cron topology | Compatibility wrapper onto the sealed cron scheduler domain. | no |
| `cron/jobs.py` | `1-EOF` | cron topology | Compatibility wrapper onto the sealed cron job-storage domain. | no |
| `acp_adapter/entry.py` | `1-EOF` | env | ACP entrypoint preloads runtime secrets via Doppler. | no |
| `agent/redact.py` | `1-EOF` | security | Two-tier secret redaction patterns. | yes |
| `agent/anthropic_adapter.py` | `1-EOF` | provider/auth | Anthropic auth and output-capacity handling diverge. | yes |
| `agent/trajectory.py` | `1-EOF` | security | Trajectory persistence redacts secrets before write. | yes |
