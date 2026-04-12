"""Read Hadto ontology artifacts into compact Hermes reasoning context."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.ontology_context import (
    DEFAULT_ONTOLOGY_REPO_ROOT,
    build_consulting_context,
    build_ontology_engineering_context,
    build_vertical_readiness_context,
    build_self_improvement_context,
    build_source_material_context,
    build_sales_context,
    build_vertical_detail,
    load_ontology_snapshot,
)
from tools.registry import registry


ONTOLOGY_CONTEXT_SCHEMA = {
    "name": "ontology_context",
    "description": (
        "Read Hadto ontology platform artifacts and return compact reasoning context for "
        "snapshot, ontology-engineering, self-improvement, consulting, sales, source-material, or vertical detail use cases. "
        "Use this instead of manually grepping ontology files or shelling into Python to reconstruct the same context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "snapshot",
                    "ontology_engineering",
                    "self_improvement",
                    "vertical_readiness",
                    "consulting_context",
                    "sales_context",
                    "source_materials",
                    "vertical_detail",
                ],
                "description": "Which ontology context pack to build. Use ontology_engineering for textbook-driven ontology upgrade work.",
            },
            "query": {
                "type": "string",
                "description": "Client brief, prospect brief, or reasoning prompt for consulting/sales context.",
            },
            "vertical": {
                "type": "string",
                "description": "Optional explicit ontology vertical such as dental or home_services.",
            },
            "ontology_root": {
                "type": "string",
                "description": "Override the ontology repo root (defaults to /home/david/stacks/smb-ontology-platform).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matches/manifests to return where applicable.",
                "minimum": 1,
            },
            "now": {
                "type": "string",
                "description": "Optional ISO timestamp override for self_improvement context tests.",
            },
            "freshness_hours": {
                "type": "integer",
                "description": "Freshness threshold for self_improvement context. Defaults to 72h.",
                "minimum": 1,
            },
        },
        "required": ["action"],
    },
}


def check_ontology_context_requirements() -> bool:
    return Path(DEFAULT_ONTOLOGY_REPO_ROOT).exists()


def _parse_now(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def ontology_context(args: dict[str, Any], **_kw) -> str:
    action = str(args.get("action") or "").strip().lower()
    repo_root = Path(str(args.get("ontology_root") or DEFAULT_ONTOLOGY_REPO_ROOT)).expanduser()
    query = str(args.get("query") or "").strip()
    vertical = str(args.get("vertical") or "").strip() or None
    limit = max(1, int(args.get("limit") or 3))
    freshness_hours = max(1, int(args.get("freshness_hours") or 72))
    now = _parse_now(args.get("now"))

    if action == "snapshot":
        context = load_ontology_snapshot(repo_root)
    elif action == "ontology_engineering":
        context = build_ontology_engineering_context(repo_root, limit=limit)
    elif action == "self_improvement":
        context = build_self_improvement_context(repo_root, now=now, freshness_hours=freshness_hours)
    elif action == "vertical_readiness":
        context = build_vertical_readiness_context(repo_root, vertical=vertical, limit=limit)
    elif action == "consulting_context":
        if not query and not vertical:
            return json.dumps({"error": "query or vertical is required for consulting_context"}, ensure_ascii=False)
        context = build_consulting_context(query=query or (vertical or ""), repo_root=repo_root, vertical=vertical, limit=limit)
    elif action == "sales_context":
        if not query and not vertical:
            return json.dumps({"error": "query or vertical is required for sales_context"}, ensure_ascii=False)
        context = build_sales_context(query=query or (vertical or ""), repo_root=repo_root, vertical=vertical, limit=limit)
    elif action == "source_materials":
        context = build_source_material_context(repo_root, limit=limit)
    elif action == "vertical_detail":
        if not vertical:
            return json.dumps({"error": "vertical is required for vertical_detail"}, ensure_ascii=False)
        try:
            context = build_vertical_detail(repo_root=repo_root, vertical=vertical)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    else:
        return json.dumps({"error": f"Unsupported action: {action}"}, ensure_ascii=False)

    return json.dumps({"success": True, "action": action, "context": context}, ensure_ascii=False)


registry.register(
    name="ontology_context",
    toolset="ontology",
    schema=ONTOLOGY_CONTEXT_SCHEMA,
    handler=ontology_context,
    check_fn=check_ontology_context_requirements,
    emoji="🧠",
)
