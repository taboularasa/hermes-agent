"""Typed cron policy for Hadto workspace coordinator ownership behavior.

The workspace backlog coordinator can select work without owning it. Ownership is
controlled here so cron prompt wording cannot accidentally turn delegation on or
bypass ignored-project deny rules.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import json
from typing import Any, Mapping, Optional


DEFAULT_IGNORED_PROJECTS: tuple[str, ...] = (
    "De Novo",
    "Symphony",
    "Hermes Agent Upstream",
    "dojoMOO",
)

_DELEGATION_TOOLS = {"delegate_task", "codex_delegate"}
_POLICY_VAR: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "HERMES_WORKSPACE_COORDINATOR_POLICY",
    default=None,
)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []

    normalized: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def normalize_workspace_coordinator_config(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Return safe typed coordinator policy with code-owned deny defaults."""
    source = dict(raw or {})
    ignored = list(DEFAULT_IGNORED_PROJECTS)
    for project_name in _string_list(source.get("ignored_projects")):
        if project_name not in ignored:
            ignored.append(project_name)

    config_path = source.get("config_path")
    if config_path is not None:
        config_path = str(config_path).strip() or None

    return {
        "auto_delegate": _bool(source.get("auto_delegate"), default=False),
        "ignored_projects": ignored,
        "config_path": config_path,
    }


def activate_workspace_coordinator_policy(raw: Optional[Mapping[str, Any]]) -> Token:
    """Set the current cron coordinator policy and return a reset token."""
    return _POLICY_VAR.set(normalize_workspace_coordinator_config(raw))


def clear_workspace_coordinator_policy(token: Token) -> None:
    _POLICY_VAR.reset(token)


def current_workspace_coordinator_policy() -> Optional[dict[str, Any]]:
    policy = _POLICY_VAR.get()
    return dict(policy) if policy is not None else None


def _mentions_ignored_project(args: Mapping[str, Any], ignored_projects: list[str]) -> Optional[str]:
    try:
        haystack = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except TypeError:
        haystack = str(args)
    haystack_lower = haystack.lower()
    for project_name in ignored_projects:
        if project_name.lower() in haystack_lower:
            return project_name
    return None


def apply_workspace_coordinator_tool_policy(
    function_name: str,
    function_args: Mapping[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """Apply the active typed coordinator policy to a tool call.

    Returns ``(args, block_message)``. ``block_message`` is set when the call
    must not execute.
    """
    args = dict(function_args or {})
    policy = current_workspace_coordinator_policy()
    if policy is None:
        return args, None

    ignored_projects = list(policy.get("ignored_projects") or DEFAULT_IGNORED_PROJECTS)

    if function_name == "workspace_backlog_orchestrator":
        args["auto_delegate"] = bool(policy.get("auto_delegate", False))
        config_path = policy.get("config_path")
        if config_path:
            args["config_path"] = config_path
        return args, None

    if function_name in _DELEGATION_TOOLS:
        ignored_project = _mentions_ignored_project(args, ignored_projects)
        if ignored_project:
            return args, (
                "Blocked by workspace coordinator typed policy: "
                f"{function_name} targets ignored project {ignored_project!r}."
            )

    return args, None
