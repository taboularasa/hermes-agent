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
import re
from typing import Any, Iterable


BOOK_STUDY_CONTEXT = "context"
BOOK_STUDY_NO_CONTEXT = "no_context"
BOOK_STUDY_RESPONSE_SCHEMA = "hermes.denovo.book_study_response.v1"
BOOK_STUDY_REQUEST_KINDS = frozenset(
    {
        "book-study",
        "book_study",
        "book-study-context",
        "chapter-prior-context",
    },
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{2,160}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9._/-]{2,80}$")
_LOCATOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/# -]{1,160}$")

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

    def validate(self) -> None:
        _match("reference_id", self.reference_id, _STABLE_ID_RE)
        _match("kind", self.kind, _KIND_RE)
        _match("memory_summary_sha256", self.memory_summary_sha256, _SHA256_RE)
        _match("source_stable_id", self.source_stable_id, _STABLE_ID_RE)
        _match("source_sha256", self.source_sha256, _SHA256_RE)
        _match("citation_locator", self.citation_locator, _LOCATOR_RE)
        _validate_safe_text("source_artifact_iri", self.source_artifact_iri)
        if "://" in self.source_artifact_iri:
            raise BookStudyResponseError("source_artifact_iri must not be a URL")
        if not self.source_artifact_iri.startswith(("urn:", "minio:", "object:", "source:")):
            raise BookStudyResponseError("source_artifact_iri must be an artifact IRI")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {
            "id": self.reference_id,
            "kind": self.kind,
            "memorySummarySha256": self.memory_summary_sha256,
            "sourceArtifactIri": self.source_artifact_iri,
            "sourceStableId": self.source_stable_id,
            "sourceSha256": self.source_sha256,
            "citationLocator": self.citation_locator,
        }

    def to_slack_line(self) -> str:
        self.validate()
        return (
            f"- `{self.reference_id}` ({self.kind}) "
            f"summary_sha256={self.memory_summary_sha256} "
            f"source={self.source_artifact_iri} "
            f"stable_id={self.source_stable_id} "
            f"source_sha256={self.source_sha256} "
            f"locator={self.citation_locator}"
        )


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
        return {
            "schema": BOOK_STUDY_RESPONSE_SCHEMA,
            "status": self.status,
            "response_sha256": self.response_sha256,
            "reference_count": len(self.references),
            "citation_locator_count": sum(1 for ref in self.references if ref.citation_locator),
            "references": [ref.to_dict() for ref in self.references],
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
            citation_locator="chapter:1#paragraph:3",
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
            for fragment in ("xox", "bearer ", "api_key", "access_token", "secret")
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


def _match(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise BookStudyResponseError(f"{name} is malformed")


def _validate_safe_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BookStudyResponseError(f"{name} is required")
    lowered = value.lower()
    for fragment in _FORBIDDEN_TEXT_FRAGMENTS:
        if fragment in lowered:
            raise BookStudyResponseError(f"{name} contains unsafe content")
