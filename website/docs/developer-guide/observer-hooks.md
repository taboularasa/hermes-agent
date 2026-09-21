---
title: "Observer Hooks"
description: "Read-only telemetry contract for plugins: event families, correlation IDs, payload safety"
---

# Hermes Observer Hooks

Hermes observer hooks are the read-only telemetry contract for plugins that
need to reconstruct agent execution without changing runtime behavior. This
contract supports trace, metrics, audit, replay, and export integrations such
as Langfuse, OpenTelemetry-style collectors, and NeMo Relay.

Observer hooks are intentionally backend-neutral. They expose stable lifecycle
events, correlation IDs, sanitized payloads, timing, status, and error fields.
They do not replace Hermes' planner, model providers, memory, tool registry,
approval UX, CLI, gateway behavior, or execution semantics.

Behavior-changing request or execution wrappers are outside this observer
contract. Observer hooks should report what happened; they should not replace
provider requests, tool arguments, or execution callbacks.

Hermes also has a first-party NeMo Relay shared-metrics path. It uses these
lifecycle boundaries directly and does not require enabling an observability
plugin. See [Relay shared metrics](relay-shared-metrics.md).

## Contract

Plugins register observer callbacks from `register(ctx)`:

```python
def register(ctx):
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
```

Every hook callback receives keyword arguments. Plugins should accept
`**kwargs` so additive fields remain backward-compatible:

```python
def on_post_tool_call(**kwargs):
    tool_name = kwargs.get("tool_name")
    status = kwargs.get("status")
    result = kwargs.get("result")
```

The plugin manager injects this field into every hook payload:

```text
telemetry_schema_version = "hermes.observer.v1"
```

Hook callbacks are fail-open. Hermes catches callback exceptions, logs a
warning, and keeps the agent loop running.

Most observer hook return values are ignored. The exceptions are older
behavior-affecting hooks:

| Hook | Return behavior |
| --- | --- |
| `pre_llm_call` | May return a string or `{"context": "..."}` to inject ephemeral context into the current user message. |
| `pre_tool_call` | May return `{"action": "block", "message": "..."}` to block a tool before execution, or `{"action": "modify", "args": {...}}` to transform the tool's input arguments. |
| `transform_tool_result` | May return a replacement tool result string after `post_tool_call`. |
| `transform_llm_output` | May return a replacement final assistant text string. |

Telemetry plugins should treat these behavior-affecting returns as optional
compatibility features, not as observability requirements.

## Correlation IDs

Observer payloads use stable IDs so plugins can join events without relying on
callback order alone.

| Field | Meaning |
| --- | --- |
| `session_id` | Conversation/session identity. |
| `task_id` | Task identity, especially useful for subagents and isolated execution. |
| `turn_id` | User-turn identity shared by API attempts and tool calls in a turn. |
| `api_request_id` | Opaque provider-attempt identity. Do not parse its string format. |
| `api_call_count` | Numeric API attempt count within the agent loop. |
| `tool_call_id` | Provider-supplied tool call ID when available. |
| `parent_session_id` / `child_session_id` | Session link for delegated subagents. |
| `parent_subagent_id` / `child_subagent_id` | Subagent link when available. |
| `parent_turn_id` | Parent turn that spawned delegated work. |

Consumers should prefer explicit fields over parsing compound IDs. In
particular, `api_request_id` is an opaque correlation value.

## Event Families

### Session Lifecycle

Session hooks describe conversation boundaries and resets:

| Hook | When it fires |
| --- | --- |
| `on_session_start` | A brand-new session starts after the system prompt is built. |
| `on_session_end` | A `run_conversation` call ends, including interrupted or incomplete turns. |
| `on_session_finalize` | CLI or gateway tears down an active session identity. |
| `on_session_reset` | CLI or gateway moves from an old session identity to a new one. |

Common fields include `session_id`, `completed`, `interrupted`, `reason`,
`old_session_id`, and `new_session_id` where available.

`on_session_end` is turn/run scoped. It is not necessarily the final lifetime
boundary for a chat identity. Use `on_session_finalize` and `on_session_reset`
for lifecycle cleanup that must happen once per session identity.

