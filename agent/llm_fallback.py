"""Availability fallback helpers for LLM provider routing."""

from __future__ import annotations

from collections import Counter
import json
import logging
import os
from typing import Any, Mapping, Optional

GROQ_KIMI_PROVIDER = "groq-kimi"
GROQ_KIMI_BASE_URL = "https://api.groq.com/openai/v1"
# Groq shut down Kimi K2 0905 on 2026-04-15 and recommends GPT-OSS 120B.
# Keep the requested Kimi ID available as an override for accounts where it
# returns, but default the automatic fallback to a model the live Groq API serves.
GROQ_KIMI_MODEL = "moonshotai/kimi-k2-instruct-0905"
GROQ_FALLBACK_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_FALLBACK_MODEL_ENV = "GROQ_FALLBACK_MODEL"
GROQ_API_KEY_ENV = "GROQ_API_KEY"
LLM_FALLBACK_FLAG = "LLM_FALLBACK_ENABLED"
LLM_PRIMARY_RETRIES_ENV = "LLM_PRIMARY_RETRIES"

_FALSEY = {"", "0", "false", "no", "off"}
_OPENAI_QUOTA_CODES = {
    "insufficient_quota",
    "rate_limit_exceeded",
    "billing_hard_limit_reached",
}
_BAD_INPUT_STATUSES = {400, 401, 404}
_FALLBACK_METRICS: Counter[tuple[str, str]] = Counter()
_GROQ_CHAT_MESSAGE_KEYS_BY_ROLE = {
    "system": {"role", "content", "name"},
    "developer": {"role", "content", "name"},
    "user": {"role", "content", "name"},
    "assistant": {"role", "content", "name", "tool_calls", "function_call"},
    "tool": {"role", "content", "tool_call_id"},
    "function": {"role", "content", "name"},
}


def llm_fallback_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    env = env or os.environ
    return str(env.get(LLM_FALLBACK_FLAG, "true")).strip().lower() not in _FALSEY


def primary_retry_limit(env: Optional[Mapping[str, str]] = None) -> int:
    env = env or os.environ
    raw = str(env.get(LLM_PRIMARY_RETRIES_ENV, "1")).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def groq_api_key(env: Optional[Mapping[str, str]] = None) -> str:
    env = env or os.environ
    return str(env.get(GROQ_API_KEY_ENV, "")).strip()


def groq_fallback_model(env: Optional[Mapping[str, str]] = None) -> str:
    env = env or os.environ
    configured = str(env.get(GROQ_FALLBACK_MODEL_ENV, "")).strip()
    return configured or GROQ_FALLBACK_DEFAULT_MODEL


def validate_groq_fallback_startup(env: Optional[Mapping[str, str]] = None) -> None:
    """Fail fast when the automatic Groq fallback is enabled but unconfigured."""
    env = env or os.environ
    if llm_fallback_enabled(env) and not groq_api_key(env):
        raise RuntimeError(
            f"{LLM_FALLBACK_FLAG}=true requires {GROQ_API_KEY_ENV} for the "
            "Groq fallback. Add GROQ_API_KEY to Doppler or set "
            f"{LLM_FALLBACK_FLAG}=false to disable LLM fallback."
        )


def is_openai_primary(provider: str = "", base_url: str = "", api_mode: str = "") -> bool:
    provider_l = (provider or "").strip().lower()
    base_l = (base_url or "").strip().lower()
    if provider_l in {"openai-codex", "codex", "openai"}:
        return True
    if "chatgpt.com/backend-api/codex" in base_l:
        return True
    if "api.openai.com" in base_l and "openrouter" not in base_l:
        return True
    return api_mode == "codex_responses" and provider_l == "openai-codex"


def is_groq_kimi_provider(provider: str = "", base_url: str = "") -> bool:
    provider_l = (provider or "").strip().lower()
    return provider_l in {GROQ_KIMI_PROVIDER, "kimi-groq"}


def groq_fallback_label(model: str = "") -> str:
    model = (model or groq_fallback_model()).strip()
    if "kimi-k2" in model.lower():
        return "groq/kimi-k2"
    if model == GROQ_FALLBACK_DEFAULT_MODEL:
        return "groq/gpt-oss-120b"
    return f"groq/{model.replace('/', '-')}"


def llm_provider_label(provider: str = "", base_url: str = "", model: str = "") -> str:
    if is_groq_kimi_provider(provider, base_url):
        return groq_fallback_label(model)
    return (provider or "unknown").strip().lower() or "unknown"


def automatic_groq_fallback_entry() -> dict[str, Any]:
    return {
        "provider": GROQ_KIMI_PROVIDER,
        "model": groq_fallback_model(),
        "automatic": True,
    }


