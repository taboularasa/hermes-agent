"""Tests for generic Hermes source-linked memory references."""

import pytest

from hadto_patches.denovo_source_reference import (
    INFORMAL_CONTEXT,
    NEEDS_SOURCE_RETRIEVAL,
    ONTOLOGY_COMMIT_READY,
    SourceLinkedHermesReference,
    SourceReferenceExport,
    SourceReferenceError,
    build_reference_export,
    fixture_reference_export,
    fixture_references,
    redaction_proof,
    sha256_text,
    validate_references,
)


def test_fixture_references_cover_source_kinds_and_are_commit_ready():
    refs = fixture_references()

    assert len(refs) == 3
    assert {ref.source_artifact_iri.split(":")[3] for ref in refs} == {
        "url",
        "minio",
        "git",
    }
    for ref in refs:
        ref.validate()
        assert ref.ontology_readiness == ONTOLOGY_COMMIT_READY
        assert ref.to_dict()["ontologyReadiness"] == ONTOLOGY_COMMIT_READY
        assert "locator=" in ref.to_public_line()
        assert "source_sha256=" in ref.to_public_line()


def test_transcript_and_book_study_reference_shapes_are_supported():
    refs = {ref.kind: ref for ref in fixture_references()}

    transcript = refs["interview-transcript"]
    book_study = refs["book-study-memory"]

    assert transcript.source_artifact_iri.startswith("urn:denovo:source:minio:")
    assert transcript.citation_locator.startswith("timestamp:")
    assert book_study.source_artifact_iri.startswith("urn:denovo:source:git:")
    assert book_study.citation_locator.startswith("paragraph:")


def test_references_without_source_are_not_commit_ready():
    pending = SourceLinkedHermesReference(
        reference_id="hermes-memory-had550-pending",
        kind="ontology-memory",
        memory_summary_sha256=sha256_text("Hermes has a summary whose source can be retrieved."),
        source_retrieval_hint="minio:hermes-memory-exports/had550-pending",
    )
    informal = SourceLinkedHermesReference(
        reference_id="hermes-memory-had550-informal",
        kind="ontology-memory",
        memory_summary_sha256=sha256_text("Hermes has context without source evidence."),
    )

    assert pending.ontology_readiness == NEEDS_SOURCE_RETRIEVAL
    assert pending.to_dict()["ontologyReadiness"] == NEEDS_SOURCE_RETRIEVAL
    assert "retrieval_hint=" in pending.to_public_line()
    assert informal.ontology_readiness == INFORMAL_CONTEXT
    assert informal.to_dict()["ontologyReadiness"] == INFORMAL_CONTEXT


@pytest.mark.parametrize(
    "edit,want",
    [
        (lambda ref: setattr(ref, "reference_id", "x"), "reference_id"),
        (lambda ref: setattr(ref, "kind", "Bad Kind"), "kind"),
        (lambda ref: setattr(ref, "memory_summary_sha256", "abc"), "memory_summary_sha256"),
        (lambda ref: setattr(ref, "source_artifact_iri", "https://example.test/source"), "source_artifact_iri"),
        (lambda ref: setattr(ref, "source_artifact_iri", "urn:denovo:source:s3:bad"), "source_artifact_iri"),
        (lambda ref: setattr(ref, "source_stable_id", "x"), "source_stable_id"),
        (lambda ref: setattr(ref, "source_sha256", "abc"), "source_sha256"),
        (lambda ref: setattr(ref, "citation_locator", "free text locator"), "citation_locator"),
        (lambda ref: setattr(ref, "citation_locator", "section:https://example.test"), "citation_locator"),
        (lambda ref: setattr(ref, "source_retrieval_hint", "x"), "source_retrieval_hint"),
    ],
)
def test_rejects_malformed_source_references(edit, want):
    mutable = _mutable_reference(fixture_references()[0])
    edit(mutable)

    with pytest.raises(SourceReferenceError, match=want):
        mutable.freeze().validate()


def test_rejects_partial_source_coordinates():
    ref = SourceLinkedHermesReference(
        reference_id="hermes-memory-had550-partial",
        kind="ontology-memory",
        memory_summary_sha256=sha256_text("partial source"),
        source_artifact_iri="urn:denovo:source:url:had550-partial",
    )

    with pytest.raises(SourceReferenceError, match="complete"):
        ref.validate()


@pytest.mark.parametrize(
    "value",
    [
        "raw Hermes memory dump",
        "raw transcript body: private",
        "raw chapter text",
        "https://example.test?X-Amz-Signature=bad",
        "secret xoxb-abcdefghijklmnopqrstuvwxyz",
        "Bearer private-token",
        "cookie=session",
        "<script>alert(1)</script>",
        "localhost:8080",
        "minio://private-bucket/key",
    ],
)
def test_rejects_unsafe_reference_material(value):
    ref = SourceLinkedHermesReference(
        reference_id="hermes-memory-had550-unsafe",
        kind="ontology-memory",
        memory_summary_sha256=sha256_text("unsafe"),
        source_retrieval_hint=value,
    )

    with pytest.raises(SourceReferenceError, match="unsafe"):
        ref.validate()


