#!/usr/bin/env python3
"""Upsert study-retro self-improvement issues into Linear."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from tools.linear_issue_tool import linear_issue


DEFAULT_SPEC_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "self-improvement-study-retro-2026-04-15.yaml"
)


def _load_spec(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping in {path}")
    return payload


def _resolve_delegate_id(delegate_name: str) -> str:
    response = json.loads(linear_issue({"action": "list_users"}))
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    for user in response.get("users", []):
        if not isinstance(user, dict):
            continue
        if str(user.get("name") or "").strip().casefold() == delegate_name.casefold():
            return str(user.get("id") or "")
        if str(user.get("displayName") or "").strip().casefold() == delegate_name.casefold():
            return str(user.get("id") or "")
    raise RuntimeError(f"Could not resolve Linear delegate {delegate_name!r}")


def _format_description(issue: dict[str, Any]) -> str:
    lines = [
        f"Lane: {issue['lane']}",
        "",
        "Capability gap:",
        str(issue["capability_gap"]).strip(),
        "",
        "Why it matters now:",
        str(issue["why_now"]).strip(),
        "",
        "Evidence:",
    ]
    lines.extend(f"- {item}" for item in issue.get("evidence", []))
    lines.extend(
        [
            "",
            "Target repo or surface:",
        ]
    )
    lines.extend(f"- {item}" for item in issue.get("target_surface", []))
    lines.extend(
        [
            "",
            "Verification expectation:",
        ]
    )
    lines.extend(f"- {item}" for item in issue.get("verification_expectation", []))
    lines.extend(
        [
            "",
            "Expected effect:",
            str(issue["expected_effect"]).strip(),
        ]
    )
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        default=str(DEFAULT_SPEC_PATH),
        help="Path to the YAML issue spec.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Linear payloads instead of writing them.",
    )
    args = parser.parse_args()

    spec_path = Path(args.spec).expanduser().resolve()
    spec = _load_spec(spec_path)
    delegate_name = str(spec.get("delegate_name") or "Hermes")
    delegate_id = _resolve_delegate_id(delegate_name)
    project_name = str(spec.get("project_name") or "").strip()
    team_key = str(spec.get("team_key") or "").strip()

    results: list[dict[str, Any]] = []
    for issue in spec.get("issues", []):
        if not isinstance(issue, dict):
            continue
        payload = {
            "action": "issue_upsert",
            "project_name": project_name,
            "team_key": team_key,
            "delegate_id": delegate_id,
            "assignee_id": "",
            "title": str(issue["title"]).strip(),
            "description": _format_description(issue),
            "priority": int(issue["priority"]),
            "dedupe_key": str(issue["dedupe_key"]).strip(),
        }
        if args.dry_run:
            results.append(payload)
            continue
        try:
            result = json.loads(linear_issue(payload))
        except Exception as exc:
            results.append(
                {
                    "title": payload["title"],
                    "dedupe_key": payload["dedupe_key"],
                    "success": False,
                    "created": False,
                    "updated_existing": False,
                    "identifier": None,
                    "url": None,
                    "error": str(exc),
                }
            )
            continue
        results.append(
            {
                "title": payload["title"],
                "dedupe_key": payload["dedupe_key"],
                "success": result.get("success"),
                "created": result.get("created", False),
                "updated_existing": result.get("updated_existing", False),
                "identifier": (result.get("issue") or {}).get("identifier"),
                "url": (result.get("issue") or {}).get("url"),
                "error": result.get("error"),
            }
        )

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
