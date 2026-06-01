from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    REPO_ROOT
    / "notes"
    / "data-migration"
    / "data-hermes-backup-verifier-manifest.md"
)
SCRIPT = REPO_ROOT / "scripts" / "data_migration_backup_verifier.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_data_migration_backup_verifier_under_test",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_manifest() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def _section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing section: {heading}"
    return match.group("body")


def test_static_verifier_accepts_repo_manifest() -> None:
    module = _load_module()

    assert module.validate_manifest(MANIFEST) == []


def test_manifest_covers_required_classes_and_columns() -> None:
    manifest = _section(_read_manifest(), "Verifier manifest")

    for column in (
        "Paths/patterns",
        "Readback proof",
        "Expected cadence",
        "Owner",
        "Failure action",
        "Migration/holdback relevance",
    ):
        assert column in manifest

    for class_name in (
        "critical-durable",
        "important-durable",
        "operational-history",
        "retention",
        "exclusion",
        "holdback-gate",
    ):
        assert f"`{class_name}`" in manifest


def test_manifest_includes_safe_read_only_pseudocommands_only() -> None:
    command_set = _section(_read_manifest(), "Read-only command set")

    assert "pseudocommands" in command_set
    assert "--read-only" in command_set
    assert "--dry-run" in command_set
    for forbidden in (
        "sudo ",
        "rsync ",
        "mv ",
        "rm ",
        "mount ",
        "umount ",
        "chmod ",
        "chown ",
        "docker stop",
        "systemctl stop",
    ):
        assert forbidden not in command_set


def test_verifier_reports_missing_required_class(tmp_path: Path) -> None:
    module = _load_module()
    broken_manifest = tmp_path / "manifest.md"
    text = _read_manifest()
    text = re.sub(r"^\| `holdback-gate` .*?\n", "", text, flags=re.MULTILINE)
    broken_manifest.write_text(text, encoding="utf-8")

    errors = module.validate_manifest(broken_manifest)

    assert "missing manifest class: holdback-gate" in errors


def test_verifier_reports_mutable_command_in_command_set(tmp_path: Path) -> None:
    module = _load_module()
    broken_manifest = tmp_path / "manifest.md"
    text = _read_manifest().replace(
        "backup-client snapshots --read-only --path /data/hermes",
        "sudo mount /dev/example /data",
    )
    broken_manifest.write_text(text, encoding="utf-8")

    errors = module.validate_manifest(broken_manifest)

    assert any("forbidden mutable command" in error for error in errors)