### Turn-Scoped LLM Hooks

These hooks frame the user turn, not individual provider API attempts:

| Hook | When it fires |
| --- | --- |
| `pre_llm_call` | Before the tool loop begins for a user turn. |
| `post_llm_call` | After the turn completes with final assistant output. |

Common `pre_llm_call` fields include `session_id`, `turn_id`,
`user_message`, `conversation_history`, `is_first_turn`, `model`, `platform`,
and `sender_id`.

Common `post_llm_call` fields include `session_id`, `turn_id`,
`user_message`, `assistant_response`, `conversation_history`, `model`, and
`platform`.

Use request-scoped API hooks for LLM span telemetry. Use `pre_llm_call` and
`post_llm_call` for turn-level context, compatibility, and final turn summary.

### Request-Scoped API Hooks

API hooks describe provider attempts inside the agent loop:

| Hook | When it fires |
| --- | --- |
| `pre_api_request` | Before execution middleware and final transport transformations. |
| `post_api_request` | After a successful provider response. |
| `api_request_error` | After a failed provider request or retryable error path. |

#### Opt-in final SDK observation

`api_request_dispatch` is a separate Python plugin subscription immediately before
the supported SDK `create` calls: ordinary Chat Completions non-streaming,
Chat Completions streaming after stream options, and Codex Responses streaming
after Relay, consumer sanitation and the SDK-transform bypass. It does not
replace the sanitized, capped `pre_api_request` telemetry contract or forward
raw payloads through builtin lifecycle telemetry. Without a subscriber, the
new path performs no payload traversal, serialization, capture I/O or observer
worker allocation; ordinary plugin discovery still applies.

```python
def register(ctx):
    ctx.register_hook("api_request_dispatch", observe_dispatch)

def observe_dispatch(sdk_kwargs_json, snapshot_status, observation_window, **event):
    # A collector must join lifecycle terminal events and enforce its own
    # privacy, retention and synchronized late-write/tombstone rules.
    if not observation_window.active or sdk_kwargs_json is None:
        return
```

The callback receives `observation_schema_version="hermes.sdk_request_observation.v1"`,
`representation="sdk_kwargs_projection"`, `route`, `provider`, `api_mode`, and the
existing `session_id`, `task_id`, `turn_id`, `api_request_id`, `retry_count` and
`api_call_count`. IDs are frozen around the call and propagated through existing
worker/Relay ContextVars; unavailable values are `None`, including direct calls
outside that lifecycle scope. These are logical lifecycle IDs, not new episode
IDs or unique identities for SDK-internal retries. The context never enters SDK
kwargs.

`observation_id` is a UUID allocated once per hook emission after the subscription
check, shared by every callback receiving that emission. Repeated dispatches get
different observation IDs even when logical IDs and SDK kwargs are identical.
Collectors can use it to distinguish duplicate delivery from another observed
dispatch. It is not an episode ID, provider attempt ID or evidence of unobserved
SDK retries; it never enters provider kwargs.

`sdk_kwargs_json` is an immutable JSON string containing the supported body-field
projection of the final SDK kwargs, including supported fields under `extra_body`.
It preserves that nesting; it does not simulate the SDK's HTTP-body merge.
`omitted_fields` is an immutable tuple of `(field_path, reason)` pairs. Transport
headers, credentials, SDK options/clients and unknown extensions are omitted.
Fields containing hidden reasoning, non-JSON objects, nonfinite numbers, invalid
Unicode or cycles are omitted whole: values are never truncated, stringified or
replaced with object representations. User-supplied prompt content can itself be
sensitive; this opt-in surface is not a general secret scrubber.

`snapshot_status` is `complete_projection` when no supplied fields were omitted,
`partial_projection` when fields were omitted, or `unavailable` on serialization
failure/resource limits. Unavailable snapshots have `sdk_kwargs_json=None` and
stable `reason_codes`, with no partial JSON. Projection traversal is bounded by
1 MiB of encoded JSON, 50,000 visited nodes, and depth 24; exact builtin JSON types
are supported. See `agent/request_observation.py` for the field allowlist. Even
`complete_projection` describes only supplied SDK kwargs: it is not HTTP-byte
identity, provider receipt, whole-request completeness or permission to retain data.

