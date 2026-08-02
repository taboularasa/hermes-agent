"""Authenticated request contract for Phoneitin -> Hermes Linear delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal, Mapping

HERMES_LINEAR_REQUEST_CONTRACT = "hermes_linear_v1"
HERMES_LINEAR_SCHEMA_VERSION = "hermes.linear-agent-session.v1"
HERMES_LINEAR_SIGNED_ROUTE_NAME = "hermes-linear-v1"
HERMES_LINEAR_SIGNED_INGRESS_PATH = f"/webhooks/{HERMES_LINEAR_SIGNED_ROUTE_NAME}"
HERMES_LINEAR_MAX_BODY_BYTES = 65_536
HERMES_LINEAR_MAX_PROMPT_BYTES = 16_384
HERMES_LINEAR_FRESHNESS_SECONDS = 300

_DELIVERY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9:._-]{1,256}$")
_SIGNATURE_PATTERN = re.compile(r"^sha256=([0-9a-f]{64})$")
_ROOT_FIELDS = {
    "schemaVersion",
    "deliveryKey",
    "action",
    "organizationId",
    "destination",
    "issue",
    "agentSession",
    "prompt",
    "source",
}


@dataclass(frozen=True)
class HermesLinearVerification:
    ok: bool
    reason: str
    status: int
    payload: dict[str, Any] | None = None
    delivery_key: str | None = None
    body_sha256: str | None = None


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value).strip()
    return ""


def _reject(reason: str, status: int) -> HermesLinearVerification:
    return HermesLinearVerification(ok=False, reason=reason, status=status)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _required_text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _optional_text(value: Any, *, maximum: int) -> bool:
    return value is None or (
        isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= maximum
    )


def _exact_fields(value: Any, fields: set[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != fields:
        return None
    return value


def _valid_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_payload(payload: Any) -> str | None:
    root = _exact_fields(payload, _ROOT_FIELDS)
    if root is None:
        return "invalid_envelope_fields"
    if root.get("schemaVersion") != HERMES_LINEAR_SCHEMA_VERSION:
        return "unsupported_schema_version"

    delivery_key = _required_text(root.get("deliveryKey"), maximum=256)
    if delivery_key is None or _DELIVERY_KEY_PATTERN.fullmatch(delivery_key) is None:
        return "invalid_delivery_key"
    action = root.get("action")
    if action not in {"created", "prompted"}:
        return "invalid_action"
    if not _optional_text(root.get("organizationId"), maximum=128):
        return "invalid_organization_id"

    destination = _exact_fields(root.get("destination"), {"kind", "appUserId"})
    if (
        destination is None
        or destination.get("kind") != "hermes"
        or _required_text(destination.get("appUserId"), maximum=128) is None
    ):
        return "invalid_destination"

    issue = _exact_fields(
        root.get("issue"),
        {"id", "identifier", "title", "url", "executionContext"},
    )
    if issue is None:
        return "invalid_issue"
    for field, maximum in (("id", 128), ("identifier", 64), ("title", 512)):
        if _required_text(issue.get(field), maximum=maximum) is None:
            return "invalid_issue"
    if not _optional_text(issue.get("url"), maximum=2_048):
        return "invalid_issue"
    execution_context = _exact_fields(issue.get("executionContext"), {"externalKey"})
    expected_external_key = f"linear:{issue['identifier'].strip()}"
    if (
        execution_context is None
        or execution_context.get("externalKey") != expected_external_key
    ):
        return "invalid_execution_context"

    session = _exact_fields(
        root.get("agentSession"), {"id", "status", "url", "appUserId"}
    )
    if session is None:
        return "invalid_agent_session"
    if _required_text(session.get("id"), maximum=128) is None:
        return "invalid_agent_session"
    session_app_user = _required_text(session.get("appUserId"), maximum=128)
    if session_app_user is None or session_app_user != destination.get("appUserId"):
        return "invalid_agent_session"
    if not _optional_text(session.get("status"), maximum=64) or not _optional_text(
        session.get("url"), maximum=2_048
    ):
        return "invalid_agent_session"

    prompt = root.get("prompt")
    if prompt is None:
        if action == "prompted":
            return "missing_prompt"
    else:
        prompt_obj = _exact_fields(
            prompt, {"id", "commentId", "sourceCommentId", "body"}
        )
        if (
            prompt_obj is None
            or _required_text(prompt_obj.get("id"), maximum=128) is None
        ):
            return "invalid_prompt"
        if not _optional_text(
            prompt_obj.get("commentId"), maximum=128
        ) or not _optional_text(prompt_obj.get("sourceCommentId"), maximum=128):
            return "invalid_prompt"
        body = prompt_obj.get("body")
        if not isinstance(body, str) or not body:
            return "invalid_prompt"
        if len(body.encode("utf-8")) > HERMES_LINEAR_MAX_PROMPT_BYTES:
            return "prompt_too_large"

    source = _exact_fields(root.get("source"), {"eventTimestamp", "traceId"})
    if (
        source is None
        or not _valid_iso_timestamp(source.get("eventTimestamp"))
        or _required_text(source.get("traceId"), maximum=256) is None
    ):
        return "invalid_source"
    return None


def verify_hermes_linear_request(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str,
    now: float,
) -> HermesLinearVerification:
    """Verify signature, freshness, key agreement, and strict v1 schema."""
    if len(raw_body) > HERMES_LINEAR_MAX_BODY_BYTES:
        return _reject("payload_too_large", 413)
    if not secret:
        return _reject("missing_receiver_secret", 403)

    timestamp_text = _header(headers, "X-Hermes-Timestamp")
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return _reject("missing_or_invalid_timestamp", 401)
    if abs(now - timestamp) > HERMES_LINEAR_FRESHNESS_SECONDS:
        return _reject("stale_timestamp", 401)

    signature = _header(headers, "X-Hermes-Signature-256")
    signature_match = _SIGNATURE_PATTERN.fullmatch(signature)
    if signature_match is None:
        return _reject("missing_or_invalid_signature", 401)
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp_text.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).digest()
    supplied = bytes.fromhex(signature_match.group(1))
    if not hmac.compare_digest(expected, supplied):
        return _reject("invalid_signature", 401)

    try:
        payload = json.loads(raw_body, object_pairs_hook=_object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _reject("invalid_json", 400)
    schema_error = _validate_payload(payload)
    if schema_error:
        return _reject(schema_error, 400)

    delivery_key = str(payload["deliveryKey"])
    if _header(headers, "X-Hermes-Delivery-Key") != delivery_key:
        return _reject("delivery_key_mismatch", 409)
    return HermesLinearVerification(
        ok=True,
        reason="accepted",
        status=202,
        payload=payload,
        delivery_key=delivery_key,
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
    )


ClaimResult = Literal["accepted", "duplicate", "conflict"]


class HermesLinearDeliveryLedger:
    """SQLite delivery-key claim ledger for restart-safe duplicate suppression."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def claim(
        self, delivery_key: str, body_sha256: str, received_at: int
    ) -> ClaimResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5.0) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hermes_linear_deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                )
                """
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO hermes_linear_deliveries
                    (delivery_key, body_sha256, received_at)
                VALUES (?, ?, ?)
                """,
                (delivery_key, body_sha256, received_at),
            )
            if cursor.rowcount == 1:
                return "accepted"
            row = connection.execute(
                "SELECT body_sha256 FROM hermes_linear_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        return "duplicate" if row and row[0] == body_sha256 else "conflict"


def default_hermes_linear_ledger_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "webhook-deliveries.db"
