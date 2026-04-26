"""Tests for Hermes book-study responses to De Novo Slack turns."""

import pytest

from hadto_patches.denovo_book_study import (
    BOOK_STUDY_CONTEXT,
    BOOK_STUDY_NO_CONTEXT,
    BOOK_STUDY_RESPONSE_SCHEMA,
    BookStudyMemoryReference,
    BookStudyResponse,
    BookStudyResponseError,
    build_book_study_response,
    build_no_context_book_study_response,
    fixture_book_study_response,
    redaction_proof,
    sha256_text,
)


def test_context_response_renders_source_linked_slack_text_and_proof():
    response = fixture_book_study_response()

    assert response.status == BOOK_STUDY_CONTEXT
    assert response.references
    slack_text = response.to_slack_text()
    proof = response.to_proof()

    assert "Hermes book-study context:" in slack_text
    assert "References for De Novo ingestion:" in slack_text
    assert "`hermes-memory-had547-book-study`" in slack_text
    assert "source=urn:denovo:source:minio:had547-book-study" in slack_text
    assert "locator=chapter:1#paragraph:3" in slack_text
    assert proof["schema"] == BOOK_STUDY_RESPONSE_SCHEMA
    assert proof["reference_count"] == 1
    assert proof["citation_locator_count"] == 1
    assert proof["raw_hermes_memory_stored"] is False
    assert proof["raw_chapter_text_stored"] is False
    assert proof["redaction"] == {
        "contains_secrets": False,
        "contains_public_url": False,
        "contains_response_url": False,
        "contains_raw_hermes_memory": False,
        "contains_raw_chapter_text": False,
        "contains_private_endpoint": False,
        "contains_cookie": False,
        "contains_html_script": False,
    }


def test_no_context_response_is_explicit_and_claims_no_references():
    response = build_no_context_book_study_response()

    assert response.status == BOOK_STUDY_NO_CONTEXT
    assert response.references == ()
    assert response.to_proof()["empty_context"] is True
    assert response.to_proof()["reference_count"] == 0
    assert "References: none" in response.to_slack_text()


def test_response_hash_is_deterministic_and_bound_to_content():
    response = fixture_book_study_response()
    copied = BookStudyResponse(
        status=response.status,
        public_summary=response.public_summary,
        references=response.references,
        response_sha256=response.response_sha256,
    )

    copied.validate()
    assert copied.response_sha256 == response.response_sha256

    tampered = BookStudyResponse(
        status=response.status,
        public_summary=response.public_summary + " changed",
        references=response.references,
        response_sha256=response.response_sha256,
    )
    with pytest.raises(BookStudyResponseError, match="response_sha256"):
        tampered.validate()


def test_rejects_context_without_reference_and_no_context_with_reference():
    reference = fixture_book_study_response().references[0]

    with pytest.raises(BookStudyResponseError, match="requires a reference"):
        build_book_study_response(
            public_summary="Hermes found context.",
            references=(),
            status=BOOK_STUDY_CONTEXT,
        )

    with pytest.raises(BookStudyResponseError, match="must not claim references"):
        build_book_study_response(
            public_summary="No context.",
            references=(reference,),
            status=BOOK_STUDY_NO_CONTEXT,
        )


@pytest.mark.parametrize(
    "edit,want",
    [
        (lambda ref: setattr(ref, "reference_id", "!!bad"), "reference_id"),
        (lambda ref: setattr(ref, "kind", "Bad Kind"), "kind"),
        (lambda ref: setattr(ref, "memory_summary_sha256", "abc"), "memory_summary_sha256"),
        (lambda ref: setattr(ref, "source_artifact_iri", "https://example.test/ref"), "source_artifact_iri"),
        (lambda ref: setattr(ref, "source_artifact_iri", "plain-id"), "artifact IRI"),
        (lambda ref: setattr(ref, "source_stable_id", "x"), "source_stable_id"),
        (lambda ref: setattr(ref, "source_sha256", "abc"), "source_sha256"),
        (lambda ref: setattr(ref, "citation_locator", "x"), "citation_locator"),
    ],
)
def test_rejects_malformed_references(edit, want):
    source = fixture_book_study_response().references[0]
    reference = _mutable_reference(source)
    edit(reference)

    with pytest.raises(BookStudyResponseError, match=want):
        reference.freeze().validate()


@pytest.mark.parametrize(
    "summary",
    [
        "raw Hermes memory: private recall",
        "raw chapter text should not appear",
        "see https://example.test/private",
        "response_url=https://hooks.slack.com/services/T/B/X",
        "secret xoxb-abcdefghijklmnopqrstuvwxyz",
        "cookie=session",
        "<script>alert(1)</script>",
        "localhost:8080",
        "minio://private-bucket/object",
    ],
)
def test_rejects_unsafe_slack_summary(summary):
    reference = fixture_book_study_response().references[0]
    with pytest.raises(BookStudyResponseError, match="unsafe content"):
        build_book_study_response(public_summary=summary, references=(reference,))


def test_redaction_proof_detects_unsafe_fragments_for_regression_coverage():
    proof = redaction_proof(
        {
            "token": "xoxb-abcdefghijklmnopqrstuvwxyz",
            "url": "https://example.test",
            "memory": "raw Hermes memory",
            "chapter": "chapter body: private",
            "endpoint": "localhost:8080",
            "cookie": "cookie=session",
            "html": "<script>alert(1)</script>",
        },
    )

    assert proof == {
        "contains_secrets": True,
        "contains_public_url": True,
        "contains_response_url": False,
        "contains_raw_hermes_memory": True,
        "contains_raw_chapter_text": True,
        "contains_private_endpoint": True,
        "contains_cookie": True,
        "contains_html_script": True,
    }


class _MutableReference:
    def __init__(self, source: BookStudyMemoryReference):
        self.reference_id = source.reference_id
        self.kind = source.kind
        self.memory_summary_sha256 = source.memory_summary_sha256
        self.source_artifact_iri = source.source_artifact_iri
        self.source_stable_id = source.source_stable_id
        self.source_sha256 = source.source_sha256
        self.citation_locator = source.citation_locator

    def freeze(self) -> BookStudyMemoryReference:
        return BookStudyMemoryReference(
            reference_id=self.reference_id,
            kind=self.kind,
            memory_summary_sha256=self.memory_summary_sha256,
            source_artifact_iri=self.source_artifact_iri,
            source_stable_id=self.source_stable_id,
            source_sha256=self.source_sha256,
            citation_locator=self.citation_locator,
        )


def _mutable_reference(source: BookStudyMemoryReference) -> _MutableReference:
    assert source.memory_summary_sha256 == sha256_text(
        "Hermes prior book study routine context with source links",
    )
    return _MutableReference(source)
