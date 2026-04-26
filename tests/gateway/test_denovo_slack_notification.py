"""Tests for De Novo Slack-thread wake-up payload validation."""

import pytest

from hadto_patches.denovo_slack_notification import (
    AVAILABILITY_PLANE,
    EXECUTION_ENGINE,
    INGRESS_CONVENTION,
    SLACK_BOT_MESSAGE_SUBTYPE,
    SLACK_METADATA_EVENT,
    DeNovoSlackNotificationError,
    build_denovo_slack_message_event,
    denovo_slack_wakeup_proof,
    parse_denovo_slack_notification,
    redacted_notification_payload,
)
from gateway.config import Platform
from gateway.session import build_session_key


HASH_A = "a" * 64
HASH_B = "b" * 64


def _payload(**overrides):
    payload = {
        "signalId": "signal-had-546",
        "sourceRequestId": "request-had-546",
        "targetAgentId": "hermes",
        "availabilityPlane": AVAILABILITY_PLANE,
        "ingressConvention": INGRESS_CONVENTION,
        "executionEngine": EXECUTION_ENGINE,
        "webhookInfraOnly": True,
        "usesDeNovoExecutionKernel": False,
        "messageIdentity": {
            "teamId": "T123ABC",
            "channelId": "C123ABC",
            "messageTs": "1714060000.123456",
            "threadTs": "1714060000.000001",
            "appId": "A123ABC",
            "botId": "B123ABC",
            "messageSubtype": SLACK_BOT_MESSAGE_SUBTYPE,
            "metadataEvent": SLACK_METADATA_EVENT,
            "idempotencyKey": "denovo:C123ABC:1714060000.123456",
            "contextSha256": [HASH_A, HASH_B],
            "permalinkSha256": "c" * 64,
        },
    }
    identity_overrides = overrides.pop("messageIdentity", None)
    if identity_overrides is not None:
        payload["messageIdentity"].update(identity_overrides)
    payload.update(overrides)
    return payload


def test_valid_payload_normalizes_slack_identity():
    notification = parse_denovo_slack_notification(_payload())

    assert notification.webhook_infra_only is True
    assert notification.uses_de_novo_execution_kernel is False
    assert notification.identity.team_id == "T123ABC"
    assert notification.identity.channel_id == "C123ABC"
    assert notification.identity.message_ts == "1714060000.123456"
    assert notification.identity.thread_ts == "1714060000.000001"
    assert notification.identity.app_id == "A123ABC"
    assert notification.identity.bot_id == "B123ABC"
    assert notification.identity.metadata_event == SLACK_METADATA_EVENT
    assert notification.identity.idempotency_key == "denovo:C123ABC:1714060000.123456"
    assert notification.identity.context_sha256 == (HASH_A, HASH_B)


def test_missing_thread_ts_derives_from_message_ts():
    notification = parse_denovo_slack_notification(
        _payload(messageIdentity={"threadTs": ""}),
    )

    assert notification.identity.thread_ts == "1714060000.123456"


def test_builds_slack_sourced_message_event_for_thread_dispatch():
    notification = parse_denovo_slack_notification(_payload())
    event = build_denovo_slack_message_event(
        notification,
        thread_context="[Thread context]\nDe Novo: What did Hermes learn?",
    )

    assert event.source.platform == Platform.SLACK
    assert event.source.chat_id == "C123ABC"
    assert event.source.chat_type == "group"
    assert event.source.thread_id == "1714060000.000001"
    assert event.source.user_id == "B123ABC"
    assert event.source.user_id_alt == "A123ABC"
    assert event.message_id == "1714060000.123456"
    assert event.reply_to_message_id == "1714060000.000001"
    assert "webhook:" not in build_session_key(event.source)
    assert "What did Hermes learn?" in event.text
    assert "Redacted context hashes" in event.text


def test_builds_dm_message_event_when_channel_is_im():
    notification = parse_denovo_slack_notification(
        _payload(messageIdentity={"channelId": "D123ABC"}),
    )
    event = build_denovo_slack_message_event(notification)

    assert event.source.platform == Platform.SLACK
    assert event.source.chat_type == "dm"
    assert event.source.chat_id == "D123ABC"


def test_redacted_raw_payload_keeps_identity_without_private_handles():
    notification = parse_denovo_slack_notification(_payload())
    raw = redacted_notification_payload(notification)
    raw_text = repr(raw).lower()

    assert raw["slack"]["app_id"] == "A123ABC"
    assert raw["slack"]["bot_id"] == "B123ABC"
    assert raw["slack"]["idempotency_key"] == "denovo:C123ABC:1714060000.123456"
    assert "https://" not in raw_text
    assert "localhost" not in raw_text
    assert "127.0.0.1" not in raw_text
    assert "credential" not in raw_text


def test_redacted_proof_records_identity_without_raw_context_or_secrets():
    notification = parse_denovo_slack_notification(_payload())
    proof = denovo_slack_wakeup_proof(
        notification,
        status="accepted",
        delivery_id="delivery-1",
        thread_context_fetched=True,
    )
    proof_text = repr(proof).lower()

    assert proof["status"] == "accepted"
    assert proof["slack_identity_seen"] is True
    assert proof["context_hash_count"] == 2
    assert proof["thread_context_fetched"] is True
    assert proof["raw_context_logged"] is False
    assert proof["secret_logged"] is False
    assert proof["de_novo_private_endpoint_seen"] is False
    assert "https://" not in proof_text
    assert "localhost" not in proof_text
    assert "bearer " not in proof_text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channelId", ""),
        ("channelId", "not-a-channel"),
        ("messageTs", ""),
        ("messageTs", "1714060000"),
        ("threadTs", "1714060000"),
        ("teamId", "W123"),
        ("appId", "B123ABC"),
        ("botId", "A123ABC"),
        ("idempotencyKey", ".."),
    ],
)
def test_rejects_missing_or_malformed_slack_identity(field, value):
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(_payload(messageIdentity={field: value}))


def test_rejects_missing_message_identity():
    payload = _payload()
    del payload["messageIdentity"]

    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(payload)


def test_rejects_invalid_context_hashes():
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(
            _payload(messageIdentity={"contextSha256": ["not-a-hash"]}),
        )


def test_rejects_non_wakeup_slack_metadata_event():
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(
            _payload(messageIdentity={"metadataEvent": "other.event"}),
        )


def test_rejects_non_bot_message_subtype():
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(
            _payload(messageIdentity={"messageSubtype": "message_changed"}),
        )


@pytest.mark.parametrize(
    "override",
    [
        {"availabilityPlane": "self-hosted-restate"},
        {"ingressConvention": "other-funnel"},
        {"executionEngine": "restate-cloud"},
        {"webhookInfraOnly": False},
        {"usesDeNovoExecutionKernel": True},
    ],
)
def test_rejects_restate_boundary_confusion(override):
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(_payload(**override))


@pytest.mark.parametrize(
    "override",
    [
        {"denovoVmUrl": "https://de-novo-vm.local/admin"},
        {"privateEndpoint": "127.0.0.1:8080"},
        {"messageIdentity": {"responseUrl": "https://hooks.slack.com/services/T/B/X"}},
        {"messageIdentity": {"credential": "Bearer secret-token"}},
        {"messageIdentity": {"persistenceHandle": "minio://de-novo/private"}},
    ],
)
def test_rejects_de_novo_private_or_secret_bearing_fields(override):
    with pytest.raises(DeNovoSlackNotificationError):
        parse_denovo_slack_notification(_payload(**override))
