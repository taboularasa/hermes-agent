"""De Novo -> Hermes Slack-thread wake-up payload validation.

The payload is a wake-up hint only. Slack remains the conversation surface and
Hermes must not receive De Novo VM credentials, URLs, or persistence handles.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


AVAILABILITY_PLANE = "restate-cloud-webhook-edge-only"
INGRESS_CONVENTION = "phoneitin-web-restate-funnel"
EXECUTION_ENGINE = "self-hosted-restate"
SLACK_METADATA_EVENT = "de_novo.hermes_wakeup"
SLACK_BOT_MESSAGE_SUBTYPE = "bot_message"

_SLACK_CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]{2,}$")
_SLACK_TEAM_ID_RE = re.compile(r"^T[A-Z0-9]{2,}$")
_SLACK_APP_ID_RE = re.compile(r"^A[A-Z0-9]{2,}$")
_SLACK_BOT_ID_RE = re.compile(r"^B[A-Z0-9]{2,}$")
_SLACK_TS_RE = re.compile(r"^[0-9]{10}\.[0-9]{6}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{2,160}$")

_FORBIDDEN_KEY_FRAGMENTS = (
    "credential",
    "secret",
    "token",
    "password",
    "cookie",
    "managementurl",
    "management_url",
    "privateurl",
    "private_url",
    "privateendpoint",
    "private_endpoint",
    "persistencehandle",
    "persistence_handle",
    "responseurl",
    "response_url",
)
_FORBIDDEN_VALUE_FRAGMENTS = (
    "http://",
    "https://",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "de-novo-vm",
    "denovo-vm",
    "minio://",
    "nats://",
    "sqlite:",
    "postgres://",
    "restate://",
    "hooks.slack.com",
    "bearer ",
)


class DeNovoSlackNotificationError(ValueError):
    """Raised when a De Novo Slack wake-up payload is invalid or unsafe."""


@dataclass(frozen=True)
class DeNovoSlackIdentity:
    team_id: str
    channel_id: str
    message_ts: str
    thread_ts: str
    app_id: str
    bot_id: str
    message_subtype: str
    metadata_event: str
    idempotency_key: str
    context_sha256: tuple[str, ...]
    permalink_sha256: str = ""

    @property
    def duplicate_key(self) -> str:
        return self.idempotency_key or f"{self.channel_id}:{self.message_ts}"


@dataclass(frozen=True)
class DeNovoSlackNotification:
    signal_id: str
    source_request_id: str
    target_agent_id: str
    availability_plane: str
    ingress_convention: str
    execution_engine: str
    webhook_infra_only: bool
    uses_de_novo_execution_kernel: bool
    identity: DeNovoSlackIdentity


def build_denovo_slack_message_event(
    notification: DeNovoSlackNotification,
    *,
    thread_context: str = "",
) -> MessageEvent:
    """Build the Slack-sourced event Hermes should process for a wake-up.

    The event is intentionally sourced from Slack, not from webhook, so normal
    Hermes reply delivery uses the original Slack channel and parent thread.
    """
    identity = notification.identity
    chat_type = "dm" if identity.channel_id.startswith("D") else "group"
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id=identity.channel_id,
        chat_name=identity.channel_id,
        chat_type=chat_type,
        user_id=identity.bot_id,
        user_name="De Novo",
        thread_id=identity.thread_ts,
        user_id_alt=identity.app_id,
    )
    text = _event_text(notification, thread_context=thread_context)
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        raw_message=redacted_notification_payload(notification),
        message_id=identity.message_ts,
        reply_to_message_id=identity.thread_ts if identity.thread_ts != identity.message_ts else None,
    )


def redacted_notification_payload(notification: DeNovoSlackNotification) -> dict[str, Any]:
    """Return the non-secret notification fields safe to attach to raw_message."""
    identity = notification.identity
    return {
        "type": "de_novo_slack_thread_wakeup",
        "signal_id": notification.signal_id,
        "source_request_id": notification.source_request_id,
        "target_agent_id": notification.target_agent_id,
        "availability_plane": notification.availability_plane,
        "ingress_convention": notification.ingress_convention,
        "execution_engine": notification.execution_engine,
        "webhook_infra_only": notification.webhook_infra_only,
        "uses_de_novo_execution_kernel": notification.uses_de_novo_execution_kernel,
        "slack": {
            "team_id": identity.team_id,
            "channel_id": identity.channel_id,
            "message_ts": identity.message_ts,
            "thread_ts": identity.thread_ts,
            "app_id": identity.app_id,
            "bot_id": identity.bot_id,
            "message_subtype": identity.message_subtype,
            "metadata_event": identity.metadata_event,
            "idempotency_key": identity.idempotency_key,
            "context_sha256": list(identity.context_sha256),
            "permalink_sha256": identity.permalink_sha256,
        },
    }


def parse_denovo_slack_notification(
    payload: Mapping[str, Any],
) -> DeNovoSlackNotification:
    """Validate and normalize a De Novo Slack-thread wake-up payload."""
    if not isinstance(payload, Mapping):
        raise DeNovoSlackNotificationError("payload must be an object")
    _reject_private_denovo_fields(payload)

    identity_payload = payload.get("messageIdentity")
    if not isinstance(identity_payload, Mapping):
        raise DeNovoSlackNotificationError("messageIdentity object is required")

    identity = _parse_identity(identity_payload)
    notification = DeNovoSlackNotification(
        signal_id=_optional_str(payload, "signalId"),
        source_request_id=_optional_str(payload, "sourceRequestId"),
        target_agent_id=_required_str(payload, "targetAgentId"),
        availability_plane=_required_str(payload, "availabilityPlane"),
        ingress_convention=_required_str(payload, "ingressConvention"),
        execution_engine=_required_str(payload, "executionEngine"),
        webhook_infra_only=_required_bool(payload, "webhookInfraOnly"),
        uses_de_novo_execution_kernel=_required_bool(
            payload, "usesDeNovoExecutionKernel",
        ),
        identity=identity,
    )
    if notification.availability_plane != AVAILABILITY_PLANE:
        raise DeNovoSlackNotificationError("availabilityPlane must be webhook-edge only")
    if notification.ingress_convention != INGRESS_CONVENTION:
        raise DeNovoSlackNotificationError("ingressConvention must match PhoneItIn funnel")
    if notification.execution_engine != EXECUTION_ENGINE:
        raise DeNovoSlackNotificationError("executionEngine must identify De Novo self-hosted Restate")
    if not notification.webhook_infra_only:
        raise DeNovoSlackNotificationError("webhookInfraOnly must be true")
    if notification.uses_de_novo_execution_kernel:
        raise DeNovoSlackNotificationError("usesDeNovoExecutionKernel must be false")
    return notification


def _parse_identity(payload: Mapping[str, Any]) -> DeNovoSlackIdentity:
    team_id = _optional_str(payload, "teamId")
    channel_id = _required_str(payload, "channelId")
    message_ts = _required_str(payload, "messageTs")
    thread_ts = _optional_str(payload, "threadTs") or message_ts
    app_id = _required_str(payload, "appId")
    bot_id = _required_str(payload, "botId")
    message_subtype = _required_str(payload, "messageSubtype")
    metadata_event = _required_str(payload, "metadataEvent")
    idempotency_key = _required_str(payload, "idempotencyKey")
    context_sha256 = _required_hashes(payload, "contextSha256")
    permalink_sha256 = _optional_str(payload, "permalinkSha256")

    _match_optional("teamId", team_id, _SLACK_TEAM_ID_RE)
    _match_required("channelId", channel_id, _SLACK_CHANNEL_ID_RE)
    _match_required("messageTs", message_ts, _SLACK_TS_RE)
    _match_required("threadTs", thread_ts, _SLACK_TS_RE)
    _match_required("appId", app_id, _SLACK_APP_ID_RE)
    _match_required("botId", bot_id, _SLACK_BOT_ID_RE)
    _match_required("idempotencyKey", idempotency_key, _IDEMPOTENCY_RE)
    _match_optional("permalinkSha256", permalink_sha256, _SHA256_RE)
    if message_subtype != SLACK_BOT_MESSAGE_SUBTYPE:
        raise DeNovoSlackNotificationError("messageSubtype must be bot_message")
    if metadata_event != SLACK_METADATA_EVENT:
        raise DeNovoSlackNotificationError("metadataEvent must be de_novo.hermes_wakeup")

    return DeNovoSlackIdentity(
        team_id=team_id,
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=thread_ts,
        app_id=app_id,
        bot_id=bot_id,
        message_subtype=message_subtype,
        metadata_event=metadata_event,
        idempotency_key=idempotency_key,
        context_sha256=context_sha256,
        permalink_sha256=permalink_sha256,
    )


def _event_text(notification: DeNovoSlackNotification, *, thread_context: str) -> str:
    identity = notification.identity
    lines = [
        "De Novo posted a Hermes-directed Slack message and sent a wake-up notification.",
        "Use Hermes' Slack credentials to answer in the referenced Slack thread.",
        f"Slack channel: {identity.channel_id}",
        f"Slack parent thread_ts: {identity.thread_ts}",
        f"Slack message ts: {identity.message_ts}",
        f"Slack metadata event: {identity.metadata_event}",
        f"Redacted context hashes: {', '.join(identity.context_sha256)}",
    ]
    if thread_context.strip():
        lines.extend(("", thread_context.strip()))
    return "\n".join(lines)


def _reject_private_denovo_fields(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            normalized = key_text.replace("-", "_").lower()
            compact = normalized.replace("_", "")
            if normalized in _FORBIDDEN_KEY_FRAGMENTS or compact in _FORBIDDEN_KEY_FRAGMENTS:
                raise DeNovoSlackNotificationError(f"unsafe De Novo field: {path}{key_text}")
            _reject_private_denovo_fields(child, f"{path}{key_text}.")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_private_denovo_fields(child, f"{path}{index}.")
        return
    if isinstance(value, str):
        lowered = value.lower()
        for fragment in _FORBIDDEN_VALUE_FRAGMENTS:
            if fragment in lowered:
                raise DeNovoSlackNotificationError("payload contains private or secret-bearing value")


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeNovoSlackNotificationError(f"{key} is required")
    return value.strip()


def _optional_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DeNovoSlackNotificationError(f"{key} must be a string")
    return value.strip()


def _required_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise DeNovoSlackNotificationError(f"{key} must be a boolean")
    return value


def _required_hashes(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise DeNovoSlackNotificationError(f"{key} must be a non-empty list")
    hashes: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _SHA256_RE.fullmatch(item):
            raise DeNovoSlackNotificationError(f"{key} contains an invalid sha256")
        hashes.append(item)
    return tuple(hashes)


def _match_required(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise DeNovoSlackNotificationError(f"{name} is malformed")


def _match_optional(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if value and not pattern.fullmatch(value):
        raise DeNovoSlackNotificationError(f"{name} is malformed")
