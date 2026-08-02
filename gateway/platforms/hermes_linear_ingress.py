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
HERMES_LINEAR_RECEIPT_MAX_BYTES = 2_048
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


def hermes_linear_acceptance_receipt(
    *,
    status: Literal["accepted", "duplicate"],
    delivery_key: str,
    body_sha256: str,
) -> dict[str, str]:
    """Return the exact bounded receipt Phoneitin validates after inbox commit."""
    return {
        "contract": HERMES_LINEAR_REQUEST_CONTRACT,
        "schemaVersion": HERMES_LINEAR_SCHEMA_VERSION,
        "status": status,
        "deliveryKey": delivery_key,
        "bodySha256": body_sha256,
    }


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

InboxState = Literal[
    "received", "scheduled", "started", "completed", "failed", "conflict"
]


@dataclass(frozen=True)
class HermesLinearInboxDelivery:
    delivery_key: str
    body_sha256: str
    raw_body: bytes
    execution_id: str
    route_name: str
    profile: str | None
    state: InboxState
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: int | None
    received_at: int
    updated_at: int
    reason_code: str | None


@dataclass(frozen=True)
class HermesLinearInboxActivity:
    delivery_key: str
    kind: Literal["action", "error"]
    request_body: bytes
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: int | None


def hermes_linear_execution_id(delivery_key: str) -> str:
    """Stable non-secret identity shared by inbox job and gateway session."""
    return hashlib.sha256(f"hermes-linear:{delivery_key}".encode()).hexdigest()


def hermes_linear_session_chat_id(route_name: str, execution_id: str) -> str:
    return f"webhook:{route_name}:hermes-linear-{execution_id[:32]}"