def append_automatic_groq_fallback(
    fallback_chain: list[dict[str, Any]],
    *,
    primary_provider: str,
    primary_base_url: str,
    primary_api_mode: str,
    env: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Append the automatic Groq fallback for ChatGPT/OpenAI primaries."""
    env = env or os.environ
    chain = list(fallback_chain or [])
    if not llm_fallback_enabled(env):
        return chain
    if not is_openai_primary(primary_provider, primary_base_url, primary_api_mode):
        return chain
    if not groq_api_key(env):
        return chain
    if any(is_groq_kimi_provider(str(item.get("provider", "")), str(item.get("base_url", ""))) for item in chain):
        return chain
    chain.append(automatic_groq_fallback_entry())
    return chain


def _status_code(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _error_body(exc: BaseException) -> Any:
    body = getattr(exc, "body", None)
    if body:
        return body
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return response.json()
        except Exception:
            return None
    return None


def error_code(exc: BaseException) -> Optional[str]:
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value:
            return str(value)

    body = _error_body(exc)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value:
                    return str(value)
        for key in ("code", "type"):
            value = body.get(key)
            if value:
                return str(value)
    return None


def request_id(exc: BaseException) -> Optional[str]:
    for attr in ("request_id", "_request_id"):
        value = getattr(exc, attr, None)
        if value:
            return str(value)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers and hasattr(headers, "get"):
        return headers.get("x-request-id") or headers.get("request-id")
    return None


def _is_network_error(exc: BaseException) -> bool:
    try:
        from openai import APIConnectionError, APITimeoutError

        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
    except Exception:
        pass
    try:
        import httpx

        if isinstance(exc, (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
            httpx.TransportError,
        )):
            return True
    except Exception:
        pass
    err_type = type(exc).__name__.lower()
    if "timeout" in err_type or "connection" in err_type:
        return True
    message = str(exc).lower()
    return any(
        phrase in message
        for phrase in (
            "connection refused",
            "connection reset",
            "connection closed",
            "connection lost",
            "network connection",
            "network error",
            "name or service not known",
            "no route to host",
            "timed out",
            "timeout",
            "upstream connect error",
        )
    )


def bad_input_error(exc: BaseException) -> bool:
    return _status_code(exc) in _BAD_INPUT_STATUSES


def fallback_reason_for_error(exc: BaseException) -> Optional[str]:
    status = _status_code(exc)
    code = (error_code(exc) or "").lower()
    message = str(exc).lower()

    if code in _OPENAI_QUOTA_CODES:
        if code == "rate_limit_exceeded":
            return "rate_limit"
        return "quota"
    if status == 429:
        if "quota" in message or "billing" in message or "insufficient" in message:
            return "quota"
        return "rate_limit"
    if status in (503, 529):
        return "overloaded"
    if _is_network_error(exc):
        return "network"
    return None


def should_fallback_for_error(exc: BaseException) -> bool:
    if bad_input_error(exc):
        return False
    return fallback_reason_for_error(exc) is not None


def _sanitize_groq_chat_messages(messages: Any) -> Any:
    """Strip provider-specific history fields before sending to Groq."""
    if not isinstance(messages, list):
        return messages

    sanitized: list[Any] = []
    for msg in messages:
        if not isinstance(msg, Mapping):
            sanitized.append(msg)
            continue

        role = str(msg.get("role") or "")
        allowed = _GROQ_CHAT_MESSAGE_KEYS_BY_ROLE.get(
            role,
            {"role", "content", "name"},
        )
        cleaned = {
            key: value
            for key, value in msg.items()
            if key in allowed and value is not None
        }
        sanitized.append(cleaned)
    return sanitized


def translate_kimi_chat_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return OpenAI-compatible chat params trimmed for Groq fallback models."""
    allowed = {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "temperature",
        "max_tokens",
        "top_p",
        "stream",
        "seed",
        "stop",
        "timeout",
        "presence_penalty",
        "frequency_penalty",
        "parallel_tool_calls",
    }
    translated = {key: value for key, value in params.items() if key in allowed and value is not None}
    if "messages" in translated:
        translated["messages"] = _sanitize_groq_chat_messages(translated["messages"])

    if "max_tokens" not in translated and params.get("max_completion_tokens") is not None:
        translated["max_tokens"] = params["max_completion_tokens"]

    response_format = params.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") in {"json_object", "text"}:
        translated["response_format"] = response_format

    translated["model"] = groq_fallback_model()
    return translated


def annotate_response_provider(response: Any, provider: str) -> Any:
    if response is None:
        return response
    for attr in ("llm_provider", "x_llm_provider"):
        try:
            setattr(response, attr, provider)
        except Exception:
            pass
    metadata = {"x-llm-provider": provider}
    try:
        existing = getattr(response, "metadata", None)
        if isinstance(existing, dict):
            existing.update(metadata)
        else:
            setattr(response, "metadata", metadata)
    except Exception:
        pass
    return response


def emit_fallback_metric(reason: str, fallback: str = "") -> None:
    fallback = fallback or groq_fallback_label()
    _FALLBACK_METRICS[(reason, fallback)] += 1
    logging.getLogger(__name__).info(
        "metric llm.fallback.triggered reason=%s fallback=%s count=%s",
        reason,
        fallback,
        _FALLBACK_METRICS[(reason, fallback)],
    )


def fallback_metric_count(reason: str, fallback: str = "") -> int:
    fallback = fallback or groq_fallback_label()
    return _FALLBACK_METRICS[(reason, fallback)]


def record_groq_fallback(
    logger: logging.Logger,
    *,
    reason: str,
    error: Optional[BaseException] = None,
    model: str = "",
) -> None:
    payload = {
        "primary": "openai",
        "fallback": groq_fallback_label(model),
        "reason": reason,
        "error_code": error_code(error) if error else None,
        "request_id": request_id(error) if error else None,
    }
    logger.warning("llm.fallback.triggered %s", json.dumps(payload, sort_keys=True))
    emit_fallback_metric(reason, payload["fallback"])
