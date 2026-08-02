"""Security and duplicate-suppression contract for Hermes Linear ingress."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.hermes_linear_ingress import (
    HERMES_LINEAR_MAX_BODY_BYTES,
    HERMES_LINEAR_REQUEST_CONTRACT,
    HERMES_LINEAR_SIGNED_INGRESS_PATH,
    HERMES_LINEAR_SIGNED_ROUTE_NAME,
    HermesLinearDeliveryLedger,
    verify_hermes_linear_request,
)
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH


SECRET = "test-only-hermes-linear-secret"
NOW = 1_786_000_000


def _envelope(delivery_key: str = "linear:comment:comment-1") -> dict:
    return {
        "schemaVersion": "hermes.linear-agent-session.v1",
        "deliveryKey": delivery_key,
        "action": "prompted",
        "organizationId": "org-1",
        "destination": {"kind": "hermes", "appUserId": "app-user-hermes"},
        "issue": {
            "id": "issue-1",
            "identifier": "HAD-271",
            "title": "Sanitized bridge replacement fixture",
            "url": "https://linear.app/hadto/issue/HAD-271",
            "executionContext": {"externalKey": "linear:HAD-271"},
        },
        "agentSession": {
            "id": "session-1",
            "status": "prompted",
            "url": "https://linear.app/hadto/issue/HAD-271",
            "appUserId": "app-user-hermes",
        },
        "prompt": {
            "id": "prompt-1",
            "commentId": "comment-1",
            "sourceCommentId": "comment-1",
            "body": "Sanitized human-authored follow-up",
        },
        "source": {
            "eventTimestamp": "2026-08-02T12:00:00Z",
            "traceId": "trace-1",
        },
    }


def _body(envelope: dict | None = None) -> bytes:
    return json.dumps(envelope or _envelope(), separators=(",", ":")).encode()


def _headers(body: bytes, *, timestamp: int = NOW, secret: str = SECRET) -> dict:
    delivery_key = json.loads(body)["deliveryKey"]
    signed = str(timestamp).encode() + b"." + body
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {
        "X-Hermes-Timestamp": str(timestamp),
        "X-Hermes-Delivery-Key": delivery_key,
        "X-Hermes-Signature-256": f"sha256={signature}",
    }


def _verify(body: bytes, headers: dict | None = None, **kwargs):
    return verify_hermes_linear_request(
        raw_body=body,
        headers=headers or _headers(body),
        secret=kwargs.get("secret", SECRET),
        now=kwargs.get("now", NOW),
    )


def test_accepts_fresh_signed_allowlisted_envelope():
    result = _verify(_body())

    assert result.ok
    assert result.delivery_key == "linear:comment:comment-1"
    assert result.body_sha256 == hashlib.sha256(_body()).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "expected_reason", "expected_status"),
    [
        (
            lambda headers: headers.pop("X-Hermes-Signature-256"),
            "missing_or_invalid_signature",
            401,
        ),
        (
            lambda headers: headers.update({
                "X-Hermes-Signature-256": "sha256=" + "0" * 64
            }),
            "invalid_signature",
            401,
        ),
        (
            lambda headers: headers.update({"X-Hermes-Delivery-Key": "other-key"}),
            "delivery_key_mismatch",
            409,
        ),
    ],
)
def test_rejects_unsigned_invalid_or_key_mismatched_requests(
    mutation, expected_reason, expected_status
):
    body = _body()
    headers = _headers(body)
    mutation(headers)

    result = _verify(body, headers)

    assert not result.ok
    assert result.reason == expected_reason
    assert result.status == expected_status


def test_rejects_stale_request():
    body = _body()

    result = _verify(body, _headers(body, timestamp=NOW - 301))

    assert not result.ok
    assert result.reason == "stale_timestamp"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not-json", "invalid_json"),
        (
            b'{"schemaVersion":"hermes.linear-agent-session.v1",'
            b'"schemaVersion":"hermes.linear-agent-session.v1"}',
            "invalid_json",
        ),
    ],
)
def test_rejects_malformed_or_duplicate_json(body, reason):
    headers = _headers(_body())
    signed = str(NOW).encode() + b"." + body
    headers["X-Hermes-Signature-256"] = (
        "sha256=" + hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    )

    result = _verify(body, headers)

    assert not result.ok
    assert result.reason == reason


def test_rejects_unknown_fields_and_oversized_prompt():
    with_extra = _envelope()
    with_extra["rawLinearPayload"] = {"secret": "must-not-be-accepted"}
    result = _verify(_body(with_extra))
    assert result.reason == "invalid_envelope_fields"

    oversized = _envelope()
    oversized["prompt"]["body"] = "x" * 16_385
    result = _verify(_body(oversized))
    assert result.reason == "prompt_too_large"


def test_rejects_oversized_body_before_parsing():
    body = b"x" * (HERMES_LINEAR_MAX_BODY_BYTES + 1)

    result = verify_hermes_linear_request(
        raw_body=body, headers={}, secret=SECRET, now=NOW
    )

    assert result.reason == "payload_too_large"
    assert result.status == 413


def test_ledger_accepts_then_suppresses_duplicate_and_detects_conflict(tmp_path):
    ledger = HermesLinearDeliveryLedger(tmp_path / "deliveries.db")

    assert ledger.claim("delivery-1", "hash-1", NOW) == "accepted"
    assert ledger.claim("delivery-1", "hash-1", NOW + 1) == "duplicate"
    assert ledger.claim("delivery-1", "hash-2", NOW + 2) == "conflict"


def test_ledger_allows_only_one_concurrent_claim(tmp_path):
    ledger = HermesLinearDeliveryLedger(tmp_path / "deliveries.db")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: ledger.claim("delivery-1", "hash-1", NOW),
                range(16),
            )
        )

    assert results.count("accepted") == 1
    assert results.count("duplicate") == 15


def _adapter(tmp_path, *, secret: str = SECRET) -> WebhookAdapter:
    return WebhookAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "host": "127.0.0.1",
                "port": 0,
                "routes": {
                    "linear-agent-session": {
                        "secret": _INSECURE_NO_AUTH,
                        "prompt": "{__raw__}",
                        "deliver": "log",
                    },
                    HERMES_LINEAR_SIGNED_ROUTE_NAME: {
                        "secret": secret,
                        "request_contract": HERMES_LINEAR_REQUEST_CONTRACT,
                        "events": ["linear_agent_session"],
                        "prompt": "{__raw__}",
                        "deliver": "log",
                        "delivery_ledger_path": str(tmp_path / "deliveries.db"),
                    },
                },
            },
        )
    )


@pytest.mark.asyncio
async def test_adapter_accepts_once_and_suppresses_replay(tmp_path):
    adapter = _adapter(tmp_path)
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    body = _body()
    timestamp = int(time.time())

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            HERMES_LINEAR_SIGNED_INGRESS_PATH,
            data=body,
            headers=_headers(body, timestamp=timestamp),
        )
        assert response.status == 202
        accepted = await response.json()
        assert accepted["event"] == "linear_agent_session"

        replay = await client.post(
            HERMES_LINEAR_SIGNED_INGRESS_PATH,
            data=body,
            headers=_headers(body, timestamp=timestamp),
        )
        assert replay.status == 200
        assert (await replay.json())["status"] == "duplicate"

    await asyncio.sleep(0)
    handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_loopback_and_signed_ingress_coexist_without_route_drift(tmp_path):
    adapter = _adapter(tmp_path)
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    legacy_body = json.dumps(
        {
            "type": "AgentSessionEvent",
            "agentSession": {"id": "legacy-session-1"},
            "prompt": {"body": "Sanitized legacy loopback fixture"},
        }
    ).encode()
    signed_body = _body()
    timestamp = int(time.time())

    async with TestClient(TestServer(app)) as client:
        legacy = await client.post(
            "/webhooks/linear-agent-session",
            data=legacy_body,
        )
        assert legacy.status == 202
        assert (await legacy.json())["route"] == "linear-agent-session"

        unsigned_new = await client.post(
            HERMES_LINEAR_SIGNED_INGRESS_PATH,
            data=signed_body,
        )
        assert unsigned_new.status == 401

        signed_new = await client.post(
            HERMES_LINEAR_SIGNED_INGRESS_PATH,
            data=signed_body,
            headers=_headers(signed_body, timestamp=timestamp),
        )
        assert signed_new.status == 202
        assert (await signed_new.json())["route"] == HERMES_LINEAR_SIGNED_ROUTE_NAME

    await asyncio.sleep(0)
    assert handle_message.await_count == 2
    events = [call.args[0] for call in handle_message.await_args_list]
    assert events[0].source.chat_id.startswith("webhook:linear-agent-session:")
    assert events[0].raw_message == json.loads(legacy_body)
    assert events[1].source.chat_id.startswith(
        f"webhook:{HERMES_LINEAR_SIGNED_ROUTE_NAME}:"
    )
    assert events[1].raw_message == _envelope()


@pytest.mark.asyncio
async def test_signed_contract_cannot_replace_the_legacy_route(tmp_path):
    adapter = WebhookAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "host": "127.0.0.1",
                "port": 0,
                "routes": {
                    "linear-agent-session": {
                        "secret": SECRET,
                        "request_contract": HERMES_LINEAR_REQUEST_CONTRACT,
                        "prompt": "{__raw__}",
                    }
                },
            },
        )
    )

    with pytest.raises(ValueError, match="restricted to the static"):
        await adapter.connect()


@pytest.mark.asyncio
async def test_adapter_rejects_insecure_contract_configuration(tmp_path):
    adapter = _adapter(tmp_path, secret=_INSECURE_NO_AUTH)

    with pytest.raises(ValueError, match="cannot use INSECURE_NO_AUTH"):
        await adapter.connect()


@pytest.mark.asyncio
async def test_adapter_rejects_stale_request_without_scheduling(tmp_path):
    adapter = _adapter(tmp_path)
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    body = _body()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                HERMES_LINEAR_SIGNED_INGRESS_PATH,
                data=body,
                headers=_headers(body, timestamp=NOW - 301),
            )
            assert response.status == 401

    handle_message.assert_not_awaited()
