"""Bounded final-dispatch observation remains fail-open and detached."""

from __future__ import annotations

import json
import threading
import uuid
from types import SimpleNamespace

import pytest

from agent import chat_completion_helpers, request_observation
from hermes_cli import plugins, plugins_dispatch
from hermes_cli.plugins_manifest import PluginManifest


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "supported",
        "missing_context",
        "repeat_emission",
        "unsupported",
        "nonfinite",
        "cycle",
        "size",
        "depth",
        "nodes",
        "unicode",
        "hidden",
        "hidden_with_visible",
        "nested_hidden",
        "thinking_block",
        "redacted_block",
        "serialization",
        "exception",
        "timeout",
        "zero_timeout",
        "nonfinite_timeout",
        "worker_failure",
    ],
)
def test_dispatch_observer_isolation_and_failure(case, monkeypatch, caplog):
    manager = plugins.get_plugin_manager()
    ctx = plugins.PluginContext(PluginManifest(name="controlled-dispatch"), manager)
    agent = SimpleNamespace(
        api_mode="chat_completions", provider="openai", _current_api_request_id="stale"
    )
    request = {
        "model": "controlled",
        "messages": [{"role": "user", "content": "hello"}],
    }
    original_messages = request["messages"]
    observed, provider_calls, late, forbidden_calls = [], [], [], []
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class Opaque:
        def __repr__(self):
            raise AssertionError("opaque values must not be rendered")

    if case in {"absent", "unsupported"}:
        request["tools"] = Opaque()
    elif case == "nonfinite":
        request["temperature"] = float("nan")
    elif case == "cycle":
        request["tools"] = []
        request["tools"].append(request["tools"])
    elif case == "size":
        request["instructions"] = "a" * (request_observation._MAX_BYTES + 1)
    elif case == "depth":
        value = []
        for _ in range(request_observation._MAX_DEPTH + 1):
            value = [value]
        request["tools"] = value
    elif case == "nodes":
        request["tools"] = [None] * (request_observation._MAX_NODES + 1)
    elif case == "unicode":
        request["instructions"] = "\ud800"
    elif case == "hidden":
        request["input"] = [{"type": "reasoning", "encrypted_content": "private"}]
    elif case == "hidden_with_visible":
        request["input"] = [
            {"role": "user", "content": "visible evidence"},
            {"type": "reasoning", "encrypted_content": "private"},
            {"type": "function_call_output", "call_id": "call_1", "output": "source"},
        ]
    elif case == "nested_hidden":
        request["input"] = [
            {"role": "assistant", "content": [{"reasoning": "private"}]}
        ]
    elif case == "thinking_block":
        request["input"] = [{"type": "thinking", "text": "private"}]
    elif case == "redacted_block":
        request["input"] = [{"type": "redacted_thinking", "data": "opaque"}]
    request["extra_headers"] = {"Authorization": "never-observed"}
    request["extra_body"] = {"temperature": 0.2, "api_key": "never-observed"}

    def create(**kwargs):
        provider_calls.append(kwargs)
        assert kwargs["messages"] is original_messages
        assert set(kwargs) == set(request)
        return "provider-result"

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    def dispatch(identity):
        if case == "missing_context":
            return chat_completion_helpers._dispatch_nonstreaming_api_request(
                agent, request, make_client=lambda reason: client
            )
        with request_observation.request_observation_context(api_request_id=identity):
            return chat_completion_helpers._dispatch_nonstreaming_api_request(
                agent, request, make_client=lambda reason: client
            )

    def callback(**kwargs):
        window = kwargs["observation_window"]
        assert window.active
        if case == "exception":
            raise RuntimeError("never-observed")
        if case == "timeout":
            entered.set()
            try:
                assert release.wait(3)
                late.append((kwargs["api_request_id"], window.active))
            finally:
                finished.set()
            return {"complete": True}  # ignored even if the abandoned worker returns
        observed.append(kwargs)
        if kwargs["sdk_kwargs_json"] is not None:
            json.loads(kwargs["sdk_kwargs_json"]).clear()

    registrations = []
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.05)
    if case != "absent":
        registrations.append(ctx.register_hook("api_request_dispatch", callback))
    if case == "absent":

        def forbidden(*args, **kwargs):
            forbidden_calls.append(True)
            raise AssertionError("no observer must mean no capture work")

        monkeypatch.setattr(request_observation, "_snapshot", forbidden)
        monkeypatch.setattr(request_observation.uuid, "uuid4", forbidden)
        monkeypatch.setattr(plugins_dispatch.threading, "Thread", forbidden)
    elif case == "serialization":
        monkeypatch.setattr(
            request_observation.json,
            "dumps",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("never-observed")),
        )
    elif case in {"zero_timeout", "nonfinite_timeout"}:
        monkeypatch.setattr(
            plugins,
            "_resolve_hook_callback_timeout",
            lambda: 0 if case == "zero_timeout" else float("nan"),
        )
    elif case == "worker_failure":
        monkeypatch.setattr(
            plugins_dispatch.threading.Thread,
            "start",
            lambda self: (_ for _ in ()).throw(RuntimeError("no thread")),
        )
    try:
        assert dispatch("A") == "provider-result"
        assert request["messages"] == [{"role": "user", "content": "hello"}]
        if case == "repeat_emission":
            assert dispatch("A") == "provider-result"
            assert len(observed) == 2
            assert observed[0]["api_request_id"] == observed[1]["api_request_id"] == "A"
            assert observed[0]["sdk_kwargs_json"] == observed[1]["sdk_kwargs_json"]
            assert uuid.UUID(observed[0]["observation_id"]) != uuid.UUID(
                observed[1]["observation_id"]
            )
            assert all("observation_id" not in kwargs for kwargs in provider_calls)
        elif case == "timeout":
            assert entered.is_set()
            registrations.append(
                ctx.register_hook(
                    "api_request_dispatch", lambda **kw: observed.append(kw)
                )
            )
            assert dispatch("B") == "provider-result"
            assert len(observed) == 1 and observed[0]["api_request_id"] == "B"
            # The original callback is suppressed; its frozen A context survives
            # while the healthy callback observes B. Late completion is expired.
            release.set()
            assert finished.wait(1)
            assert late == [("A", False)]
        elif case in {"absent", "exception", "worker_failure"}:
            assert not observed
        else:
            assert len(observed) == 1
            value = observed[0]
            assert value["api_request_id"] == (
                None if case == "missing_context" else "A"
            )
            assert value["session_id"] is None
            assert not value["observation_window"].active
            if case in {"size", "depth", "nodes", "serialization"}:
                assert value["snapshot_status"] == "unavailable"
                assert value["sdk_kwargs_json"] is None
                assert value["reason_codes"] == (
                    ("serialization_failed",)
                    if case == "serialization"
                    else ("resource_limit",)
                )
            else:
                captured = json.loads(value["sdk_kwargs_json"])
                assert captured["messages"] == request["messages"]
                assert captured["extra_body"] == {"temperature": 0.2}
                assert "never-observed" not in value["sdk_kwargs_json"]
                if case == "hidden_with_visible":
                    assert captured["input"] == [request["input"][0], request["input"][2]]
                    assert ("input[1]", "hidden_reasoning") in value["omitted_fields"]
                    assert "private" not in value["sdk_kwargs_json"]
                    assert provider_calls[0]["input"] is request["input"]
                    assert provider_calls[0]["input"][1]["encrypted_content"] == "private"
                expected = {
                    "unsupported": ("tools", "non_json_value"),
                    "nonfinite": ("temperature", "nonfinite_number"),
                    "cycle": ("tools", "cyclic_value"),
                    "unicode": ("instructions", "invalid_unicode"),
                    "hidden": ("input", "hidden_reasoning"),
                    "nested_hidden": ("input", "hidden_reasoning"),
                    "thinking_block": ("input", "hidden_reasoning"),
                    "redacted_block": ("input", "hidden_reasoning"),
                }.get(case)
                if expected:
                    assert expected in value["omitted_fields"]
                    assert expected[0] not in captured
                assert ("extra_headers", "outside_projection") in value[
                    "omitted_fields"
                ]
                assert ("extra_body.api_key", "outside_projection") in value[
                    "omitted_fields"
                ]
        assert len(provider_calls) == (
            2 if case in {"timeout", "repeat_emission"} else 1
        )
        assert forbidden_calls == []
        assert "never-observed" not in caplog.text
        # The call scope resets even across exceptions; stale agent IDs are never used.
        assert request_observation._CONTEXT.get() is None
        with pytest.raises(RuntimeError, match="controlled failure"):
            with request_observation.request_observation_context(
                api_request_id="failed"
            ):
                raise RuntimeError("controlled failure")
        assert request_observation._CONTEXT.get() is None
    finally:
        release.set()
        for registration in registrations:
            registration.dispose()


@pytest.mark.parametrize("field", ["input", "messages", "extra_body.input"])
@pytest.mark.parametrize("hidden", [
    {"type": "reasoning", "encrypted_content": "private-reasoning"},
    {"role": "assistant", "content": [{"type": "thinking", "text": "private-reasoning"}]},
])
def test_hidden_conversation_item_preserves_visible_siblings(field, hidden):
    """Later turns keep visible context without retaining hidden reasoning."""
    visible = [
        {"role": "user", "content": "Inspect the ontology source"},
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "visible evidence"},
    ]
    items = [visible[0], hidden, *visible[1:]]
    original = json.dumps(items)
    request = {"model": "controlled", "extra_body": {"input": items}} if field.startswith("extra_body") else {"model": "controlled", field: items}
    encoded, omitted, status, reasons = request_observation._snapshot(request)
    snapshot = json.loads(encoded)
    projected = snapshot["extra_body"]["input"] if field.startswith("extra_body") else snapshot[field]
    assert projected == visible
    assert "private-reasoning" not in encoded
    assert (field + "[1]", "hidden_reasoning") in omitted
    assert status == "partial_projection" and reasons == ()
    assert json.dumps(items) == original
    assert items[1] is hidden
