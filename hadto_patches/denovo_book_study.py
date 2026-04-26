"""Safe book-study response contract for De Novo -> Hermes Slack turns.

Hermes may answer De Novo with source-linked memory references. Slack-facing
text and proof artifacts must stay citable and redacted: stable identifiers,
hashes, source artifact IRIs, and citation locators are allowed; raw memory,
raw chapter text, tokens, URLs, and private endpoints are not.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from hadto_patches.denovo_source_reference import (
    INFORMAL_CONTEXT,
    NEEDS_SOURCE_RETRIEVAL,
    ONTOLOGY_COMMIT_READY,
    SourceLinkedHermesReference,
    SourceReferenceError,
    build_reference_export,
)


BOOK_STUDY_CONTEXT = "context"
BOOK_STUDY_NO_CONTEXT = "no_context"
BOOK_STUDY_RESPONSE_SCHEMA = "hermes.denovo.book_study_response.v1"
BOOK_STUDY_FIXTURE_PROOF_SCHEMA = "hermes.denovo.book_study_fixture_proof.v1"
BOOK_STUDY_ISSUE = "HAD-547"
BOOK_STUDY_REQUEST_KINDS = frozenset(
    {
        "book-study",
        "book_study",
        "book-study-context",
        "chapter-prior-context",
    },
)

_FORBIDDEN_TEXT_FRAGMENTS = (
    "raw hermes memory",
    "raw memory dump",
    "raw chapter text",
    "raw study note",
    "chapter body:",
    "full chapter:",
    "transcript body:",
    "http://",
    "https://",
    "hooks.slack.com",
    "response_url",
    "responseurl",
    "xoxb-",
    "xoxa-",
    "xapp-",
    "bearer ",
    "cookie=",
    "set-cookie",
    "<script",
    "</script",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "de-novo-vm",
    "denovo-vm",
    "restate://",
    "nats://",
    "postgres://",
    "sqlite:",
    "minio://",
)


class BookStudyResponseError(ValueError):
    """Raised when a book-study response would be unsafe or malformed."""


@dataclass(frozen=True)
class BookStudyMemoryReference:
    """Source-linked Hermes memory reference safe for De Novo ingestion."""

    reference_id: str
    kind: str
    memory_summary_sha256: str
    source_artifact_iri: str
    source_stable_id: str
    source_sha256: str
    citation_locator: str

    @property
    def ontology_readiness(self) -> str:
        return self.to_source_reference().ontology_readiness

    def validate(self) -> None:
        try:
            self.to_source_reference().validate()
        except SourceReferenceError as exc:
            raise BookStudyResponseError(str(exc)) from exc

    def to_source_reference(self) -> SourceLinkedHermesReference:
        return SourceLinkedHermesReference(
            reference_id=self.reference_id,
            kind=self.kind,
            memory_summary_sha256=self.memory_summary_sha256,
            source_artifact_iri=self.source_artifact_iri,
            source_stable_id=self.source_stable_id,
            source_sha256=self.source_sha256,
            citation_locator=self.citation_locator,
        )

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return self.to_source_reference().to_dict()

    def to_slack_line(self) -> str:
        self.validate()
        return self.to_source_reference().to_public_line()


@dataclass(frozen=True)
class BookStudyResponse:
    """A Slack-safe Hermes book-study response with optional references."""

    status: str
    public_summary: str
    references: tuple[BookStudyMemoryReference, ...]
    response_sha256: str

    def validate(self) -> None:
        if self.status not in {BOOK_STUDY_CONTEXT, BOOK_STUDY_NO_CONTEXT}:
            raise BookStudyResponseError("status must be context or no_context")
        _validate_safe_text("public_summary", self.public_summary)
        if self.status == BOOK_STUDY_CONTEXT and not self.references:
            raise BookStudyResponseError("context response requires a reference")
        if self.status == BOOK_STUDY_NO_CONTEXT and self.references:
            raise BookStudyResponseError("no_context response must not claim references")
        for reference in self.references:
            reference.validate()
        expected = _response_hash(self.status, self.public_summary, self.references)
        if self.response_sha256 != expected:
            raise BookStudyResponseError("response_sha256 does not match response content")

    def to_slack_text(self) -> str:
        self.validate()
        if self.status == BOOK_STUDY_NO_CONTEXT:
            return (
                "No citable Hermes book-study or ontology context matched this Slack thread.\n"
                f"Response SHA256: `{self.response_sha256}`\n"
                "References: none"
            )

        lines = [
            "Hermes book-study context:",
            self.public_summary,
            "",
            "References for De Novo ingestion:",
        ]
        lines.extend(reference.to_slack_line() for reference in self.references)
        lines.append(f"Response SHA256: `{self.response_sha256}`")
        return "\n".join(lines)

    def to_proof(self) -> dict[str, Any]:
        self.validate()
        reference_export = _source_reference_export(self.response_sha256, self.references)
        return {
            "schema": BOOK_STUDY_RESPONSE_SCHEMA,
            "status": self.status,
            "response_sha256": self.response_sha256,
            "reference_count": len(self.references),
            "readiness_counts": _readiness_counts(self.references),
            "citation_locator_count": sum(1 for ref in self.references if ref.citation_locator),
            "references": [ref.to_dict() for ref in self.references],
            "source_reference_export": reference_export.to_proof() if reference_export else None,
            "raw_hermes_memory_stored": False,
            "raw_chapter_text_stored": False,
            "empty_context": self.status == BOOK_STUDY_NO_CONTEXT,
            "redaction": redaction_proof(
                self.to_slack_text(),
                self.to_dict(redacted=True),
            ),
        }

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        self.validate()
        payload = {
            "schema": BOOK_STUDY_RESPONSE_SCHEMA,
            "status": self.status,
            "responseSha256": self.response_sha256,
            "references": [ref.to_dict() for ref in self.references],
        }
        if not redacted:
            payload["publicSummary"] = self.public_summary
        else:
            payload["publicSummarySha256"] = sha256_text(self.public_summary)
        return payload


def build_book_study_response(
    *,
    public_summary: str,
    references: Iterable[BookStudyMemoryReference],
    status: str = BOOK_STUDY_CONTEXT,
) -> BookStudyResponse:
    """Build and validate a safe context-bearing book-study response."""
    refs = tuple(references)
    response = BookStudyResponse(
        status=status,
        public_summary=public_summary.strip(),
        references=refs,
        response_sha256=_response_hash(status, public_summary.strip(), refs),
    )
    response.validate()
    return response


def build_no_context_book_study_response() -> BookStudyResponse:
    """Build the deterministic empty-context response."""
    summary = "No citable Hermes book-study or ontology context matched this Slack thread."
    return build_book_study_response(
        public_summary=summary,
        references=(),
        status=BOOK_STUDY_NO_CONTEXT,
    )


def build_book_study_fixture_proof(
    *,
    slack_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build deterministic HAD-547 proof for context and no-context paths."""
    context_response = fixture_book_study_response()
    no_context_response = build_no_context_book_study_response()
    identity = slack_identity or {
        "team_id": "T123HADTO",
        "channel_id": "C123BOOKSTUDY",
        "thread_ts": "1760000123.000000",
        "message_ts": "1760000123.000400",
        "app_id_seen": "true",
        "bot_id_seen": "true",
        "metadata_event": "de_novo.hermes_wakeup",
    }
    safe_identity = {
        "team_id_seen": bool(identity.get("team_id")),
        "channel_id": identity.get("channel_id", ""),
        "thread_ts": identity.get("thread_ts", ""),
        "message_ts": identity.get("message_ts", ""),
        "app_id_seen": bool(identity.get("app_id") or identity.get("app_id_seen")),
        "bot_id_seen": bool(identity.get("bot_id") or identity.get("bot_id_seen")),
        "metadata_event": identity.get("metadata_event", ""),
    }
    proof = {
        "schema": BOOK_STUDY_FIXTURE_PROOF_SCHEMA,
        "issue": BOOK_STUDY_ISSUE,
        "status": "passed",
        "slack_identity": safe_identity,
        "context_case": context_response.to_proof(),
        "no_context_case": no_context_response.to_proof(),
        "checks": [
            "context case exposes at least one source-linked Hermes memory reference",
            "no-context case is explicit and claims zero references",
            "proof carries Slack channel/thread/message identity without tokens or response URLs",
            "proof omits unredacted Hermes memory, chapter passages, study notes, and private endpoints",
        ],
    }
    proof["redaction"] = redaction_proof(
        safe_identity,
        context_response.to_dict(redacted=True),
        no_context_response.to_dict(redacted=True),
        proof["checks"],
    )
    if any(proof["redaction"].values()):
        raise BookStudyResponseError("fixture proof contains unsafe content")
    return proof


