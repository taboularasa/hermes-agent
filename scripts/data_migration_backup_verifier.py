from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "notes"
    / "data-migration"
    / "data-hermes-backup-verifier-manifest.md"
)

REQUIRED_SECTIONS = (
    "Scope and guardrails",
    "Verifier manifest",
    "Readback report template",
    "Read-only command set",
    "Static repo verification",
)

REQUIRED_COLUMNS = (
    "Class",
    "Paths/patterns",
    "Readback proof",
    "Expected cadence",
    "Owner",
    "Failure action",
    "Migration/holdback relevance",
)

REQUIRED_CLASSES: dict[str, tuple[str, ...]] = {
    "critical-durable": (
        "/data/hermes/profile-default",
        "/data/hermes/stacks",
        "/data/hermes/ctx-data",
        "/data/hermes/codex-home",
        "SQLite integrity check",
        "Nightly",
        "keep holdbacks",
    ),
    "important-durable": (
        "Provenance",
        "source materials",
        "/data/hermes/stacks",
        "Weekly",
        "manager signoff",
    ),
    "operational-history": (
        "/data/hermes/.migration-evidence",
        "backup verification reports",
        "Weekly",
        "audit trail",
    ),
    "retention": (
        "newest successful snapshot",
        "oldest retained snapshot",
        "Weekly policy check",
        "pause holdback cleanup",
    ),
    "exclusion": (
        "Excluded caches",
        "virtualenvs",
        "holdback directories",
        "rerun readback",
    ),
    "holdback-gate": (
        "manager acceptance",
        "SQLite/session checks",
        "ctx and Codex metadata checks",
        "Holdbacks stay in place",
    ),
}

REQUIRED_REPORT_HEADINGS = (
    "Snapshot identity",
    "critical-durable",
    "important-durable",
    "operational-history",
    "retention",
    "exclusion",
    "holdback-gate",
    "Failures and follow-ups",
    "Manager acceptance",
)

GUARDRAIL_PHRASES = (
    "planning-only",
    "verification-only",
    "does not execute backups",
    "does not execute",
    "read-only pseudocommands",
)

FORBIDDEN_COMMAND_PREFIXES = (
    "sudo ",
    "rsync ",
    "mv ",
    "rm ",
    "mount ",
    "umount ",
    "cp ",
    "chmod ",
    "chown ",
    "docker stop",
    "docker start",
    "systemctl stop",
    "systemctl start",
)


def _section(markdown: str, heading: str) -> str | None:
    match = re.search(
        rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group("body")


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _split_table_row(stripped)
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _plain_cell(cell: str) -> str:
    return cell.replace("`", "").strip()


def _fenced_code_blocks(markdown: str) -> list[str]:
    return re.findall(r"^```[^\n]*\n(.*?)^```", markdown, flags=re.MULTILINE | re.DOTALL)


def _normalized_whitespace(text: str) -> str:
    return " ".join(text.split())


def validate_manifest(path: Path = DEFAULT_MANIFEST) -> list[str]:
    markdown = path.read_text(encoding="utf-8")
    errors: list[str] = []

    for heading in REQUIRED_SECTIONS:
        if _section(markdown, heading) is None:
            errors.append(f"missing section: {heading}")

    guardrails = _normalized_whitespace(_section(markdown, "Scope and guardrails") or "")
    for phrase in GUARDRAIL_PHRASES:
        if phrase not in guardrails:
            errors.append(f"missing guardrail phrase: {phrase}")

    manifest = _section(markdown, "Verifier manifest")
    if manifest is None:
        return errors

    rows = _table_rows(manifest)
    if not rows:
        errors.append("missing verifier manifest table")
        return errors

    header = rows[0]
    for column in REQUIRED_COLUMNS:
        if column not in header:
            errors.append(f"missing manifest column: {column}")

    rows_by_class: dict[str, list[str]] = {}
    for row in rows[1:]:
        if not row:
            continue
        rows_by_class[_plain_cell(row[0])] = row

    for class_name, required_tokens in REQUIRED_CLASSES.items():
        row = rows_by_class.get(class_name)
        if row is None:
            errors.append(f"missing manifest class: {class_name}")
            continue
        if len(row) < len(REQUIRED_COLUMNS):
            errors.append(f"manifest class {class_name} has too few columns")
            continue
        for index, column in enumerate(REQUIRED_COLUMNS[1:], start=1):
            value = row[index].strip()
            if not value or value.lower() in {"tbd", "todo", "n/a"}:
                errors.append(f"manifest class {class_name} has empty {column}")
        row_text = " ".join(row)
        for token in required_tokens:
            if token not in row_text:
                errors.append(f"manifest class {class_name} missing token: {token}")

    report_template = _section(markdown, "Readback report template") or ""
    for heading in REQUIRED_REPORT_HEADINGS:
        if heading not in report_template:
            errors.append(f"missing readback report heading: {heading}")

    command_set = _section(markdown, "Read-only command set") or ""
    if "pseudocommands" not in command_set:
        errors.append("read-only command set must label commands as pseudocommands")
    if "--read-only" not in command_set:
        errors.append("read-only command set must include --read-only examples")
    if "--dry-run" not in command_set:
        errors.append("read-only command set must include --dry-run examples")
    for block in _fenced_code_blocks(command_set):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for prefix in FORBIDDEN_COMMAND_PREFIXES:
                if stripped.startswith(prefix):
                    errors.append(f"forbidden mutable command in command set: {stripped}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the static /data/hermes backup verifier manifest.",
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to the markdown manifest to validate.",
    )
    args = parser.parse_args(argv)

    errors = validate_manifest(args.manifest)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"OK: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
