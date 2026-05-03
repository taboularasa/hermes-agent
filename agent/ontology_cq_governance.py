from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


_CONTRACT_RELATIVE_PATH = Path("docs/operations/query-ready-cq-governance.yaml")
_CQ_TOKEN_RE = re.compile(r"\bcqs?\b", re.IGNORECASE)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _resolve_repo_root(cwd: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if cwd is not None:
        candidates.append(Path(cwd).expanduser())
    candidates.append(_default_repo_root())

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            resolved = resolved.parent
        git_root = _find_git_root(resolved)
        if git_root and (git_root / _CONTRACT_RELATIVE_PATH).is_file():
            return git_root
        if (resolved / _CONTRACT_RELATIVE_PATH).is_file():
            return resolved

    return _default_repo_root()


@lru_cache(maxsize=8)
def _load_cached_contract(contract_path: str) -> dict[str, Any]:
    data = yaml.safe_load(Path(contract_path).read_text(encoding="utf-8")) or {}
    contract = data.get("query_ready_cq_governance")
    if not isinstance(contract, dict):
        raise ValueError(
            f"{contract_path} must define a top-level query_ready_cq_governance mapping."
        )
    return contract


def load_query_ready_cq_governance(cwd: str | Path | None = None) -> dict[str, Any]:
    repo_root = _resolve_repo_root(cwd)
    contract_path = repo_root / _CONTRACT_RELATIVE_PATH
    return deepcopy(_load_cached_contract(str(contract_path)))


def should_apply_query_ready_cq_governance(message: str | None) -> bool:
    if not isinstance(message, str):
        return False

    normalized = message.lower()
    if "smb-ontology-platform" in normalized:
        return True
    if "competency question" in normalized or "competency questions" in normalized:
        return True
    if "query-ready" in normalized:
        return True
    if "query_contract" in normalized or "template_id" in normalized:
        return True
    if "orsd" in normalized and "ontology" in normalized:
        return True
    return "ontology" in normalized and bool(_CQ_TOKEN_RE.search(message))


def build_query_ready_cq_governance_prompt(cwd: str | Path | None = None) -> str:
    try:
        contract = load_query_ready_cq_governance(cwd)
    except (FileNotFoundError, ValueError):
        return ""

    supported_templates = contract.get("supported_templates") or []
    template_ids = contract.get("supported_template_ids") or []
    minimum = contract.get("query_ready_minimum") or {}
    ontology_sources = contract.get("ontology_guidance_sources") or {}
    hermes_surface = contract.get("hermes_surface") or {}

    local_citation = str(hermes_surface.get("citation_path") or _CONTRACT_RELATIVE_PATH.as_posix())
    canonical_contract_path = str(
        (ontology_sources.get("canonical_contract") or {}).get("path") or ""
    )
    runbook_path = str((ontology_sources.get("canonical_runbook") or {}).get("path") or "")
    canonical_contract_branch = str(
        (ontology_sources.get("canonical_contract") or {}).get("branch") or ""
    )
    canonical_contract_commit = str(
        (ontology_sources.get("canonical_contract") or {}).get("commit") or ""
    )
    required_cq_fields = minimum.get("required_cq_fields") or []
    required_query_fields = minimum.get("required_query_contract_fields") or []
    required_forms = minimum.get("required_executable_forms") or []

    lines = [
        "## Query-ready CQ Governance",
        f"Cite `{local_citation}` for Hermes-side ontology CQ governance.",
        "free-form competency questions are incomplete until they are rewritten into one supported template family and carry a query-ready contract.",
        "Supported template families:",
    ]
    for spec in supported_templates:
        if not isinstance(spec, Mapping):
            continue
        template_id = str(spec.get("template_id") or "").strip()
        template_category = str(spec.get("template_category") or "").strip()
        query_family = str(spec.get("query_family") or "").strip()
        if not template_id:
            continue
        descriptor = f"  - {template_id}"
        if template_category:
            descriptor += f" (template_category={template_category}"
            if query_family:
                descriptor += f", query_family={query_family}"
            descriptor += ")"
        lines.append(descriptor)
    if not supported_templates and template_ids:
        lines.append(f"  - {', '.join(template_ids)}")
    if required_cq_fields:
        lines.append(f"Required CQ fields: {', '.join(required_cq_fields)}.")
    if required_query_fields:
        lines.append(
            "Required query_contract fields: "
            + ", ".join(required_query_fields)
            + "."
        )
    if required_forms:
        forms = " and ".join(f"`executable_forms.{form}`" for form in required_forms)
        lines.append(f"Executable forms must include {forms}.")
    if canonical_contract_path:
        source_line = (
            f"Ontology-side source of truth: `smb-ontology-platform/{canonical_contract_path}`"
        )
        if canonical_contract_branch and canonical_contract_commit:
            source_line += (
                f" on `{canonical_contract_branch}` at commit `{canonical_contract_commit}`"
            )
        lines.append(source_line + ".")
    if runbook_path:
        lines.append(
            f"Use `smb-ontology-platform/{runbook_path}` for operator/runbook detail when the mirrored Hermes contract is not enough."
        )
    return "\n".join(lines)


def evaluate_query_ready_cq_completion(
    candidate: Any,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    contract = load_query_ready_cq_governance(cwd)
    minimum = contract.get("query_ready_minimum") or {}
    supported_template_ids = list(contract.get("supported_template_ids") or [])
    supported_templates = {
        str(spec.get("template_id")): spec
        for spec in (contract.get("supported_templates") or [])
        if isinstance(spec, Mapping) and spec.get("template_id")
    }

    required_cq_fields = [str(field) for field in (minimum.get("required_cq_fields") or [])]
    required_query_fields = [
        str(field) for field in (minimum.get("required_query_contract_fields") or [])
    ]
    required_forms = [str(field) for field in (minimum.get("required_executable_forms") or [])]

    report = {
        "complete": False,
        "missing_fields": [],
        "errors": [],
        "supported_template_ids": supported_template_ids,
        "citation_path": str(
            (contract.get("hermes_surface") or {}).get("citation_path")
            or _CONTRACT_RELATIVE_PATH.as_posix()
        ),
    }

    def _add_missing(field_name: str) -> None:
        if field_name not in report["missing_fields"]:
            report["missing_fields"].append(field_name)

    def _add_error(message: str) -> None:
        if message not in report["errors"]:
            report["errors"].append(message)

    if not isinstance(candidate, Mapping):
        for field_name in required_cq_fields:
            _add_missing(field_name)
        _add_error(
            "free-form competency questions are incomplete until they map to a supported template family and query-ready contract."
        )
        return report

    question = str(candidate.get("question") or "").strip()
    for field_name in required_cq_fields:
        value = candidate.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            _add_missing(field_name)

    if question and report["missing_fields"]:
        _add_error(
            "free-form competency questions are incomplete until they map to a supported template family and query-ready contract."
        )

    template_id = str(candidate.get("template_id") or "").strip()
    template_category = str(candidate.get("template_category") or "").strip()
    if template_id and template_id not in supported_template_ids:
        _add_error(
            "template_id must reference a supported template family in docs/operations/query-ready-cq-governance.yaml."
        )

    template_spec = supported_templates.get(template_id)
    expected_category = ""
    expected_query_family = ""
    if isinstance(template_spec, Mapping):
        expected_category = str(template_spec.get("template_category") or "").strip()
        expected_query_family = str(template_spec.get("query_family") or "").strip()
    if expected_category and template_category and template_category != expected_category:
        _add_error("template_category must match the supported template definition.")

    query_contract = candidate.get("query_contract")
    if query_contract is None:
        report["complete"] = not report["missing_fields"] and not report["errors"]
        return report

    if not isinstance(query_contract, Mapping):
        _add_error("query_contract must be a mapping.")
        report["complete"] = not report["missing_fields"] and not report["errors"]
        return report

    for field_name in required_query_fields:
        value = query_contract.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            _add_missing(f"query_contract.{field_name}")

    if expected_query_family:
        query_family = str(query_contract.get("query_family") or "").strip()
        if query_family and query_family != expected_query_family:
            _add_error("query_contract.query_family must match the supported template definition.")

    executable_forms = query_contract.get("executable_forms")
    if executable_forms is None:
        for form_name in required_forms:
            _add_missing(f"query_contract.executable_forms.{form_name}")
        report["complete"] = not report["missing_fields"] and not report["errors"]
        return report

    if not isinstance(executable_forms, Mapping):
        _add_error("query_contract.executable_forms must be a mapping.")
        report["complete"] = not report["missing_fields"] and not report["errors"]
        return report

    for form_name in required_forms:
        value = executable_forms.get(form_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            _add_missing(f"query_contract.executable_forms.{form_name}")

    report["complete"] = not report["missing_fields"] and not report["errors"]
    return report
