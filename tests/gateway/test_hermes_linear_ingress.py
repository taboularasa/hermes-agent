"""Security and duplicate-suppression contract for Hermes Linear ingress."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path
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
    HermesLinearDeliveryInbox,
    hermes_linear_execution_id,
    hermes_linear_session_chat_id,
    verify_hermes_linear_request,
)
from gateway.platforms.base import ProcessingOutcome
from gateway.session import build_session_key
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH


SECRET = "test-only-hermes-linear-secret"
NOW = 1_786_000_000
CONTRACT_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "contracts"


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


def test_cross_repository_canonical_fixture_and_hmac_vector():
    body = (
        (CONTRACT_FIXTURE_DIR / "hermes.linear-agent-session.v1.json")
        .read_text()
        .rstrip()
        .encode()
    )
    contract = json.loads(
        (
            CONTRACT_FIXTURE_DIR / "hermes.linear-agent-session.v1.contract.json"
        ).read_text()
    )

    assert len(body) == contract["canonicalBodyBytes"]
    assert hashlib.sha256(body).hexdigest() == contract["canonicalBodySha256"]
    signed = contract["hmacTestTimestamp"].encode() + b"." + body
    signature = hmac.new(
        contract["hmacTestSecret"].encode(), signed, hashlib.sha256
    ).hexdigest()
    assert signature == contract["hmacSha256"]
    headers = {
        "X-Hermes-Timestamp": contract["hmacTestTimestamp"],
        "X-Hermes-Delivery-Key": json.loads(body)["deliveryKey"],
        "X-Hermes-Signature-256": f"sha256={signature}",
    }
    result = verify_hermes_linear_request(
        raw_body=body,
        headers=headers,
        secret=contract["hmacTestSecret"],
        now=float(contract["hmacTestTimestamp"]),
    )
    assert result.ok

    mutated = body.replace(b"authorized", b"unauthorized", 1)
    rejected = verify_hermes_linear_request(
        raw_body=mutated,
        headers=headers,
        secret=contract["hmacTestSecret"],
        now=float(contract["hmacTestTimestamp"]),
    )
    assert not rejected.ok
    assert rejected.reason == "invalid_signature"


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


def test_inbox_upgrades_claim_only_ledger_without_stranding_old_rows(tmp_path):
    path = tmp_path / "deliveries.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE hermes_linear_deliveries (
                delivery_key TEXT PRIMARY KEY,
                body_sha256 TEXT NOT NULL,
                received_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO hermes_linear_deliveries VALUES ('old-key', 'old-hash', ?)",
            (NOW,),
        )

    inbox = HermesLinearDeliveryInbox(path)
    old = inbox.get("old-key")

    assert old is not None
    assert old.state == "failed"
    assert old.reason_code == "legacy_claim_missing_body"
    assert inbox.state_counts() == {"failed": 1}


def test_inbox_accepts_exact_body_and_records_conflict_without_overwrite(tmp_path):
    inbox = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    body = _body()
    digest = hashlib.sha256(body).hexdigest()

    assert (
        inbox.accept(
            delivery_key="delivery-1",
            body_sha256=digest,
            raw_body=body,
            received_at=NOW,
            route_name="linear-agent-session",
            profile=None,
        )
        == "accepted"
    )
    assert (
        inbox.accept(
            delivery_key="delivery-1",
            body_sha256=digest,
            raw_body=body,
            received_at=NOW + 1,
            route_name="linear-agent-session",
            profile=None,
        )
        == "duplicate"
    )
    assert (
        inbox.accept(
            delivery_key="delivery-1",
            body_sha256="0" * 64,
            raw_body=b'{"changed":true}',
            received_at=NOW + 2,
            route_name="linear-agent-session",
            profile=None,
        )
        == "conflict"
    )

    stored = inbox.get("delivery-1")
    assert stored is not None
    assert stored.raw_body == body
    assert stored.body_sha256 == digest
    assert stored.reason_code == "delivery_key_body_conflict"
    assert inbox.state_counts() == {"received": 1, "conflict": 1}


def test_inbox_allows_only_one_concurrent_accept(tmp_path):
    inbox = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    body = _body()
    digest = hashlib.sha256(body).hexdigest()

    def accept(index: int) -> str:
        return inbox.accept(
            delivery_key="delivery-1",
            body_sha256=digest,
            raw_body=body,
            received_at=NOW + index,
            route_name="linear-agent-session",
            profile=None,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(accept, range(16)))

    assert results.count("accepted") == 1
    assert results.count("duplicate") == 15


def test_inbox_competing_workers_and_expired_lease_recovery(tmp_path):
    inbox = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    body = _body()
    digest = hashlib.sha256(body).hexdigest()
    inbox.accept(
        delivery_key="delivery-1",
        body_sha256=digest,
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda index: inbox.lease_next(
                    worker_id=f"worker-{index}", now=NOW, lease_seconds=10
                ),
                range(8),
            )
        )
    leased = [row for row in results if row is not None]
    assert len(leased) == 1

    restarted = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    recovered = restarted.lease_next(
        worker_id="worker-restarted", now=NOW + 11, lease_seconds=10
    )
    assert recovered is not None
    assert recovered.lease_owner == "worker-restarted"
    assert recovered.attempt_count == 2


def test_inbox_lifecycle_and_activity_rows_are_idempotent(tmp_path):
    inbox = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    body = _body()
    inbox.accept(
        delivery_key="delivery-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="worker-1", now=NOW, lease_seconds=60)
    assert row is not None
    assert inbox.mark_scheduled("delivery-1", "worker-1", NOW)
    assert inbox.mark_started(
        delivery_key="delivery-1",
        worker_id="worker-1",
        now=NOW,
        activity_body=b'{"activity":"start"}',
    )
    assert inbox.mark_started(
        delivery_key="delivery-1",
        worker_id="worker-1",
        now=NOW + 1,
        activity_body=b'{"activity":"different-must-not-overwrite"}',
    )
    assert inbox.activity_states("delivery-1") == {"action": "pending"}
    assert inbox.activity_state_counts() == {"action:pending": 1}
    assert inbox.finish(
        delivery_key="delivery-1",
        outcome="completed",
        now=NOW + 2,
        reason_code="execution_completed",
    )
    assert inbox.get("delivery-1").state == "completed"  # type: ignore[union-attr]


def test_delivery_key_derives_stable_execution_and_session_identity():
    first = hermes_linear_execution_id("delivery-1")
    second = hermes_linear_execution_id("delivery-1")
    assert first == second
    assert first != hermes_linear_execution_id("delivery-2")
    assert hermes_linear_session_chat_id("linear-agent-session", first) == (
        f"webhook:linear-agent-session:hermes-linear-{first[:32]}"
    )


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
    handle_message.assert_not_awaited()
    inbox = adapter._hermes_linear_delivery_inboxes["linear-agent-session"]
    stored = inbox.get("linear:comment:comment-1")
    assert stored is not None
    assert stored.state == "received"
    assert stored.raw_body == body


@pytest.mark.asyncio
async def test_adapter_returns_retryable_error_when_inbox_commit_fails(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    body = _body()
    timestamp = int(time.time())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            inbox,
            "accept",
            lambda **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("simulated commit failure with private body")
            ),
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/webhooks/linear-agent-session",
                data=body,
                headers=_headers(body, timestamp=timestamp),
            )
            response_text = await response.text()

    assert response.status == 503
    assert inbox.get("linear:comment:comment-1") is None
    assert "private body" not in response_text


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


@pytest.mark.asyncio
async def test_worker_schedules_committed_inbox_once_and_completes(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="worker-1", now=NOW, lease_seconds=60)
    assert row is not None

    async def fake_handle(event):
        await adapter.on_processing_start(event)
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=True,
            thread_sessions_per_user=False,
        )
        task = asyncio.create_task(
            adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        )
        adapter._session_tasks[session_key] = task

    adapter.handle_message = fake_handle  # type: ignore[invalid-assignment]
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        await adapter._dispatch_hermes_linear_inbox_row(inbox, row)

    stored = inbox.get("linear:comment:comment-1")
    assert stored is not None
    assert stored.state == "completed"
    assert stored.reason_code == "execution_completed"
    assert inbox.activity_states(stored.delivery_key) == {"action": "pending"}


@pytest.mark.asyncio
async def test_worker_records_scheduling_rejection_without_replay(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="worker-1", now=NOW, lease_seconds=60)
    assert row is not None
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        await adapter._dispatch_hermes_linear_inbox_row(inbox, row)

    stored = inbox.get(row.delivery_key)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.reason_code == "scheduling_rejected"
    assert inbox.activity_states(row.delivery_key) == {
        "action": "pending",
        "error": "pending",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failpoint", "expected_state"),
    [
        ("after_worker_lease", "scheduled"),
        ("before_start_transition", "scheduled"),
        ("after_start_transition", "started"),
        ("before_agent_schedule", "started"),
    ],
)
async def test_worker_crash_boundaries_remain_recoverable(
    tmp_path, failpoint, expected_state
):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="crashed", now=NOW, lease_seconds=10)
    assert row is not None
    adapter._hermes_linear_failpoint = lambda name, _key: (
        (_ for _ in ()).throw(RuntimeError("simulated crash"))
        if name == failpoint
        else None
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await adapter._dispatch_hermes_linear_inbox_row(inbox, row)

    stored = inbox.get(row.delivery_key)
    assert stored is not None
    assert stored.state == expected_state
    recovered = inbox.lease_next(worker_id="restarted", now=NOW + 11, lease_seconds=10)
    assert recovered is not None
    assert recovered.delivery_key == row.delivery_key


@pytest.mark.asyncio
async def test_crash_after_agent_schedule_never_blindly_starts_a_second_run(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="crashed", now=NOW, lease_seconds=10)
    assert row is not None
    scheduled_task: asyncio.Task | None = None

    async def fake_handle(event):
        nonlocal scheduled_task
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=True,
            thread_sessions_per_user=False,
        )
        scheduled_task = asyncio.create_task(asyncio.sleep(60))
        adapter._session_tasks[session_key] = scheduled_task

    adapter.handle_message = fake_handle  # type: ignore[invalid-assignment]
    adapter._hermes_linear_failpoint = lambda name, _key: (
        (_ for _ in ()).throw(RuntimeError("simulated process loss"))
        if name == "after_agent_schedule"
        else None
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        with pytest.raises(RuntimeError, match="simulated process loss"):
            await adapter._dispatch_hermes_linear_inbox_row(inbox, row)

    assert inbox.get(row.delivery_key).state == "started"  # type: ignore[union-attr]
    assert scheduled_task is not None
    scheduled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scheduled_task

    restarted = HermesLinearDeliveryInbox(tmp_path / "deliveries.db")
    recovered = restarted.lease_next(
        worker_id="restarted", now=NOW + 11, lease_seconds=10
    )
    assert recovered is not None
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]
    adapter._hermes_linear_failpoint = None
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW + 11)
        monkeypatch.setattr(
            adapter,
            "_hermes_linear_session_has_persisted_turn",
            AsyncMock(return_value=True),
        )
        await adapter._dispatch_hermes_linear_inbox_row(restarted, recovered)

    handle_message.assert_not_awaited()
    stored = restarted.get(row.delivery_key)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.reason_code == "stalled_started_execution"


@pytest.mark.asyncio
async def test_execution_failure_is_durable_and_queues_one_error_activity(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="worker-1", now=NOW, lease_seconds=60)
    assert row is not None

    async def fake_handle(event):
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=True,
            thread_sessions_per_user=False,
        )
        task = asyncio.create_task(
            adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
        )
        adapter._session_tasks[session_key] = task

    adapter.handle_message = fake_handle  # type: ignore[invalid-assignment]
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        await adapter._dispatch_hermes_linear_inbox_row(inbox, row)

    stored = inbox.get(row.delivery_key)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.reason_code == "execution_failed"
    assert inbox.activity_states(row.delivery_key) == {
        "action": "pending",
        "error": "pending",
    }


@pytest.mark.asyncio
async def test_started_crash_recovers_only_when_no_persisted_turn_exists(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    first = inbox.lease_next(worker_id="crashed", now=NOW, lease_seconds=10)
    assert first is not None
    inbox.mark_scheduled(first.delivery_key, "crashed", NOW)
    inbox.mark_started(
        delivery_key=first.delivery_key,
        worker_id="crashed",
        now=NOW,
    )
    recovered = inbox.lease_next(worker_id="restarted", now=NOW + 11, lease_seconds=10)
    assert recovered is not None
    assert recovered.state == "started"

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW + 11)
        monkeypatch.setattr(
            adapter,
            "_hermes_linear_session_has_persisted_turn",
            AsyncMock(return_value=False),
        )
        await adapter._dispatch_hermes_linear_inbox_row(inbox, recovered)

    stored = inbox.get(first.delivery_key)
    assert stored is not None
    assert stored.state == "received"
    assert stored.reason_code == "unstarted_lease_recovered"


@pytest.mark.asyncio
async def test_started_crash_with_persisted_turn_fails_visible_instead_of_double_run(
    tmp_path,
):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    first = inbox.lease_next(worker_id="crashed", now=NOW, lease_seconds=10)
    assert first is not None
    inbox.mark_scheduled(first.delivery_key, "crashed", NOW)
    inbox.mark_started(
        delivery_key=first.delivery_key,
        worker_id="crashed",
        now=NOW,
    )
    recovered = inbox.lease_next(worker_id="restarted", now=NOW + 11, lease_seconds=10)
    assert recovered is not None
    handle_message = AsyncMock()
    adapter.handle_message = handle_message  # type: ignore[invalid-assignment]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW + 11)
        monkeypatch.setattr(
            adapter,
            "_hermes_linear_session_has_persisted_turn",
            AsyncMock(return_value=True),
        )
        await adapter._dispatch_hermes_linear_inbox_row(inbox, recovered)

    handle_message.assert_not_awaited()
    stored = inbox.get(first.delivery_key)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.reason_code == "stalled_started_execution"


@pytest.mark.asyncio
async def test_activity_retry_is_idempotent_and_does_not_duplicate_rows(tmp_path):
    adapter = _adapter(tmp_path)
    route = adapter._routes["linear-agent-session"]
    route["linear_activity_url"] = (
        "https://phoneitin.example.test/api/internal/linear/agent-session"
    )
    route["linear_activity_secret"] = "test-only-activity-secret"
    inbox = adapter._hermes_linear_inbox_for_route("linear-agent-session", route)
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )
    row = inbox.lease_next(worker_id="worker-1", now=NOW, lease_seconds=60)
    assert row is not None
    inbox.mark_scheduled(row.delivery_key, "worker-1", NOW)
    event = adapter._build_hermes_linear_event(row)
    inbox.mark_started(
        delivery_key=row.delivery_key,
        worker_id="worker-1",
        now=NOW,
        activity_body=adapter._hermes_linear_activity_body(event, "action"),
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW)
        monkeypatch.setattr(
            adapter,
            "_post_hermes_linear_activity",
            lambda *_args: ("retry", "network_or_timeout"),
        )
        assert await adapter._dispatch_hermes_linear_activity(inbox, "activity-worker")
    assert inbox.activity_states(row.delivery_key) == {"action": "pending"}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(time, "time", lambda: NOW + 3)
        monkeypatch.setattr(
            adapter,
            "_post_hermes_linear_activity",
            lambda *_args: ("delivered", "activity_accepted"),
        )
        assert await adapter._dispatch_hermes_linear_activity(inbox, "activity-worker")
    assert inbox.activity_states(row.delivery_key) == {"action": "delivered"}


@pytest.mark.asyncio
async def test_after_commit_failpoint_leaves_recoverable_row(tmp_path):
    adapter = _adapter(tmp_path)
    adapter._hermes_linear_failpoint = lambda name, _key: (
        (_ for _ in ()).throw(RuntimeError("simulated crash"))
        if name == "after_inbox_commit"
        else None
    )
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    body = _body()
    timestamp = int(time.time())

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/webhooks/linear-agent-session",
            data=body,
            headers=_headers(body, timestamp=timestamp),
        )
        assert response.status == 500

    inbox = adapter._hermes_linear_delivery_inboxes["linear-agent-session"]
    stored = inbox.get("linear:comment:comment-1")
    assert stored is not None
    assert stored.state == "received"


@pytest.mark.asyncio
async def test_health_exposes_bounded_inbox_states_without_bodies(tmp_path):
    adapter = _adapter(tmp_path)
    inbox = adapter._hermes_linear_inbox_for_route(
        "linear-agent-session", adapter._routes["linear-agent-session"]
    )
    body = _body()
    inbox.accept(
        delivery_key="linear:comment:comment-1",
        body_sha256=hashlib.sha256(body).hexdigest(),
        raw_body=body,
        received_at=NOW,
        route_name="linear-agent-session",
        profile=None,
    )

    response = await adapter._handle_health(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)
    assert payload["hermes_linear_inbox"] == {"linear-agent-session": {"received": 1}}
    assert "Sanitized human-authored follow-up" not in response.text
    assert "raw_body" not in response.text
