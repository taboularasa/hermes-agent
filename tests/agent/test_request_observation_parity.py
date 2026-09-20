"""HAD-2911: measure the existing pre-request hook against real final dispatch.

Request assembly, middleware, native Relay and late transport transformations
run normally. The SDK cases fake create; the HTTP cases run actual OpenAI
serialization into MockTransport. Neither establishes live provider receipt.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import nemo_relay
import httpx
import openai
import pytest

from agent import chat_completion_helpers, codex_runtime, relay_llm, relay_runtime
from agent import turn_api_call, turn_api_request
from hermes_cli import lifecycle, middleware, plugins, plugins_dispatch
from hermes_cli.plugins_manifest import PluginManifest
import run_agent
from tests.agent.test_run_agent import _mock_response


_PERMITTED_KEYS = (
    "model",
    "messages",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "temperature",
    "stream",
    "stream_options",
    "prompt_cache_retention",
    "extra_body",
)


def _freeze(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def _projection(request):
    return {key: request[key] for key in _PERMITTED_KEYS if key in request}


def _rewrite(request, label, temperature):
    rewritten = deepcopy(request)
    key = "input" if "input" in rewritten else "messages"
    user = next(item for item in reversed(rewritten[key]) if item.get("role") == "user")
    user["content"] = [
        {"type": "input_text" if key == "input" else "text", "text": label}
    ]
    if key == "input":
        rewritten["instructions"] += " / " + label
    else:
        system = next(item for item in rewritten[key] if item["role"] == "system")
        system["content"] += " / " + label
    tool = rewritten["tools"][0]
    function = tool.get("function", tool)
    function["description"] = label
    function["parameters"]["properties"]["value"]["description"] = label
    rewritten["temperature"] = temperature
    return rewritten


class _Stream:
    def __init__(self, events):
        self.events = events

    def __iter__(self):
        return iter(self.events)

    def close(self):
        pass


def _provider_result(route):
    if route == "chat_nonstream":
        return _mock_response(content="observed answer")
    if route == "codex":
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(type="output_text", text="observed answer")
                    ],
                )
            ],
            usage=None,
            status="completed",
            model="gpt-5-codex",
        )
        return _Stream([
            SimpleNamespace(
                type="response.output_item.done",
                item=response.output[0],
                output_index=0,
            ),
            SimpleNamespace(type="response.completed", response=response),
        ])
    delta = SimpleNamespace(
        content="observed answer",
        tool_calls=None,
        reasoning_content=None,
        reasoning=None,
    )
    return _Stream([
        SimpleNamespace(
            choices=[SimpleNamespace(index=0, delta=delta, finish_reason="stop")],
            model="gpt-4.1",
            usage=None,
        )
    ])


def _http_response(route):
    """Controlled response bytes, decoded by the actual SDK stream machinery."""
    if route == "codex":
        response = {
            "id": "resp_observation",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "gpt-5-codex",
            "output": [
                {
                    "type": "message",
                    "id": "msg_observation",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "observed answer",
                            "annotations": [],
                        }
                    ],
                }
            ],
        }
        events = [
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": response["output"][0],
            },
            {"type": "response.completed", "response": response},
        ]
    else:
        response = {
            "id": "chatcmpl_observation",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "observed answer"},
                    "finish_reason": "stop",
                }
            ],
        }
        if route == "chat_nonstream":
            return httpx.Response(200, json=response)
        events = [
            {
                "id": "chatcmpl_observation",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "observed answer"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_observation",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4.1",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
    content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        content=content + "data: [DONE]\n\n",
    )


def _runtime_profile(tmp_path):
    root = Path(__file__).resolve().parents[2]
    modules = (
        run_agent,
        turn_api_request,
        turn_api_call,
        chat_completion_helpers,
        codex_runtime,
        relay_llm,
        relay_runtime,
        lifecycle,
        middleware,
        plugins,
        plugins_dispatch,
    )
    for module in modules:
        assert Path(module.__file__).resolve().is_relative_to(root)
    assert Path(openai.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert Path(httpx.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert (
        Path(nemo_relay.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    )
    assert Path(os.environ["HERMES_HOME"]).resolve().is_relative_to(tmp_path.resolve())
    return {
        "python": sys.version,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "modules": {
            module.__name__: str(Path(module.__file__).resolve()) for module in modules
        },
        "openai": {
            "version": importlib.metadata.version("openai"),
            "path": openai.__file__,
        },
        "nemo_relay": {
            "version": importlib.metadata.version("nemo-relay"),
            "path": nemo_relay.__file__,
        },
        "pydantic": importlib.metadata.version("pydantic"),
        "httpx": {
            "version": importlib.metadata.version("httpx"),
            "path": httpx.__file__,
        },
        "codex_sdk_transform_override": os.environ.get("HERMES_CODEX_SDK_TRANSFORM"),
        "hermes_home": os.environ["HERMES_HOME"],
    }


@pytest.mark.parametrize("route", ["chat_nonstream", "chat_stream", "codex"])
@pytest.mark.parametrize("boundary", ["sdk", "http"])
@pytest.mark.parametrize(
    "rewritten", [False, True], ids=["baseline", "middleware_and_relay"]
)
def test_pre_request_observation_compares_with_final_sdk_dispatch(
    route,
    boundary,
    rewritten,
    monkeypatch,
    tmp_path,
    record_property,
):
    profile = _runtime_profile(tmp_path)
    tool = {
        "type": "function",
        "function": {
            "name": "observation_fixture",
            "description": "original tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "description": "original value"},
                },
            },
        },
    }
    monkeypatch.setattr(
        "model_tools.get_tool_definitions", lambda **kwargs: [deepcopy(tool)]
    )
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda: {})
    codex = route == "codex"
    agent = run_agent.AIAgent(
        model="gpt-5-codex" if codex else "gpt-4.1",
        provider="openai-codex" if codex else "openai",
        api_mode="codex_responses" if codex else "chat_completions",
        base_url="https://chatgpt.com/backend-api/codex"
        if codex
        else "https://openai.invalid/v1",
        api_key="controlled-fake-key",
        quiet_mode=True,
        max_iterations=2,
        skip_context_files=True,
        skip_memory=True,
        session_id="observation-fixture",
        save_trajectories=False,
    )
    agent._cached_system_prompt = "Controlled stable system prompt."
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent._disable_streaming = route == "chat_nonstream"
    initial_tools, initial_system = _freeze(agent.tools), agent._cached_system_prompt
    history = [
        {"role": "user", "content": "earlier controlled question"},
        {"role": "assistant", "content": "earlier controlled answer"},
    ]
    initial_history = _freeze(history)
    snapshots = {"request": [], "hook": [], "execution": [], "relay": [], "sdk": []}
    observations, narrow_observations, lifecycle_ids, wire, emission_ids = (
        [],
        [],
        [],
        [],
        [],
    )
    http_override = boundary == "http" and rewritten

    def provider_create(**kwargs):
        snapshots["sdk"].append(_freeze(_projection(kwargs)))
        return _provider_result(route)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=provider_create)),
        responses=SimpleNamespace(create=provider_create),
        base_url=agent.base_url,
        close=lambda: None,
    )
    if boundary == "http":

        def respond(request):
            assert request.method == "POST"
            assert request.url.host == ("chatgpt.com" if codex else "openai.invalid")
            assert request.url.path == (
                "/backend-api/codex/responses" if codex else "/v1/chat/completions"
            )
            raw = request.content
            wire.append({
                "body_utf8": raw.decode("utf-8"),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
            return _http_response(route)

        client = openai.OpenAI(
            api_key="controlled-fake-key",
            base_url=agent.base_url,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
    monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: client)

    def request_middleware(request, **kwargs):
        value = _rewrite(request, "request rewrite", 0.11) if rewritten else request
        snapshots["request"].append(_freeze(_projection(value)))
        return {"request": value}

    def pre_request(**kwargs):
        # Freeze inside the callback: retaining the shallow hook list can hide
        # later in-place changes and incorrectly make an early hook look exact.
        snapshots["hook"].append(
            _freeze({
                "body": _projection(kwargs["request"]["body"]),
                "messages": kwargs["request_messages"],
                "system": kwargs["system_prompt"],
            })
        )
        lifecycle_ids.append({
            key: kwargs.get(key)
            for key in (
                "session_id",
                "task_id",
                "turn_id",
                "api_request_id",
                "api_call_count",
            )
        })

    def narrow_observer(sdk_kwargs_json, observation_window, observation_id):
        assert observation_window.active
        emission_ids.append(observation_id)
        value = json.loads(sdk_kwargs_json)
        narrow_observations.append(deepcopy(value))
        # Editing a decoded value cannot mutate another callback or the SDK call.
        value.clear()

    async def observer(**kwargs):
        assert kwargs["observation_window"].active
        observations.append(kwargs)
        if boundary == "http":
            # The SDK is unmodified: this is the observer's pre-serialization
            # declaration, independently checked against real HTTP bytes below.
            snapshots["sdk"].append(
                _freeze(_projection(json.loads(kwargs["sdk_kwargs_json"])))
            )

    def execution_middleware(request, next_call, **kwargs):
        value = _rewrite(request, "execution rewrite", 0.22) if rewritten else request
        snapshots["execution"].append(_freeze(_projection(value)))
        return next_call(value)

    ctx = plugins.PluginContext(
        PluginManifest(name="controlled-observation"), plugins.get_plugin_manager()
    )
    registrations = [
        ctx.register_middleware("llm_request", request_middleware),
        ctx.register_hook("pre_api_request", pre_request),
        ctx.register_middleware("llm_execution", execution_middleware),
        ctx.register_hook("api_request_dispatch", narrow_observer),
        ctx.register_hook("api_request_dispatch", observer),
    ]
    host = relay_runtime.get_runtime()
    assert host is not None, "This comparison requires the actual native Relay runtime"
    host.retain_managed_execution("test.request_observation")
    relay = host.relay

    def relay_rewrite(name, request, annotated):
        value = deepcopy(request.content)
        if rewritten:
            value["temperature"] = 0.33
        if rewritten and codex:
            value["prompt_cache_retention"] = "24h"
            value["extra_body"] = {"prompt_cache_retention": "24h"}
        if http_override:
            value.setdefault("extra_body", {})["temperature"] = 0.73
            if codex:
                override = _rewrite(value, "extra-body override — café", 0.73)
                value["extra_body"].update(
                    input=override["input"],
                    tools=[
                        {"type": "function", **tool.get("function", tool)}
                        for tool in override["tools"]
                    ],
                )
        snapshots["relay"].append(_freeze(_projection(value)))
        if rewritten:
            codec = (
                relay.codecs.OpenAIResponsesCodec()
                if codex
                else relay.codecs.OpenAIChatCodec()
            )
            # Native codecs require edits through their annotated request; the
            # provider-specific body is rebuilt by the real Relay pipeline.
            annotated = codec.decode(relay.LLMRequest(request.headers, value))
        return relay.LLMRequestInterceptOutcome(request, annotated)

    relay.intercepts.register_llm_request(
        "controlled-request-observation", 1, False, relay_rewrite
    )
    try:
        result = agent.run_conversation(
            "controlled question",
            conversation_history=history,
            task_id="controlled-task",
        )
    finally:
        relay.intercepts.deregister_llm_request("controlled-request-observation")
        host.release_managed_execution("test.request_observation")
        for registration in registrations:
            registration.dispose()
        agent.close()
        client.close()
        relay_runtime._reset_for_tests()

    assert result["final_response"] == "observed answer"
    assert all(len(values) == 1 for values in snapshots.values()), snapshots
    request, hook, execution, relay_value, final = (
        json.loads(snapshots[key][0]) for key in snapshots
    )
    assert len(observations) == len(narrow_observations) == 1
    observed = observations[0]
    assert emission_ids == [observed["observation_id"]]
    assert observed["observation_id"]
    observed_kwargs = json.loads(observed["sdk_kwargs_json"])
    assert observed_kwargs == narrow_observations[0]
    assert _projection(observed_kwargs) == final
    assert observed["representation"] == "sdk_kwargs_projection"
    assert observed["snapshot_status"] != "unavailable"
    assert observed["reason_codes"] == ()
    assert not observed["observation_window"].active
    assert {key: observed[key] for key in lifecycle_ids[0]} == lifecycle_ids[0]
    assert observed["session_id"] == "observation-fixture"
    assert observed["task_id"] == "controlled-task"
    assert observed["api_request_id"] and observed["turn_id"]
    assert observed["retry_count"] == 0
    key = "input" if codex else "messages"
    assert hook["messages"] == request[key]
    assert hook["body"]["model"] == request["model"]
    assert (
        hook["body"]["tools"][0].get("function", hook["body"]["tools"][0])[
            "description"
        ]
        == (request["tools"][0].get("function", request["tools"][0])["description"])
    )
    final_payload = final.get("extra_body", {}) if codex else final
    if rewritten:
        assert request[key] != execution[key]
        assert request[key] != final_payload[key]
        assert request["tools"] != execution["tools"]
        if codex and http_override:
            assert execution["tools"] != final_payload["tools"]
            assert (
                final_payload["tools"][0]["description"] == "extra-body override — café"
            )
            assert final_payload["input"] != execution["input"]
        else:
            assert execution["tools"] == final_payload["tools"]
        assert request["temperature"] == 0.11
        assert execution["temperature"] == 0.22
        assert final["temperature"] == 0.33
        if codex:
            assert hook["system"] == request["instructions"]
            assert (
                request["instructions"]
                != execution["instructions"]
                == final["instructions"]
            )
        else:
            assert request[key][0]["content"] != execution[key][0]["content"]
    else:
        assert request[key] == execution[key] == final_payload[key]
        assert request["tools"] == final_payload["tools"]
    if codex:
        assert "input" not in final and "tools" not in final
        assert final["stream"] is True
        assert "prompt_cache_retention" not in final
        assert "prompt_cache_retention" not in final_payload
        if rewritten:
            assert relay_value["prompt_cache_retention"] == "24h"
            assert relay_value["extra_body"]["prompt_cache_retention"] == "24h"
    elif route == "chat_stream":
        assert final["stream"] is True
        assert final["stream_options"] == {"include_usage": True}
        assert "stream_options" not in hook["body"]

    if boundary == "http":
        assert len(wire) == 1
        body = json.loads(wire[0]["body_utf8"])
        # SDK extra_body overrides its transformed typed body. Compare the
        # permitted projection, without pretending snapshot JSON is wire bytes.
        expected_body = {
            key: value for key, value in observed_kwargs.items() if key != "extra_body"
        }
        expected_body.update(observed_kwargs.get("extra_body", {}))
        assert expected_body == {key: body[key] for key in expected_body}
        assert "extra_body" not in body and "extra_headers" not in body
        assert "timeout" not in body and "api_request_id" not in body
        assert body.get("stream", False) == (route != "chat_nonstream")
        if http_override:
            assert observed_kwargs["temperature"] == 0.33
            assert (
                observed_kwargs["extra_body"]["temperature"]
                == body["temperature"]
                == 0.73
            )
        if codex:
            assert "input" not in observed_kwargs and "tools" not in observed_kwargs
            assert body["input"] == observed_kwargs["extra_body"]["input"]
            assert body["tools"] == observed_kwargs["extra_body"]["tools"]
            assert "prompt_cache_retention" not in body
        record_property(
            "http_serialization",
            _freeze({
                "evidence_class": "controlled_real_sdk_mock_http_transport",
                "wire": wire[0],
                "permitted_body": expected_body,
                "observer_json_sha256": hashlib.sha256(
                    observed["sdk_kwargs_json"].encode("utf-8")
                ).hexdigest(),
                "extra_body_override": http_override,
                "provider_receipt_proven": False,
            }),
        )
    else:
        assert wire == []

    assert _freeze(agent.tools) == initial_tools
    assert agent._cached_system_prompt == initial_system
    assert _freeze(history) == initial_history
    roles = [
        message["role"] for message in result["messages"] if message["role"] != "system"
    ]
    assert all(left != right for left, right in zip(roles, roles[1:]))
    assert result["messages"][-2]["content"] == "controlled question"
    assert result["messages"][-1]["role"] == "assistant"
    assert all("rewrite" not in _freeze(message) for message in result["messages"])
    # This diagnostic records the measured relationship, not a parity claim.
    record_property(
        "request_observation_comparison",
        _freeze({
            "route": route,
            "boundary": boundary,
            "rewritten": rewritten,
            "profile": profile,
            "evidence_class": "controlled_final_sdk_kwargs_not_http_body"
            if boundary == "sdk"
            else "controlled_real_sdk_mock_http_transport",
            "prehook_equals_final_allowed_kwargs": hook["body"] == final,
            "exact_capture_supported": False,
            "snapshots": {
                key: json.loads(values[0]) for key, values in snapshots.items()
            },
        }),
    )
    record_property(
        "dispatch_observation",
        _freeze({
            key: value for key, value in observed.items() if key != "observation_window"
        }),
    )
