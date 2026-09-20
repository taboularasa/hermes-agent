"""Opt-in, detached observation immediately before supported SDK dispatch.

This is a bounded JSON projection of SDK kwargs, not serialized HTTP bytes or
proof of capture completion. No collector, persistence or admission policy lives
here. The early telemetry hooks keep their existing redaction and size limits.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import math

logger = logging.getLogger(__name__)

# Body fields only. In particular, headers, credentials, query parameters,
# clients and SDK timeout/options never enter the projection. Unknown extensions
# stay explicitly omitted until their representation is supported.
_BODY_FIELDS = frozenset({
    "model",
    "messages",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "stream",
    "stream_options",
    "parallel_tool_calls",
    "response_format",
    "text",
    "reasoning",
    "stop",
    "seed",
    "n",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "prompt_cache_key",
    "prompt_cache_retention",
    "store",
    "truncation",
    "service_tier",
    "modalities",
    "audio",
})
_HIDDEN_FIELDS = frozenset({
    "encrypted_content",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "thinking",
    "redacted_thinking",
})
_HIDDEN_TYPES = frozenset({"reasoning", "thinking", "redacted_thinking"})
_MAX_BYTES = 1_048_576
_MAX_NODES = 50_000
_MAX_DEPTH = 24


@dataclass(frozen=True)
class RequestObservationContext:
    """Existing lifecycle identities; missing values are never synthesized."""

    session_id: str | None = None
    task_id: str | None = None
    turn_id: str | None = None
    api_request_id: str | None = None
    retry_count: int | None = None
    api_call_count: int | None = None


_CONTEXT: ContextVar[RequestObservationContext | None] = ContextVar(
    "request_observation_context", default=None
)


@contextmanager
def request_observation_context(**values):
    """Scope known IDs to the call; provider workers already copy ContextVars."""
    normalized = {
        key: value if type(value) is str and value else None
        for key, value in values.items()
        if key in {"session_id", "task_id", "turn_id", "api_request_id"}
    }
    for key in ("retry_count", "api_call_count"):
        value = values.get(key)
        normalized[key] = value if type(value) is int and value >= 0 else None
    token = _CONTEXT.set(RequestObservationContext(**normalized))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


class _Unsupported(ValueError):
    pass


class _BudgetExceeded(_Unsupported):
    pass


class _JSONProjection:
    def __init__(self):
        self.nodes = 0
        self.bytes = 0
        self.ancestors = set()

    def copy(self, value, depth=0):
        self.nodes += 1
        self.bytes += 2  # container separators / scalar framing
        if self.nodes > _MAX_NODES or depth > _MAX_DEPTH or self.bytes > _MAX_BYTES:
            raise _BudgetExceeded("resource_limit")
        kind = type(value)
        if value is None or kind is bool:
            return value
        if kind is str:
            if len(value) > _MAX_BYTES:
                raise _BudgetExceeded("resource_limit")
            try:
                self.bytes += len(value.encode("utf-8"))
            except UnicodeError:
                raise _Unsupported("invalid_unicode") from None
            if self.bytes > _MAX_BYTES:
                raise _BudgetExceeded("resource_limit")
            return value
        if kind is int:
            if value.bit_length() > 4096:
                raise _Unsupported("unsupported_number")
            self.bytes += len(str(value))
            return value
        if kind is float:
            if not math.isfinite(value):
                raise _Unsupported("nonfinite_number")
            return value
        if kind not in (dict, list):
            raise _Unsupported("non_json_value")
        if len(value) > _MAX_NODES - self.nodes:
            raise _BudgetExceeded("resource_limit")
        if id(value) in self.ancestors:
            raise _Unsupported("cyclic_value")
        self.ancestors.add(id(value))
        try:
            if kind is list:
                return [self.copy(item, depth + 1) for item in value]
            if any(type(key) is not str for key in value):
                raise _Unsupported("non_string_key")
            item_type = value.get("type")
            if _HIDDEN_FIELDS.intersection(value) or (
                type(item_type) is str and item_type in _HIDDEN_TYPES
            ):
                raise _Unsupported("hidden_reasoning")
            return {
                self.copy(key, depth + 1): self.copy(item, depth + 1)
                for key, item in value.items()
            }
        finally:
            self.ancestors.remove(id(value))


def _snapshot(kwargs):
    """Omit whole unsupported fields; never substitute repr or truncate values."""
    omitted = []
    budget = _JSONProjection()

    def body(value, prefix=""):
        if type(value) is not dict:
            raise _Unsupported("non_json_body")
        if len(value) > _MAX_NODES:
            raise _BudgetExceeded("resource_limit")
        result = {}
        for key, item in value.items():
            if type(key) is not str or len(key) > 128:
                raise _Unsupported("unsupported_field_name")
            path = prefix + key
            if not prefix and key == "extra_body":
                try:
                    result[key] = body(item, "extra_body.")
                except _BudgetExceeded:
                    raise
                except _Unsupported as exc:
                    omitted.append((path, str(exc)))
            elif key not in _BODY_FIELDS:
                omitted.append((path, "outside_projection"))
            else:
                try:
                    result[key] = budget.copy(item)
                except _BudgetExceeded:
                    raise
                except _Unsupported as exc:
                    omitted.append((path, str(exc)))
        return result

    try:
        projection = body(kwargs)
        encoded = json.dumps(
            projection, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise _BudgetExceeded("resource_limit")
    except (_Unsupported, UnicodeError, ValueError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, _Unsupported) else "serialization_failed"
        return None, (), "unavailable", (reason,)
    return (
        encoded,
        tuple(sorted(omitted)),
        "partial_projection" if omitted else "complete_projection",
        (),
    )


def observe_api_dispatch(agent, sdk_kwargs, *, route: str) -> None:
    """Observe supported SDK kwargs; failures never block the provider call.

    No subscription means no traversal, JSON encoding or observer worker. Plugin
    discovery itself retains its existing behavior. A callback's window is only
    an expiry signal; a collector must synchronize persistence against its own
    terminal/tombstone state. Neither this hook nor callback results prove a
    complete request, successful provider receipt or serialized HTTP identity.
    """
    try:
        from hermes_cli import plugins

        if not plugins.has_hook("api_request_dispatch"):
            return
        encoded, omitted, status, reasons = _snapshot(sdk_kwargs)
        context = _CONTEXT.get() or RequestObservationContext()
        plugins.invoke_hook(
            "api_request_dispatch",
            observation_schema_version="hermes.sdk_request_observation.v1",
            representation="sdk_kwargs_projection",
            route=route,
            provider=agent.provider if type(agent.provider) is str else None,
            api_mode=agent.api_mode if type(agent.api_mode) is str else None,
            session_id=context.session_id,
            task_id=context.task_id,
            turn_id=context.turn_id,
            api_request_id=context.api_request_id,
            retry_count=context.retry_count,
            api_call_count=context.api_call_count,
            sdk_kwargs_json=encoded,
            omitted_fields=omitted,
            snapshot_status=status,
            reason_codes=reasons,
        )
    except Exception as exc:
        # Never include request values or plugin exception text in telemetry.
        logger.warning("SDK request observation unavailable (%s)", type(exc).__name__)