def fixture_book_study_response() -> BookStudyResponse:
    """Deterministic context response used by Hermes/De Novo compatibility tests."""
    references = (
        BookStudyMemoryReference(
            reference_id="hermes-memory-had547-book-study",
            kind="book-study-memory",
            memory_summary_sha256=sha256_text(
                "Hermes prior book study routine context with source links",
            ),
            source_artifact_iri="urn:denovo:source:minio:had547-book-study",
            source_stable_id="minio:had547-book-study",
            source_sha256=sha256_text("had547 source-linked book study reference"),
            citation_locator="paragraph:3",
        ),
    )
    return build_book_study_response(
        public_summary=(
            "Hermes connects this question to prior book-study practice and ontology "
            "source-linking discipline. Treat the cited reference as context only; "
            "De Novo should make any ontology change through its own source gate."
        ),
        references=references,
    )


def is_book_study_wakeup(notification: Any, thread_context: str = "") -> bool:
    """Return whether a De Novo wake-up should use book-study handling."""
    request_kind = str(getattr(notification, "request_kind", "") or "").strip().lower()
    if request_kind in BOOK_STUDY_REQUEST_KINDS:
        return True
    context = thread_context.lower()
    return (
        "book-study" in context
        or "book study" in context
        or "chapter" in context
        or "ontology context" in context
    )