Callbacks use the existing bounded dispatcher and signature filtering (narrow
callbacks and `**kwargs` both work). This hook remains bounded when the existing
`plugins.hook_callback_timeout` is zero/nonfinite, using the 30-second default;
other hooks retain their existing timeout behavior. Each callback receives a
separate `observation_window` with a monotonic deadline and `active` property.
The window closes on return, exception, worker-start failure or timeout. Timed-out
workers are abandoned and suppressed under existing rules; research proceeds.
Callback return values never establish capture success. An `active` read is an
expiry signal, not an atomic write permission: collectors must synchronize their
own terminal/tombstone checks with finalization and reject delayed completion.
Missing, dropped, failed or late observations remain explicit collection gaps.

The controlled serialization profile uses Python 3.11, OpenAI 2.24.0, httpx 0.28.1
and NeMo Relay 0.8.3 with the default Codex SDK bypass. The route test family runs
real `AIAgent` turns through registered middleware and Relay, then actual SDK
serialization into `httpx.MockTransport`. It checks Chat stream/non-stream and
Codex stream JSON bodies, including `extra_body` precedence, input/tool relocation
and late retention removal. SDK retries are disabled in this proof. Raw serialized
body hashes and observer JSON hashes are separate; the assertion compares only
the permitted body projection. Headers, timeout and Codex `include` remain
explicit omissions, so the observed snapshots are partial projections.

Anthropic/Bedrock, MoA/ACP, auxiliary/summary/compression calls, internal SDK retries,
alternate Codex bypass settings and unwitnessed routes are outside that proof.
Controlled HTTP serialization does not establish live provider receipt, live
profile parity, a deployed collector or complete capture. Those require matching
source/runtime and lifecycle collection evidence. No collector or capture storage
is enabled by this hook addition.

`pre_api_request` includes:

- identity: `session_id`, `task_id`, `turn_id`, `api_request_id`
- runtime: `platform`, `model`, `provider`, `base_url`, `api_mode`
- attempt metadata: `api_call_count`, `message_count`, `tool_count`,
  `approx_input_tokens`, `request_char_count`, `max_tokens`
- timing: `started_at`
- sanitized request payload: `request`

`post_api_request` includes the same identity/runtime fields plus:

- `api_duration`, `started_at`, `ended_at`
- `finish_reason`, `message_count`, `response_model`
- `usage`
- `assistant_content_chars`, `assistant_tool_call_count`
- sanitized response payload: `response`
- compatibility object: `assistant_message`

`api_request_error` includes the same identity/runtime fields plus:

- `api_duration`, `started_at`, `ended_at`
- `status_code`, `retry_count`, `max_retries`, `retryable`, `reason`
- structured `error = {"type": ..., "message": ...}`
- sanitized failed request payload: `request`

The sanitized `request`, `response`, and `error` fields are the canonical
observer inputs for new consumers.

### Tool Lifecycle

Tool hooks describe individual tool calls:

| Hook | When it fires |
| --- | --- |
| `pre_tool_call` | Before guardrail-approved tool dispatch. |
| `post_tool_call` | After tool dispatch, cancellation, block, or error completion. |
| `transform_tool_result` | After `post_tool_call`, before the result is appended to model context. |

`pre_tool_call` includes `tool_name`, `args`, `task_id`, `session_id`,
`tool_call_id`, `turn_id`, and `api_request_id`.

`post_tool_call` includes the same identity fields plus `result`,
`duration_ms`, `status`, `error_type`, and `error_message`.

`status` is the observer-grade lifecycle outcome. Common values include:

| Status | Meaning |
| --- | --- |
| `ok` | Tool completed normally. |
| `error` | Tool ran and returned or raised an error outcome. |
| `blocked` | A `pre_tool_call` hook blocked execution. |
| `cancelled` | Execution was cancelled before normal completion. |

`post_tool_call` is emitted for blocked and cancelled paths so telemetry
plugins can close spans cleanly.

### Approval Lifecycle

Approval hooks describe dangerous-command approval prompts:

