# Proposal: `on_agent_init` Hook and Per-Session CWD Overrides

## Summary

Hermes plugins can already hook session and LLM lifecycles, but they still lack
two small extension points needed for workspace-aware runtime integrations:
process initialization before the first session, and a first-class way to set a
session-specific working directory. Adding `on_agent_init` and `session_cwd`
would eliminate the remaining ctx.rs-related fork patches while staying fully
backward compatible for existing plugins and sessions.

## Motivation

The remaining ctx.rs fork patches fall into two buckets:

1. Process-level setup that must happen once when the gateway starts.
2. Per-session working-directory overrides so terminal/file tools operate in a
   task-specific worktree instead of the gateway's own checkout.

Today, the plugin system exposes `on_session_start`, `pre_llm_call`,
`post_tool_call`, and similar hooks, but nothing that fires once after plugin
discovery and before the agent starts serving traffic. That forces plugins with
runtime state to use import-time side effects or fork-local bootstrap code.

Likewise, tools currently assume the gateway process working directory unless a
fork patch injects a task-specific override. Workspace managers such as ctx.rs
need a supported way to tell Hermes, "for this session, use this worktree as
the filesystem base."

These two additions would replace the fork-local seams currently carried in:

- `hermes_cli/ctx_runtime.py`
- `run_agent.py`
- `tools/terminal_tool.py`
- the corresponding ctx-related compatibility layers documented in
  `docs/fork-divergence-inventory.md`

## Proposed API

### 1. `on_agent_init`

Add `on_agent_init` to the plugin hook registry and fire it once during gateway
startup, after plugin discovery/registration and before Hermes begins handling
connections.

Suggested hook registration:

```python
ctx.register_hook("on_agent_init", callback)
```

Suggested callback signature:

```python
def on_agent_init(context) -> None:
    ...
```

Suggested context fields:

- `gateway_config`: resolved gateway configuration object/dict
- `registered_tools`: list of tool names registered so far
- `plugin_metadata`: the current plugin's manifest metadata

Suggested call timing:

1. discover plugins
2. register plugin tools/hooks/skills
3. call each plugin's `on_agent_init`
4. start transports / accept sessions

This is intentionally process-scoped rather than session-scoped.

#### Use cases

- establish daemon bindings (ctx.rs, job queues, service brokers)
- warm credentials or caches
- validate external dependencies once at startup
- register background health checks

### 2. Per-session CWD override

Add an optional `session_cwd` field to the session/runtime context. When set,
filesystem-aware tools use it as their base directory for that session.

Suggested shape:

```python
session_context.session_cwd: str | None
```

Suggested usage from plugins:

```python
def on_session_start(session_context):
    worktree = resolve_worktree(session_context.session_id)
    if worktree:
        session_context.session_cwd = worktree
```

Suggested core behavior:

- `terminal_tool` uses `session_cwd` as the working directory default
- `file_tools` resolves relative paths from `session_cwd`
- if `session_cwd` is unset, Hermes behaves exactly as it does today

This keeps the override local to the session rather than global to the
gateway process.

## Backward Compatibility

Both additions are backward compatible:

- plugins that do not register `on_agent_init` are unaffected
- sessions without `session_cwd` continue using the current gateway working
  directory behavior
- existing tool call signatures do not need to change

The new hook is additive, and the new session field is optional.

## Reference Implementation

The current fork carries this behavior through sealed compatibility layers and
ctx.rs integration shims:

- `hermes_cli/ctx_runtime.py`
- `tools/terminal_tool.py`
- `run_agent.py`
- `docs/fork-divergence-inventory.md` (`CtxBindingPort` entry)

In the current fork, ctx.rs binds a session to a workspace/worktree and
`get_task_cwd(...)` resolves the per-task directory used by terminal
operations. Upstream support for `on_agent_init` and `session_cwd` would remove
the need for those fork-local call-site patches and make workspace-aware
plugins first-class.