def build_book_study_event_text(notification: Any, *, thread_context: str) -> str:
    """Build the task-shaped event text Hermes receives for book-study turns."""
    identity = notification.identity
    cleaned_context = sanitize_book_study_thread_context(thread_context)
    lines = [
        "De Novo is asking Hermes for book-study context in Slack.",
        "Answer in the referenced Slack thread only, using Hermes' own Slack credentials.",
        "Write a concise book-club style reply grounded in Hermes memory and ontology context.",
        "If no citable Hermes context matches the question, say that explicitly and do not fabricate.",
        (
            "For every contextual claim, make a source-linked reference available in this shape: "
            "`reference_id`, `kind`, `memory_summary_sha256`, `source_artifact_iri`, "
            "`source_stable_id`, `source_sha256`, and `citation_locator`."
        ),
        "Do not paste unredacted Hermes memory, chapter passages, study notes, transcripts, tokens, URLs, or private endpoint details.",
        f"Slack channel: {identity.channel_id}",
        f"Slack parent thread_ts: {identity.thread_ts}",
        f"Slack message ts: {identity.message_ts}",
        f"Slack metadata event: {identity.metadata_event}",
        f"Redacted context hashes: {', '.join(identity.context_sha256)}",
    ]
    if cleaned_context.strip():
        lines.extend(("", cleaned_context.strip()))
    else:
        lines.extend(("", "No Slack thread context was available; produce the no-context response if Hermes cannot cite a matching memory."))
    return "\n".join(lines)


def sanitize_book_study_thread_context(thread_context: str) -> str:
    """Remove unsafe raw/private lines from Slack thread context."""
    safe_lines: list[str] = []
    for line in thread_context.splitlines():
        stripped = line.strip()
        if not stripped:
            safe_lines.append(line)
            continue
        lowered = stripped.lower()
        if any(fragment in lowered for fragment in _FORBIDDEN_TEXT_FRAGMENTS):
            safe_lines.append(
                "[redacted unsafe book-study context line "
                f"sha256={sha256_text(stripped)}]",
            )
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines)


def redaction_proof(*values: Any) -> dict[str, bool]:
    """Return booleans proving values do not carry unsafe response material."""
    text = json.dumps(values, sort_keys=True, default=str).lower()
    return {
        "contains_secrets": any(
            fragment in text
            for fragment in ("xox", "bearer ", "api_key", "access_token")
        ),
        "contains_public_url": "https://" in text or "http://" in text,
        "contains_response_url": "response_url" in text or "hooks.slack.com" in text,
        "contains_raw_hermes_memory": "raw hermes memory" in text or "raw memory dump" in text,
        "contains_raw_chapter_text": "raw chapter text" in text or "chapter body:" in text,
        "contains_private_endpoint": any(
            fragment in text
            for fragment in (
                "localhost",
                "127.0.0.1",
                "0.0.0.0",
                "de-novo-vm",
                "denovo-vm",
                "restate://",
                "nats://",
                "postgres://",
                "sqlite:",
                "minio://",
            )
        ),
        "contains_cookie": "cookie=" in text or "set-cookie" in text,
        "contains_html_script": "<script" in text or "</script" in text,
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _response_hash(
    status: str,
    public_summary: str,
    references: tuple[BookStudyMemoryReference, ...],
) -> str:
    payload = {
        "status": status,
        "publicSummary": public_summary,
        "references": [ref.to_dict() for ref in references],
    }
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _source_reference_export(
    response_sha256: str,
    references: tuple[BookStudyMemoryReference, ...],
):
    if not references:
        return None
    return build_reference_export(
        response_id=f"book-study-{response_sha256}",
        references=(ref.to_source_reference() for ref in references),
    )


def _readiness_counts(references: tuple[BookStudyMemoryReference, ...]) -> dict[str, int]:
    counts = {
        ONTOLOGY_COMMIT_READY: 0,
        NEEDS_SOURCE_RETRIEVAL: 0,
        INFORMAL_CONTEXT: 0,
    }
    for reference in references:
        counts[reference.ontology_readiness] += 1
    return counts


def _validate_safe_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BookStudyResponseError(f"{name} is required")
    lowered = value.lower()
    for fragment in _FORBIDDEN_TEXT_FRAGMENTS:
        if fragment in lowered:
            raise BookStudyResponseError(f"{name} contains unsafe content")