class HermesLinearDeliveryInbox:
    """SQLite inbox, execution lifecycle, and idempotent activity queue.

    HTTP acceptance commits the exact verified body here. Workers use expiring
    leases; the delivery-derived execution identity anchors gateway restart
    recovery. No signature or secret is persisted.
    """

    _DELIVERY_COLUMNS: dict[str, str] = {
        "execution_id": "TEXT NOT NULL DEFAULT ''",
        "raw_body": "BLOB NOT NULL DEFAULT X''",
        "route_name": "TEXT NOT NULL DEFAULT 'linear-agent-session'",
        "profile": "TEXT",
        "state": "TEXT NOT NULL DEFAULT 'received'",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "lease_owner": "TEXT",
        "lease_expires_at": "INTEGER",
        "updated_at": "INTEGER NOT NULL DEFAULT 0",
        "scheduled_at": "INTEGER",
        "started_at": "INTEGER",
        "completed_at": "INTEGER",
        "reason_code": "TEXT",
        "conflict_count": "INTEGER NOT NULL DEFAULT 0",
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hermes_linear_deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    execution_id TEXT NOT NULL,
                    raw_body BLOB NOT NULL,
                    route_name TEXT NOT NULL,
                    profile TEXT,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    scheduled_at INTEGER,
                    started_at INTEGER,
                    completed_at INTEGER,
                    reason_code TEXT,
                    conflict_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(hermes_linear_deliveries)"
                )
            }
            for name, definition in self._DELIVERY_COLUMNS.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE hermes_linear_deliveries ADD COLUMN {name} {definition}"
                    )
            # PR #193 rows contain a permanent key/hash claim but no executable
            # body. Preserve them for diagnostics and make the stranded state
            # explicit rather than pretending they are recoverable jobs.
            connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET state = 'failed',
                       reason_code = 'legacy_claim_missing_body',
                       updated_at = CASE WHEN updated_at = 0 THEN received_at ELSE updated_at END,
                       execution_id = CASE
                         WHEN execution_id = '' THEN 'legacy-' || delivery_key
                         ELSE execution_id
                       END
                 WHERE length(raw_body) = 0
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS hermes_linear_execution_id_idx
                    ON hermes_linear_deliveries(execution_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS hermes_linear_inbox_ready_idx
                    ON hermes_linear_deliveries(state, lease_expires_at, received_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hermes_linear_activities (
                    delivery_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    request_body BLOB NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at INTEGER,
                    reason_code TEXT,
                    created_at INTEGER NOT NULL,
                    delivered_at INTEGER,
                    PRIMARY KEY (delivery_key, kind),
                    FOREIGN KEY (delivery_key)
                        REFERENCES hermes_linear_deliveries(delivery_key)
                )
                """
            )

    @staticmethod
    def _delivery(row: sqlite3.Row) -> HermesLinearInboxDelivery:
        return HermesLinearInboxDelivery(
            delivery_key=str(row["delivery_key"]),
            body_sha256=str(row["body_sha256"]),
            raw_body=bytes(row["raw_body"]),
            execution_id=str(row["execution_id"]),
            route_name=str(row["route_name"]),
            profile=str(row["profile"]) if row["profile"] is not None else None,
            state=str(row["state"]),  # type: ignore[arg-type]
            attempt_count=int(row["attempt_count"]),
            lease_owner=(
                str(row["lease_owner"]) if row["lease_owner"] is not None else None
            ),
            lease_expires_at=(
                int(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            received_at=int(row["received_at"]),
            updated_at=int(row["updated_at"]),
            reason_code=(
                str(row["reason_code"]) if row["reason_code"] is not None else None
            ),
        )

    def accept(
        self,
        *,
        delivery_key: str,
        body_sha256: str,
        raw_body: bytes,
        received_at: int,
        route_name: str,
        profile: str | None,
    ) -> ClaimResult:
        execution_id = hermes_linear_execution_id(delivery_key)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO hermes_linear_deliveries (
                    delivery_key, body_sha256, received_at, execution_id,
                    raw_body, route_name, profile, state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?)
                """,
                (
                    delivery_key,
                    body_sha256,
                    received_at,
                    execution_id,
                    raw_body,
                    route_name,
                    profile,
                    received_at,
                ),
            )
            if cursor.rowcount == 1:
                return "accepted"
            row = connection.execute(
                "SELECT body_sha256 FROM hermes_linear_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
            if row and row["body_sha256"] == body_sha256:
                return "duplicate"
            connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET conflict_count = conflict_count + 1,
                       reason_code = 'delivery_key_body_conflict',
                       updated_at = ?
                 WHERE delivery_key = ?
                """,
                (received_at, delivery_key),
            )
            return "conflict"

    def get(self, delivery_key: str) -> HermesLinearInboxDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hermes_linear_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        return self._delivery(row) if row else None

    def get_by_execution_id(
        self, execution_id: str
    ) -> HermesLinearInboxDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hermes_linear_deliveries WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return self._delivery(row) if row else None

    def get_by_execution_prefix(
        self, execution_prefix: str
    ) -> HermesLinearInboxDelivery | None:
        if len(execution_prefix) < 32:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM hermes_linear_deliveries
                 WHERE execution_id LIKE ?
                 LIMIT 2
                """,
                (f"{execution_prefix}%",),
            ).fetchall()
        return self._delivery(rows[0]) if len(rows) == 1 else None

    def lease_next(
        self, *, worker_id: str, now: int, lease_seconds: int
    ) -> HermesLinearInboxDelivery | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                  FROM hermes_linear_deliveries
                 WHERE state IN ('received', 'scheduled', 'started')
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                 ORDER BY received_at, delivery_key
                 LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET lease_owner = ?, lease_expires_at = ?,
                       attempt_count = attempt_count + 1, updated_at = ?
                 WHERE delivery_key = ?
                   AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                (
                    worker_id,
                    now + lease_seconds,
                    now,
                    row["delivery_key"],
                    now,
                ),
            )
            leased = connection.execute(
                "SELECT * FROM hermes_linear_deliveries WHERE delivery_key = ?",
                (row["delivery_key"],),
            ).fetchone()
        return self._delivery(leased) if leased else None

    def mark_scheduled(self, delivery_key: str, worker_id: str, now: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET state = CASE WHEN state = 'received' THEN 'scheduled' ELSE state END,
                       scheduled_at = COALESCE(scheduled_at, ?), updated_at = ?
                 WHERE delivery_key = ? AND lease_owner = ?
                   AND state IN ('received', 'scheduled')
                """,
                (now, now, delivery_key, worker_id),
            )
        return cursor.rowcount == 1

    def mark_started(
        self,
        *,
        delivery_key: str,
        worker_id: str,
        now: int,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET state = 'started', started_at = COALESCE(started_at, ?),
                       updated_at = ?, reason_code = NULL
                 WHERE delivery_key = ? AND lease_owner = ?
                   AND state IN ('received', 'scheduled', 'started')
                """,
                (now, now, delivery_key, worker_id),
            )
        return cursor.rowcount == 1

    def enqueue_start_activity(
        self,
        *,
        delivery_key: str,
        worker_id: str,
        now: int,
        activity_body: bytes,
    ) -> bool:
        """Queue the idempotent start activity only after scheduler acceptance."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT 1 FROM hermes_linear_deliveries
                 WHERE delivery_key = ?
                   AND (
                     (lease_owner = ? AND state = 'started')
                     OR state IN ('completed', 'failed')
                   )
                """,
                (delivery_key, worker_id),
            ).fetchone()
            if row is None:
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO hermes_linear_activities (
                    delivery_key, kind, request_body, next_attempt_at, created_at
                ) VALUES (?, 'action', ?, ?, ?)
                """,
                (delivery_key, activity_body, now, now),
            )
        return cursor.rowcount == 1 or self.activity_states(delivery_key).get(
            "action"
        ) is not None

    def heartbeat(
        self, delivery_key: str, worker_id: str, now: int, lease_seconds: int
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET lease_expires_at = ?, updated_at = ?
                 WHERE delivery_key = ? AND lease_owner = ?
                   AND state IN ('scheduled', 'started')
                """,
                (now + lease_seconds, now, delivery_key, worker_id),
            )
        return cursor.rowcount == 1

    def finish(
        self,
        *,
        delivery_key: str,
        outcome: Literal["completed", "failed"],
        now: int,
        reason_code: str,
        error_activity_body: bytes | None = None,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET state = ?, completed_at = ?, updated_at = ?,
                       reason_code = ?, lease_owner = NULL, lease_expires_at = NULL
                 WHERE delivery_key = ?
                   AND (
                     state IN ('scheduled', 'started')
                     OR (state = 'failed' AND reason_code LIKE 'stalled_started_%')
                   )
                """,
                (outcome, now, now, reason_code, delivery_key),
            )
            if cursor.rowcount == 1 and error_activity_body is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO hermes_linear_activities (
                        delivery_key, kind, request_body, next_attempt_at, created_at
                    ) VALUES (?, 'error', ?, ?, ?)
                    """,
                    (delivery_key, error_activity_body, now, now),
                )
        return cursor.rowcount == 1

    def mark_stalled_started(
        self,
        *,
        delivery_key: str,
        worker_id: str,
        now: int,
        scheduler_evidence: str,
        activity_body: bytes | None = None,
    ) -> bool:
        """Fail an uncertain started claim visibly instead of replaying it.

        Once the start transition commits, a crash may have happened on either
        side of task creation. The generic gateway scheduler does not expose a
        cross-process exactly-once claim, so automatic replay would risk a
        second effective run. Persist the bounded evidence reason and let an
        already-running task complete the row through ``finish``.
        """
        reason_code = f"stalled_started_{scheduler_evidence}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE hermes_linear_deliveries
                   SET state = 'failed', reason_code = ?,
                       lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                 WHERE delivery_key = ? AND lease_owner = ? AND state = 'started'
                """,
                (reason_code, now, delivery_key, worker_id),
            )
            if cursor.rowcount == 1 and activity_body is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO hermes_linear_activities (
                        delivery_key, kind, request_body, next_attempt_at, created_at
                    ) VALUES (?, 'action', ?, ?, ?)
                    """,
                    (delivery_key, activity_body, now, now),
                )
        return cursor.rowcount == 1

    def state_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM hermes_linear_deliveries GROUP BY state"
            ).fetchall()
            conflict_count = int(
                connection.execute(
                    "SELECT COALESCE(SUM(conflict_count), 0) "
                    "FROM hermes_linear_deliveries"
                ).fetchone()[0]
            )
        counts = {str(row["state"]): int(row["count"]) for row in rows}
        if conflict_count:
            # A conflicting replay must be visible without changing the
            # authoritative original delivery's execution state.
            counts["conflict"] = conflict_count
        return counts

    def lease_activity(
        self, *, worker_id: str, now: int, lease_seconds: int
    ) -> HermesLinearInboxActivity | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT delivery_key, kind, request_body, attempt_count,
                       lease_owner, lease_expires_at
                  FROM hermes_linear_activities
                 WHERE (
                   state = 'pending' AND next_attempt_at <= ?
                 ) OR (
                   state = 'leased' AND lease_expires_at <= ?
                 )
                 ORDER BY next_attempt_at, created_at
                 LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE hermes_linear_activities
                   SET state = 'leased', attempt_count = attempt_count + 1,
                       lease_owner = ?, lease_expires_at = ?, reason_code = NULL
                 WHERE delivery_key = ? AND kind = ?
                """,
                (
                    worker_id,
                    now + lease_seconds,
                    row["delivery_key"],
                    row["kind"],
                ),
            )
            leased = connection.execute(
                """
                SELECT delivery_key, kind, request_body, attempt_count,
                       lease_owner, lease_expires_at
                  FROM hermes_linear_activities
                 WHERE delivery_key = ? AND kind = ?
                """,
                (row["delivery_key"], row["kind"]),
            ).fetchone()
        if leased is None:
            return None
        return HermesLinearInboxActivity(
            delivery_key=str(leased["delivery_key"]),
            kind=str(leased["kind"]),  # type: ignore[arg-type]
            request_body=bytes(leased["request_body"]),
            attempt_count=int(leased["attempt_count"]),
            lease_owner=(
                str(leased["lease_owner"])
                if leased["lease_owner"] is not None
                else None
            ),
            lease_expires_at=(
                int(leased["lease_expires_at"])
                if leased["lease_expires_at"] is not None
                else None
            ),
        )

    def finish_activity(
        self,
        *,
        activity: HermesLinearInboxActivity,
        worker_id: str,
        result: Literal["delivered", "retry", "terminal"],
        now: int,
        reason_code: str,
    ) -> bool:
        next_attempt_at = now
        if result == "retry":
            next_attempt_at += min(300, 2 ** min(activity.attempt_count, 8))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE hermes_linear_activities
                   SET state = ?, next_attempt_at = ?, reason_code = ?,
                       delivered_at = CASE WHEN ? = 'delivered' THEN ? ELSE delivered_at END,
                       lease_owner = NULL, lease_expires_at = NULL
                 WHERE delivery_key = ? AND kind = ?
                   AND state = 'leased' AND lease_owner = ?
                """,
                (
                    "pending" if result == "retry" else result,
                    next_attempt_at,
                    reason_code,
                    result,
                    now,
                    activity.delivery_key,
                    activity.kind,
                    worker_id,
                ),
            )
        return cursor.rowcount == 1

    def activity_states(self, delivery_key: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, state FROM hermes_linear_activities WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchall()
        return {str(row["kind"]): str(row["state"]) for row in rows}

    def activity_state_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT kind || ':' || state AS activity_state, COUNT(*) AS count
                  FROM hermes_linear_activities
                 GROUP BY kind, state
                 ORDER BY kind, state
                """
            ).fetchall()
        return {str(row["activity_state"]): int(row["count"]) for row in rows}


class HermesLinearDeliveryLedger:
    """Compatibility facade for PR #193 callers; new code uses the inbox."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._inbox = HermesLinearDeliveryInbox(path)

    def claim(
        self, delivery_key: str, body_sha256: str, received_at: int
    ) -> ClaimResult:
        return self._inbox.accept(
            delivery_key=delivery_key,
            body_sha256=body_sha256,
            raw_body=b"{}",
            received_at=received_at,
            route_name="compatibility-ledger",
            profile=None,
        )


def default_hermes_linear_ledger_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "webhook-deliveries.db"


def default_hermes_linear_inbox_path() -> Path:
    return default_hermes_linear_ledger_path()