| Hook | When it fires |
| --- | --- |
| `pre_approval_request` | Before the approval request is shown or sent. |
| `post_approval_response` | After the user responds or the request times out. |

Common fields include `command`, `description`, `pattern_key`,
`pattern_keys`, `session_key`, and `surface`.

`post_approval_response` also includes `choice`, with values such as `once`,
`session`, `always`, `deny`, and `timeout`.

Approval hooks are observer-only. Plugins cannot pre-answer or veto approvals
from these hooks. To prevent a tool from reaching approval, use
`pre_tool_call` blocking.

### Subagent Lifecycle

Subagent hooks describe delegated child-agent work:

| Hook | When it fires |
| --- | --- |
| `subagent_start` | A delegated child agent is created. |
| `subagent_stop` | A delegated child agent returns or fails. |

`subagent_start` fields include `parent_session_id`, `parent_turn_id`,
`parent_subagent_id`, `child_session_id`, `child_subagent_id`, `child_role`,
and `child_goal`.

`subagent_stop` fields include parent/child session IDs, role/status fields,
`child_summary`, `duration_ms`, and a metadata-only `tool_call_history`. Each
history entry contains the tool name, argument names, bounded side-effect
targets, input/output byte counts, and outcome. URL query strings and fragments
are removed; raw arguments, prompts, commands, contents, headers, and results
are intentionally excluded.

Observers can use these hooks to model nested trajectories while keeping child
agent execution linked to the parent turn that spawned it.

## Payload Safety

Observer payloads are designed for telemetry consumers, not raw object access.
New consumers should use the sanitized API payloads:

- `pre_api_request.request`
- `post_api_request.response`
- `api_request_error.request`
- `api_request_error.error`

Sanitization converts provider objects to JSON-compatible structures, bounds
large payloads, redacts sensitive keys, and avoids exposing raw response
objects in sanitized fields.

Legacy compatibility fields such as `request_messages`, `conversation_history`,
and `assistant_message` may still be present for existing plugins. New
observability consumers should prefer the sanitized payloads.

## Performance

The default uninstrumented path should stay cheap. Expensive request/response
payload construction is gated behind `has_hook(...)`, so Hermes only builds
sanitized API telemetry payloads when at least one plugin registered the
relevant hook.

Plugin authors should preserve this property:

- Register only hooks the plugin actually consumes.
- Avoid deep-copying or re-sanitizing already sanitized payloads.
- Keep hook callbacks fast and fail-open.
- Offload network export or batch writes when practical.

## Writing An Observer Plugin

Minimal observer plugin:

```python
def register(ctx):
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)


def on_pre_api_request(**kwargs):
    start_llm_span(
        request_id=kwargs.get("api_request_id"),
        turn_id=kwargs.get("turn_id"),
        request=kwargs.get("request"),
        model=kwargs.get("model"),
    )


def on_post_api_request(**kwargs):
    finish_llm_span(
        request_id=kwargs.get("api_request_id"),
        response=kwargs.get("response"),
        usage=kwargs.get("usage"),
        duration=kwargs.get("api_duration"),
    )


def on_pre_tool_call(**kwargs):
    start_tool_span(
        call_id=kwargs.get("tool_call_id"),
        name=kwargs.get("tool_name"),
        args=kwargs.get("args"),
    )


def on_post_tool_call(**kwargs):
    finish_tool_span(
        call_id=kwargs.get("tool_call_id"),
        result=kwargs.get("result"),
        status=kwargs.get("status"),
        duration_ms=kwargs.get("duration_ms"),
    )
```

Use `session_id`, `turn_id`, `api_request_id`, and `tool_call_id` for span
correlation. Use subagent and approval hooks when the export format supports
nested agent work or security lifecycle events.

## Existing Consumers

The bundled Langfuse plugin demonstrates direct hook-based observability for
turns, provider requests, and tool calls.

The native NeMo Relay SDK integration maps Hermes session, turn, LLM, and tool
lifecycles to Relay. Explicit Relay plugin configuration can add
[ATOF, ATIF, or OTEL](https://docs.nvidia.com/nemo/relay/configure-plugins/observability/about)
exporters and execution middleware; see
[Relay shared metrics](relay-shared-metrics.md).
