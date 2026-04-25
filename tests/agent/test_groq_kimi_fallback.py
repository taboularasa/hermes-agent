from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.llm_fallback import (
    GROQ_FALLBACK_DEFAULT_MODEL,
    GROQ_KIMI_BASE_URL,
    GROQ_KIMI_MODEL,
    append_automatic_groq_fallback,
    fallback_metric_count,
    groq_fallback_model,
    translate_kimi_chat_params,
    validate_groq_fallback_startup,
)
from run_agent import AIAgent


def _message_response(content: str = "ok"):
    return SimpleNamespace(
        id="chatcmpl-test",
        model=GROQ_FALLBACK_DEFAULT_MODEL,
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


def _stream_chunk(content=None, finish_reason=None, model=GROQ_FALLBACK_DEFAULT_MODEL):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                    reasoning=None,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _rate_limit_error(status=429, code="rate_limit_exceeded"):
    err = Exception("rate limit exceeded")
    err.status_code = status
    err.code = code
    err.request_id = "req-primary"
    return err


def _bad_request_error():
    err = Exception("Bad Request: invalid model")
    err.status_code = 400
    err.code = "invalid_request_error"
    return err


def _make_agent(monkeypatch, primary_client, groq_client, *, request_overrides=None):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    import agent.auxiliary_client as auxiliary_client
    import run_agent

    monkeypatch.setattr(run_agent, "OpenAI", lambda **_kwargs: primary_client)
    monkeypatch.setattr(auxiliary_client, "OpenAI", lambda **_kwargs: groq_client)
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        return AIAgent(
            api_key="sk-primary",
            base_url="https://api.openai.com/v1",
            provider="openai",
            api_mode="chat_completions",
            model="gpt-4o",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            request_overrides=request_overrides,
        )


def test_openai_429_falls_back_to_groq_with_translated_params(monkeypatch):
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = _rate_limit_error()
    groq_client = MagicMock()
    groq_client.api_key = "gsk-test"
    groq_client.base_url = GROQ_KIMI_BASE_URL
    groq_client.chat.completions.create.return_value = _message_response("fallback ok")

    agent = _make_agent(
        monkeypatch,
        primary_client,
        groq_client,
        request_overrides={
            "logprobs": True,
            "reasoning_effort": "high",
            "response_format": {"type": "json_schema", "json_schema": {"name": "x"}},
            "max_completion_tokens": 77,
            "seed": 42,
            "top_p": 0.9,
        },
    )

    before = fallback_metric_count("rate_limit")
    result = agent.run_conversation("hello")

    assert result["final_response"] == "fallback ok"
    assert result["llm_provider"] == "groq/gpt-oss-120b"
    assert fallback_metric_count("rate_limit") == before + 1
    groq_kwargs = groq_client.chat.completions.create.call_args.kwargs
    assert groq_kwargs["model"] == GROQ_FALLBACK_DEFAULT_MODEL
    assert groq_kwargs["messages"][0]["role"] == "system"
    assert groq_kwargs["seed"] == 42
    assert groq_kwargs["top_p"] == 0.9
    assert groq_kwargs["max_tokens"] == 77
    assert "logprobs" not in groq_kwargs
    assert "reasoning_effort" not in groq_kwargs
    assert "max_completion_tokens" not in groq_kwargs
    assert "response_format" not in groq_kwargs


def test_openai_400_does_not_fall_back(monkeypatch):
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = _bad_request_error()
    groq_client = MagicMock()
    groq_client.api_key = "gsk-test"
    groq_client.base_url = GROQ_KIMI_BASE_URL

    agent = _make_agent(monkeypatch, primary_client, groq_client)
    result = agent.run_conversation("hello")

    assert result["failed"] is True
    assert "invalid model" in result["error"].lower()
    groq_client.chat.completions.create.assert_not_called()


def test_explicit_fallback_chain_does_not_append_automatic_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    chain = [
        {"provider": "openai-codex", "model": "gpt-5.3-codex"},
        {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
    ]

    result = append_automatic_groq_fallback(
        chain,
        primary_provider="openai",
        primary_base_url="https://api.openai.com/v1",
        primary_api_mode="chat_completions",
    )

    assert result == chain
    assert all(item["provider"] != "groq-kimi" for item in result)


def test_stream_failure_before_first_token_falls_back(monkeypatch):
    monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")
    monkeypatch.setenv("LLM_PRIMARY_RETRIES", "0")
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = ConnectionError("socket closed")
    groq_client = MagicMock()
    groq_client.api_key = "gsk-test"
    groq_client.base_url = GROQ_KIMI_BASE_URL
    groq_client.chat.completions.create.return_value = iter([
        _stream_chunk(content="fallback"),
        _stream_chunk(finish_reason="stop"),
    ])

    agent = _make_agent(monkeypatch, primary_client, groq_client)
    deltas = []
    result = agent.run_conversation("hello", stream_callback=deltas.append)

    assert result["final_response"] == "fallback"
    assert deltas == ["fallback"]
    assert groq_client.chat.completions.create.called


def test_stream_failure_after_first_token_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")
    monkeypatch.setenv("LLM_PRIMARY_RETRIES", "0")

    def broken_stream():
        yield _stream_chunk(content="partial")
        raise ConnectionError("socket closed")

    primary_client = MagicMock()
    primary_client.chat.completions.create.return_value = broken_stream()
    groq_client = MagicMock()
    groq_client.api_key = "gsk-test"
    groq_client.base_url = GROQ_KIMI_BASE_URL

    agent = _make_agent(monkeypatch, primary_client, groq_client)
    deltas = []
    result = agent.run_conversation("hello", stream_callback=deltas.append)

    assert result["failed"] is True
    assert result["partial"] is True
    assert result["final_response"] is None
    assert result["partial_response"] == "partial"
    assert "socket closed" in result["error"]
    assert deltas == ["partial"]
    groq_client.chat.completions.create.assert_not_called()


def test_missing_groq_api_key_fails_startup_when_enabled(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        validate_groq_fallback_startup()


def test_explicit_fallback_chain_skips_groq_startup_requirement(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")

    validate_groq_fallback_startup(explicit_fallback_chain=True)


def test_kimi_param_translation_preserves_supported_fields():
    params = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "x"}}],
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 100,
        "top_p": 0.8,
        "stream": True,
        "seed": 1,
        "logprobs": True,
        "reasoning_effort": "medium",
        "response_format": {"type": "json_schema", "json_schema": {"name": "x"}},
    }

    translated = translate_kimi_chat_params(params)

    assert translated["model"] == GROQ_FALLBACK_DEFAULT_MODEL
    for key in ("messages", "tools", "tool_choice", "temperature", "max_tokens", "top_p", "stream", "seed"):
        assert translated[key] == params[key]
    assert "logprobs" not in translated
    assert "reasoning_effort" not in translated
    assert "response_format" not in translated


def test_kimi_param_translation_strips_provider_specific_message_fields():
    params = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}},
            {
                "role": "assistant",
                "content": "",
                "reasoning": "internal trace",
                "reasoning_content": "provider-only reasoning",
                "reasoning_details": [{"type": "text", "text": "detail"}],
                "finish_reason": "tool_calls",
                "codex_reasoning_items": [{"id": "rs_1"}],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok", "name": "tool-name"},
        ],
    }

    translated = translate_kimi_chat_params(params)

    assistant_msg = translated["messages"][2]
    assert assistant_msg == {
        "role": "assistant",
        "content": "",
        "tool_calls": params["messages"][2]["tool_calls"],
    }
    assert translated["messages"][1] == {"role": "user", "content": "hi"}
    assert translated["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    assert "reasoning_content" in params["messages"][2]


def test_groq_fallback_model_can_be_overridden_to_kimi(monkeypatch):
    monkeypatch.setenv("GROQ_FALLBACK_MODEL", GROQ_KIMI_MODEL)

    assert groq_fallback_model() == GROQ_KIMI_MODEL
    translated = translate_kimi_chat_params({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert translated["model"] == GROQ_KIMI_MODEL
