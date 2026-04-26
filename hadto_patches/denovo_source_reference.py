"""Source-linked Hermes memory references for De Novo ontology work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


ONTOLOGY_COMMIT_READY = "ontology_commit_ready"
NEEDS_SOURCE_RETRIEVAL = "needs_source_retrieval"
INFORMAL_CONTEXT = "informal_context"

SOURCE_REFERENCE_SCHEMA = "hermes.denovo.source_reference.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9:._/-]{2,160}$")
_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{2,160}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9._/-]{2,80}$")
_SOURCE_IRI_RE = re.compile(r"^urn:denovo:source:(url|minio|git):[a-z0-9][a-z0-9._:-]{2,160}$")
_LOCATOR_RE = re.compile(
    r"^(line|lines|page|pages|section|paragraph|timestamp|xpath|quote):"
    r"[A-Za-z0-9._:/#=, -]{1,160}$",
)

_FORBIDDEN_FRAGMENTS = (
    "raw hermes memory",
    "raw memory dump",
    "raw transcript",
    "transcript body:",
    "raw chapter text",
    "chapter body:",
    "http://",
    "https://",
    "x-amz-signature",
    "x-amz-credential",
    "presigned",
    "response_url",
    "hooks.slack.com",
    "xoxb-",
    "xoxa-",
    "xapp-",
    "bearer ",
    "api_key",
    "access_token",
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


class SourceReferenceError(ValueError):
    """Raised when a Hermes reference is malformed or unsafe."""


@dataclass(frozen=True)
class SourceLinkedHermesReference:
    """A Hermes memory reference with optional source citation coordinates."""

    reference_id: str
    kind: str
    memory_summary_sha256: str
    source_artifact_iri: str = ""
    source_stable_id: str = ""
    source_sha256: str = ""
    citation_locator: str = ""
    source_retrieval_hint: str = ""

    @property
    def has_complete_source(self) -> bool:
        return all(
            (
                self.source_artifact_iri,
                self.source_stable_id,
                self.source_sha256,
                self.citation_locator,
            ),
        )

    @property
    def ontology_readiness(self) -> str:
        if self.has_complete_source:
            return ONTOLOGY_COMMIT_READY
        if self.source_retrieval_hint:
            return NEEDS_SOURCE_RETRIEVAL
        return INFORMAL_CONTEXT

    def validate(self) -> None:
        _match("reference_id", self.reference_id, _REFERENCE_ID_RE)
        _match("kind", self.kind, _KIND_RE)
        _match("memory_summary_sha256", self.memory_summary_sha256, _SHA256_RE)
        _validate_safe_text("reference_id", self.reference_id)
        _validate_safe_text("kind", self.kind)
        fields = (
            self.source_artifact_iri,
            self.source_stable_id,
            self.source_sha256,
            self.citation_locator,
        )
        populated = [bool(value) for value in fields]
        if any(populated) and not all(populated):
            raise SourceReferenceError("source citation must be complete when present")
        if self.has_complete_source:
            _match("source_artifact_iri", self.source_artifact_iri, _SOURCE_IRI_RE)
            _match("source_stable_id", self.source_stable_id, _STABLE_ID_RE)
            _match("source_sha256", self.source_sha256, _SHA256_RE)
            _match("citation_locator", self.citation_locator, _LOCATOR_RE)
            if "://" in self.citation_locator:
                raise SourceReferenceError("citation_locator must not embed URLs")
            _validate_safe_text(
                "source citation",
                " ".join(
                    (
                        self.source_artifact_iri,
                        self.source_stable_id,
                        self.source_sha256,
                        self.citation_locator,
                    ),
                ),
            )
        if self.source_retrieval_hint:
            _validate_safe_text("source_retrieval_hint", self.source_retrieval_hint)
            _match("source_retrieval_hint", self.source_retrieval_hint, _REFERENCE_ID_RE)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "id": self.reference_id,
            "kind": self.kind,
            "memorySummarySha256": self.memory_summary_sha256,
            "sourceArtifactIri": self.source_artifact_iri,
            "sourceStableId": self.source_stable_id,
            "sourceSha256": self.source_sha256,
            "citationLocator": self.citation_locator,
            "sourceRetrievalHint": self.source_retrieval_hint,
            "ontologyReadiness": self.ontology_readiness,
        }

    def to_public_line(self) -> str:
        self.validate()
        if self.ontology_readiness == ONTOLOGY_COMMIT_READY:
            return (
                f"- `{self.reference_id}` ({self.kind}) readiness={self.ontology_readiness} "
                f"summary_sha256={self.memory_summary_sha256} "
                f"source={self.source_artifact_iri} stable_id={self.source_stable_id} "
                f"source_sha256={self.source_sha256} locator={self.citation_locator}"
            )
        if self.ontology_readiness == NEEDS_SOURCE_RETRIEVAL:
            return (
                f"- `{self.reference_id}` ({self.kind}) readiness={self.ontology_readiness} "
                f"summary_sha256={self.memory_summary_sha256} retrieval_hint={self.source_retrieval_hint}"
            )
        return (
            f"- `{self.reference_id}` ({self.kind}) readiness={self.ontology_readiness} "
            f"summary_sha256={self.memory_summary_sha256}"
        )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_references(references: Iterable[SourceLinkedHermesReference]) -> tuple[SourceLinkedHermesReference, ...]:
    refs = tuple(references)
    for reference in refs:
        reference.validate()
    return refs


def redaction_proof(*values: Any) -> dict[str, bool]:
    text = json.dumps(values, sort_keys=True, default=str).lower()
    return {
        "contains_secrets": any(
            fragment in text
            for fragment in ("xox", "bearer ", "api_key", "access_token")
        ),
        "contains_public_url": "https://" in text or "http://" in text,
        "contains_response_url": "response_url" in text or "hooks.slack.com" in text,
        "contains_raw_hermes_memory": "raw hermes memory" in text or "raw memory dump" in text,
        "contains_raw_transcript": "raw transcript" in text or "transcript body:" in text,
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
        "contains_presigned_url": "x-amz-signature" in text or "x-amz-credential" in text,
    }


def fixture_references() -> tuple[SourceLinkedHermesReference, ...]:
    """Return deterministic references covering source kinds and readiness."""
    return (
        SourceLinkedHermesReference(
            reference_id="hermes-memory-had550-url-source",
            kind="ontology-memory",
            memory_summary_sha256=sha256_text("Hermes summarized an ontology source from a website."),
            source_artifact_iri="urn:denovo:source:url:had550-ontology-citation",
            source_stable_id="url:had550-ontology-citation",
            source_sha256=sha256_text("had550 ontology citation source"),
            citation_locator="section:ontology-citation",
        ),
        SourceLinkedHermesReference(
            reference_id="hermes-memory-had550-minio-transcript",
            kind="interview-transcript",
            memory_summary_sha256=sha256_text("Hermes summarized an interview transcript source."),
            source_artifact_iri="urn:denovo:source:minio:had550-transcript",
            source_stable_id="minio:had550-transcript",
            source_sha256=sha256_text("had550 transcript source"),
            citation_locator="timestamp:00:03:14",
        ),
        SourceLinkedHermesReference(
            reference_id="hermes-memory-had550-git-book-study",
            kind="book-study-memory",
            memory_summary_sha256=sha256_text("Hermes summarized a book-study artifact from git."),
            source_artifact_iri="urn:denovo:source:git:had550-book-study",
            source_stable_id="git:had550-book-study",
            source_sha256=sha256_text("had550 book study source"),
            citation_locator="paragraph:3",
        ),
    )


def _match(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise SourceReferenceError(f"{name} is malformed")


def _validate_safe_text(name: str, value: str) -> None:
    lowered = str(value).lower()
    for fragment in _FORBIDDEN_FRAGMENTS:
        if fragment in lowered:
            raise SourceReferenceError(f"{name} contains unsafe content")