def test_validate_references_returns_tuple_and_redaction_proof_is_clean():
    refs = validate_references(fixture_references())
    proof = redaction_proof([ref.to_dict() for ref in refs])

    assert isinstance(refs, tuple)
    assert proof == {
        "contains_secrets": False,
        "contains_public_url": False,
        "contains_response_url": False,
        "contains_raw_hermes_memory": False,
        "contains_raw_transcript": False,
        "contains_raw_chapter_text": False,
        "contains_private_endpoint": False,
        "contains_cookie": False,
        "contains_html_script": False,
        "contains_presigned_url": False,
    }


def test_reference_export_classifies_ready_pending_and_informal_refs():
    export = fixture_reference_export()
    proof = export.to_proof()

    assert export.response_id == "had550-hermes-reference-export"
    assert proof["reference_count"] == 3
    assert proof["readiness_counts"] == {
        ONTOLOGY_COMMIT_READY: 1,
        NEEDS_SOURCE_RETRIEVAL: 1,
        INFORMAL_CONTEXT: 1,
    }
    assert proof["citation_locator_count"] == 1
    assert proof["source_ready_reference_ids"] == ["hermes-memory-had550-url-source"]
    assert proof["pending_reference_ids"] == ["hermes-memory-had550-needs-source"]
    assert proof["informal_reference_ids"] == ["hermes-memory-had550-informal-context"]
    assert proof["redaction"] == {
        "contains_secrets": False,
        "contains_public_url": False,
        "contains_response_url": False,
        "contains_raw_hermes_memory": False,
        "contains_raw_transcript": False,
        "contains_raw_chapter_text": False,
        "contains_private_endpoint": False,
        "contains_cookie": False,
        "contains_html_script": False,
        "contains_presigned_url": False,
    }


def test_reference_export_hash_is_deterministic_and_bound_to_content():
    export = fixture_reference_export()
    copied = SourceReferenceExport(
        response_id=export.response_id,
        references=export.references,
        bundle_sha256=export.bundle_sha256,
    )
    copied.validate()

    tampered_refs = (
        SourceLinkedHermesReference(
            reference_id="hermes-memory-had550-url-source-changed",
            kind=export.references[0].kind,
            memory_summary_sha256=export.references[0].memory_summary_sha256,
            source_artifact_iri=export.references[0].source_artifact_iri,
            source_stable_id=export.references[0].source_stable_id,
            source_sha256=export.references[0].source_sha256,
            citation_locator=export.references[0].citation_locator,
        ),
        *export.references[1:],
    )
    tampered = SourceReferenceExport(
        response_id=export.response_id,
        references=tampered_refs,
        bundle_sha256=export.bundle_sha256,
    )

    with pytest.raises(SourceReferenceError, match="bundle_sha256"):
        tampered.validate()


def test_reference_export_rejects_empty_or_unsafe_bundles():
    with pytest.raises(SourceReferenceError, match="references"):
        build_reference_export(response_id="had550-empty-export", references=())

    unsafe = SourceLinkedHermesReference(
        reference_id="hermes-memory-had550-unsafe-export",
        kind="ontology-memory",
        memory_summary_sha256=sha256_text("unsafe export"),
        source_retrieval_hint="https://example.test?X-Amz-Signature=bad",
    )
    with pytest.raises(SourceReferenceError, match="unsafe"):
        build_reference_export(
            response_id="had550-unsafe-export",
            references=(unsafe,),
        )


def test_redaction_proof_detects_unsafe_material():
    proof = redaction_proof(
        {
            "token": "xoxb-abcdefghijklmnopqrstuvwxyz",
            "url": "https://example.test?X-Amz-Signature=bad",
            "memory": "raw Hermes memory",
            "transcript": "raw transcript",
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
        "contains_raw_transcript": True,
        "contains_raw_chapter_text": True,
        "contains_private_endpoint": True,
        "contains_cookie": True,
        "contains_html_script": True,
        "contains_presigned_url": True,
    }


class _MutableReference:
    def __init__(self, source: SourceLinkedHermesReference):
        self.reference_id = source.reference_id
        self.kind = source.kind
        self.memory_summary_sha256 = source.memory_summary_sha256
        self.source_artifact_iri = source.source_artifact_iri
        self.source_stable_id = source.source_stable_id
        self.source_sha256 = source.source_sha256
        self.citation_locator = source.citation_locator
        self.source_retrieval_hint = source.source_retrieval_hint

    def freeze(self) -> SourceLinkedHermesReference:
        return SourceLinkedHermesReference(
            reference_id=self.reference_id,
            kind=self.kind,
            memory_summary_sha256=self.memory_summary_sha256,
            source_artifact_iri=self.source_artifact_iri,
            source_stable_id=self.source_stable_id,
            source_sha256=self.source_sha256,
            citation_locator=self.citation_locator,
            source_retrieval_hint=self.source_retrieval_hint,
        )


def _mutable_reference(source: SourceLinkedHermesReference) -> _MutableReference:
    return _MutableReference(source)
