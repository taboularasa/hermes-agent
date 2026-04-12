"""Workspace-wide Linear backlog orchestration for Hermes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from hermes_constants import display_hermes_home, get_hermes_home
from tools import linear_issue_tool as linear_tool
from tools.registry import registry
from utils import atomic_json_write

logger = logging.getLogger(__name__)

DEFAULT_TEAM_KEY = "HAD"
DEFAULT_ISSUE_LIMIT = 200
DEFAULT_CANDIDATE_LIMIT = 10
DEFAULT_STALE_HOURS = 24
DEFAULT_CONFIG_PATH = get_hermes_home() / "notes" / "hadto-workspace-orchestrator.yaml"
DEFAULT_STATE_PATH = get_hermes_home() / "backlog" / "workspace_orchestrator_state.json"
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"

DEFAULT_PROJECT_REPO_MAP = {
    "Hermes Self-Improvement": "/home/david/stacks/hermes-agent",
    "Hermes Journal": "/home/david/stacks/hermes-journal",
    "Hadto Ontology Platform": "/home/david/stacks/smb-ontology-platform",
    "Hadto Ontology Workbench": "/home/david/stacks/hadto-ontology-workbench",
    "Hadto Pipeline": "/home/david/stacks/hadto-pipeline",
    "Hadto.co": "/home/david/stacks/hadto.co",
    "Hermes Field Copilot": "/home/david/stacks/phoneitin",
    "Home Server Deployment": "/home/david/stacks/phoneitin",
    "Research Quality Loop": "/home/david/stacks/phoneitin",
}

WORKSPACE_BACKLOG_ORCHESTRATOR_SCHEMA = {
    "name": "workspace_backlog_orchestrator",
    "description": (
        "Inspect the full HAD Linear backlog, detect stale WIP, map issues to "
        "local repo roots, and select the highest-leverage Hermes issue to act on next."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "team_key": {
                "type": "string",
                "description": "Linear team key to inspect (default: HAD).",
            },
            "config_path": {
                "type": "string",
                "description": (
                    "Optional YAML override for repo mappings and prioritization "
                    f"(default: {display_hermes_home()}/notes/hadto-workspace-orchestrator.yaml)."
                ),
            },
            "state_path": {
                "type": "string",
                "description": (
                    "Optional JSON path for the persisted orchestrator snapshot "
                    f"(default: {display_hermes_home()}/backlog/workspace_orchestrator_state.json)."
                ),
            },
            "codex_runs_path": {
                "type": "string",
                "description": (
                    "Optional path to the Codex supervisor run registry "
                    f"(default: {display_hermes_home()}/codex/runs.json)."
                ),
            },
            "issue_limit": {
                "type": "integer",
                "description": "Maximum number of open issues to inspect (default 200).",
                "minimum": 1,
            },
            "candidate_limit": {
                "type": "integer",
                "description": "Maximum number of ranked candidates to return (default 10).",
                "minimum": 1,
            },
            "stale_hours": {
                "type": "integer",
                "description": "Hours before active WIP is considered stale (default 24).",
                "minimum": 1,
            },
            "auto_delegate": {
                "type": "boolean",
                "description": "Delegate the selected unowned issue to Hermes when possible.",
            },
            "write_status_comment": {
                "type": "boolean",
                "description": "Write a deduplicated status comment onto the selected issue.",
            },
            "persist": {
                "type": "boolean",
                "description": "Persist the orchestrator snapshot to disk (default true).",
            },
            "now": {
                "type": "string",
                "description": "Optional ISO timestamp override for tests (defaults to now).",
            },
        },
        "required": [],
    },
}


def check_workspace_backlog_orchestrator_requirements() -> bool:
    return linear_tool.check_linear_issue_requirements()


def _parse_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        if value.replace(".", "", 1).isdigit():
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to load JSON from %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to load YAML from %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return default


def _load_orchestrator_config(path: Path) -> dict[str, Any]:
    raw = _load_yaml(path)
    repo_map = dict(DEFAULT_PROJECT_REPO_MAP)
    for project_name, repo_root in (raw.get("project_repo_roots") or {}).items():
        if str(project_name).strip() and str(repo_root).strip():
            repo_map[str(project_name).strip()] = str(repo_root).strip()

    project_priority = [
        str(item).strip()
        for item in (raw.get("project_priority") or [])
        if str(item).strip()
    ]

    return {
        "team_key": str(raw.get("team_key") or DEFAULT_TEAM_KEY).strip() or DEFAULT_TEAM_KEY,
        "project_repo_roots": repo_map,
        "project_priority": project_priority,
        "stale_hours": _coerce_positive_int(raw.get("stale_hours"), DEFAULT_STALE_HOURS),
        "candidate_limit": _coerce_positive_int(raw.get("candidate_limit"), DEFAULT_CANDIDATE_LIMIT),
        "issue_limit": _coerce_positive_int(raw.get("issue_limit"), DEFAULT_ISSUE_LIMIT),
    }


def _resolve_hermes_delegate_id(users: Iterable[dict[str, Any]]) -> str:
    exact_matches: list[str] = []
    fuzzy_matches: list[str] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            continue
        active = bool(user.get("active", True))
        name = str(user.get("name") or "").strip()
        display_name = str(user.get("displayName") or "").strip()
        email = str(user.get("email") or "").strip()
        email_local = email.partition("@")[0].strip()
        tokens = [value.casefold() for value in (name, display_name, email_local) if value]
        if any(token == "hermes" for token in tokens) and active:
            exact_matches.append(user_id)
        elif active and any("hermes" in token for token in tokens):
            fuzzy_matches.append(user_id)
    return exact_matches[0] if exact_matches else (fuzzy_matches[0] if fuzzy_matches else "")


def _parse_issue_timestamp(issue: dict[str, Any]) -> Optional[datetime]:
    for key in ("updatedAt", "startedAt", "createdAt"):
        parsed = _parse_time(issue.get(key))
        if parsed:
            return parsed
    return None


def _parse_run_timestamp(record: dict[str, Any]) -> Optional[datetime]:
    for key in ("completed_at", "started_at", "process_started_at"):
        parsed = _parse_time(record.get(key))
        if parsed:
            return parsed
    return None


def _load_latest_codex_runs(path: Path) -> dict[str, dict[str, Any]]:
    latest_by_issue: dict[str, dict[str, Any]] = {}
    runs = _load_json(path).get("runs", {})
    if not isinstance(runs, dict):
        return latest_by_issue

    for record in runs.values():
        if not isinstance(record, dict):
            continue
        external_key = str(record.get("external_key") or "").strip()
        if not external_key.startswith("linear:"):
            continue
        identifier = external_key.split(":", 1)[1].strip().upper()
        if not identifier:
            continue
        candidate_ts = _parse_run_timestamp(record)
        existing = latest_by_issue.get(identifier)
        existing_ts = _parse_run_timestamp(existing) if isinstance(existing, dict) else None
        if existing is None or (candidate_ts and (existing_ts is None or candidate_ts > existing_ts)):
            latest_by_issue[identifier] = record
    return latest_by_issue


def _repo_root_for_issue(issue: dict[str, Any], repo_map: dict[str, str]) -> str:
    project = issue.get("project") if isinstance(issue.get("project"), dict) else {}
    project_name = str(project.get("name") or "").strip()
    return str(repo_map.get(project_name) or "").strip()


def _project_priority_rank(project_name: str, project_priority: list[str]) -> int:
    normalized = project_name.casefold()
    for index, candidate in enumerate(project_priority):
        if candidate.casefold() == normalized:
            return index
    return len(project_priority) + 50


def _priority_rank(priority: Any) -> int:
    try:
        numeric = int(priority)
    except Exception:
        numeric = 0
    return numeric if numeric > 0 else 99


def _state_sort_rank(state_type: str, *, stale_wip: bool, ownership: str) -> int:
    if stale_wip and ownership == "hermes":
        return 0
    if stale_wip and ownership == "unowned":
        return 1
    if state_type == "started" and ownership == "hermes":
        return 2
    if state_type == "started" and ownership == "unowned":
        return 3
    if ownership == "hermes":
        return 4
    if ownership == "unowned":
        return 5
    return 9


def _is_other_agent(owner: dict[str, Any]) -> bool:
    email = str(owner.get("email") or "").strip().casefold()
    return email.endswith("@oauthapp.linear.app")


def _summarize_owner(owner: dict[str, Any]) -> str:
    for key in ("displayName", "name", "email"):
        value = str(owner.get(key) or "").strip()
        if value:
            return value
    return ""


def _classify_ownership(issue: dict[str, Any], hermes_id: str) -> tuple[str, str]:
    delegate = issue.get("delegate") if isinstance(issue.get("delegate"), dict) else {}
    assignee = issue.get("assignee") if isinstance(issue.get("assignee"), dict) else {}

    delegate_id = str(delegate.get("id") or "").strip()
    assignee_id = str(assignee.get("id") or "").strip()
    if hermes_id and (delegate_id == hermes_id or assignee_id == hermes_id):
        return "hermes", _summarize_owner(delegate if delegate_id == hermes_id else assignee)
    if delegate_id:
        if _is_other_agent(delegate):
            return "other-agent", _summarize_owner(delegate)
        return "human", _summarize_owner(delegate)
    if assignee_id:
        return "human", _summarize_owner(assignee)
    return "unowned", ""


def _active_relation_targets(relations: list[dict[str, Any]], *, key: str) -> list[dict[str, str]]:
    active: list[dict[str, str]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if str(relation.get("type") or "").strip().casefold() != "blocks":
            continue
        target = relation.get(key)
        if not isinstance(target, dict):
            continue
        state = target.get("state") if isinstance(target.get("state"), dict) else {}
        state_type = str(state.get("type") or "").strip().casefold()
        if state_type in {"completed", "canceled"}:
            continue
        active.append(
            {
                "id": str(target.get("id") or "").strip(),
                "identifier": str(target.get("identifier") or "").strip(),
                "title": str(target.get("title") or "").strip(),
                "state_name": str(state.get("name") or "").strip(),
                "state_type": state_type,
            }
        )
    return active


def _selection_bucket(*, state_type: str, stale_wip: bool, ownership: str, repo_root: str) -> str:
    if stale_wip:
        return "revive_stale_wip"
    if state_type == "started":
        return "continue_active_wip"
    if ownership == "unowned" and repo_root:
        return "claim_actionable_backlog"
    if ownership == "hermes" and repo_root:
        return "continue_hermes_backlog"
    if repo_root:
        return "planning_or_followthrough"
    return "repo_unresolved"


def _execution_mode(*, bucket: str, latest_codex: Optional[dict[str, Any]], repo_root: str) -> str:
    status = str((latest_codex or {}).get("status") or "").strip().casefold()
    if status in {"running", "active", "started"}:
        return "observe_running_codex"
    if bucket == "revive_stale_wip":
        return "resume_or_repair_wip"
    if not repo_root:
        return "planning_only"
    return "delegate_codex"


def _issue_to_candidate(
    issue: dict[str, Any],
    *,
    now: datetime,
    hermes_id: str,
    stale_hours: int,
    repo_map: dict[str, str],
    project_priority: list[str],
    latest_codex_runs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    identifier = str(issue.get("identifier") or "").strip()
    project = issue.get("project") if isinstance(issue.get("project"), dict) else {}
    project_name = str(project.get("name") or "").strip() or "No Project"
    state = issue.get("state") if isinstance(issue.get("state"), dict) else {}
    state_name = str(state.get("name") or "").strip()
    state_type = str(state.get("type") or "").strip().casefold()
    ownership, owner_label = _classify_ownership(issue, hermes_id)
    updated_at = _parse_issue_timestamp(issue)
    age_hours = None
    if updated_at is not None:
        age_hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
    stale_wip = bool(state_type == "started" and age_hours is not None and age_hours >= stale_hours)
    blocked_by = _active_relation_targets(issue.get("inverseRelations", []), key="issue")
    blocking = _active_relation_targets(issue.get("relations", []), key="relatedIssue")
    repo_root = _repo_root_for_issue(issue, repo_map)
    latest_codex = latest_codex_runs.get(identifier.upper())
    bucket = _selection_bucket(
        state_type=state_type,
        stale_wip=stale_wip,
        ownership=ownership,
        repo_root=repo_root,
    )
    execution_mode = _execution_mode(bucket=bucket, latest_codex=latest_codex, repo_root=repo_root)
    actionable = ownership in {"hermes", "unowned"} and not blocked_by
    sort_key = (
        1 if not actionable else 0,
        _state_sort_rank(state_type, stale_wip=stale_wip, ownership=ownership),
        _priority_rank(issue.get("priority")),
        _project_priority_rank(project_name, project_priority),
        1 if repo_root else 2,
        -(age_hours or 0.0) if state_type == "started" else (age_hours or 0.0),
        identifier,
    )

    if stale_wip:
        selection_reason = (
            f"Revive stale WIP: {identifier} has been {state_name or 'active'} for "
            f"{age_hours:.1f}h without a fresh update."
        )
    elif state_type == "started":
        selection_reason = f"Continue active WIP: {identifier} is already {state_name or 'started'}."
    elif ownership == "hermes":
        selection_reason = f"Continue Hermes-owned backlog: {identifier} is already delegated to Hermes."
    elif ownership == "unowned":
        selection_reason = f"Claim unowned backlog: {identifier} is open and actionable."
    else:
        selection_reason = f"Skip externally owned issue: {identifier} belongs to {owner_label or ownership}."

    return {
        "id": str(issue.get("id") or "").strip(),
        "identifier": identifier,
        "title": str(issue.get("title") or "").strip(),
        "url": str(issue.get("url") or "").strip(),
        "project_name": project_name,
        "priority": issue.get("priority"),
        "state_name": state_name,
        "state_type": state_type,
        "ownership": ownership,
        "owner_label": owner_label,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "stale_wip": stale_wip,
        "blocked_by": blocked_by,
        "blocking": blocking,
        "repo_root": repo_root,
        "repo_resolved": bool(repo_root),
        "latest_codex": latest_codex,
        "selection_bucket": bucket,
        "execution_mode": execution_mode,
        "selection_reason": selection_reason,
        "actionable": actionable,
        "sort_key": sort_key,
    }


def _format_status_comment(*, selected: dict[str, Any], counts: dict[str, int]) -> str:
    lines = [
        "Status: selected by the HAD workspace backlog orchestrator.",
        "",
        (
            "Workspace snapshot: "
            f"{counts.get('open', 0)} open; {counts.get('started', 0)} active; "
            f"{counts.get('hermes_owned', 0)} Hermes-owned; {counts.get('unowned', 0)} unowned"
        ),
        f"Selection bucket: `{selected.get('selection_bucket')}`",
        f"Execution mode: `{selected.get('execution_mode')}`",
        f"Why now: {selected.get('selection_reason')}",
    ]
    if selected.get("repo_root"):
        lines.append(f"Repo root: `{selected.get('repo_root')}`")
    if selected.get("blocked_by"):
        blockers = ", ".join(item.get("identifier") or item.get("title") or "unknown" for item in selected["blocked_by"])
        lines.append(f"Blockers: {blockers}")
    latest_codex = selected.get("latest_codex") if isinstance(selected.get("latest_codex"), dict) else {}
    if latest_codex:
        lines.append(
            "Latest Codex run: "
            f"`{latest_codex.get('status')}` "
            f"({latest_codex.get('run_id') or latest_codex.get('process_session_id') or 'unknown'})"
        )
    return "\n".join(lines).strip()


def _format_summary(
    *,
    counts: dict[str, int],
    selected: Optional[dict[str, Any]],
    stale_wip: list[dict[str, Any]],
    candidate_pool: list[dict[str, Any]],
) -> str:
    lines = [
        (
            "Workspace backlog: "
            f"{counts.get('open', 0)} open; {counts.get('started', 0)} started; "
            f"{counts.get('hermes_owned', 0)} Hermes-owned; {counts.get('unowned', 0)} unowned"
        )
    ]
    if stale_wip:
        lines.append(
            "- stale WIP: "
            + ", ".join(
                f"{item.get('identifier')} ({item.get('age_hours')}h)"
                for item in stale_wip[:3]
            )
        )
    if selected:
        lines.append(
            f"- selected: {selected.get('identifier')} [{selected.get('selection_bucket')}] -> {selected.get('execution_mode')}"
        )
        lines.append(f"- why: {selected.get('selection_reason')}")
    else:
        lines.append("- selected: none")
    for candidate in candidate_pool[:3]:
        lines.append(
            f"- candidate: {candidate.get('identifier')} [{candidate.get('selection_bucket')}] "
            f"{candidate.get('state_name') or candidate.get('state_type')}"
        )
    return "\n".join(lines)


def evaluate_workspace_backlog(
    *,
    team_key: str = DEFAULT_TEAM_KEY,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    codex_runs_path: Path = DEFAULT_CODEX_RUNS_PATH,
    issue_limit: int = DEFAULT_ISSUE_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    stale_hours: int = DEFAULT_STALE_HOURS,
    auto_delegate: bool = False,
    write_status_comment: bool = False,
    persist: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    cfg = _load_orchestrator_config(config_path)
    effective_team_key = str(team_key or cfg["team_key"]).strip() or DEFAULT_TEAM_KEY
    effective_issue_limit = _coerce_positive_int(issue_limit or cfg["issue_limit"], DEFAULT_ISSUE_LIMIT)
    effective_candidate_limit = _coerce_positive_int(
        candidate_limit or cfg["candidate_limit"], DEFAULT_CANDIDATE_LIMIT
    )
    effective_stale_hours = _coerce_positive_int(stale_hours or cfg["stale_hours"], DEFAULT_STALE_HOURS)

    users = linear_tool._list_users()
    hermes_id = _resolve_hermes_delegate_id(users)
    raw_issues = linear_tool._list_issues(
        limit=effective_issue_limit,
        filter_input={
            "team": {"key": {"eq": effective_team_key}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
    )
    latest_codex_runs = _load_latest_codex_runs(codex_runs_path)

    candidates = [
        _issue_to_candidate(
            issue,
            now=current,
            hermes_id=hermes_id,
            stale_hours=effective_stale_hours,
            repo_map=cfg["project_repo_roots"],
            project_priority=cfg["project_priority"],
            latest_codex_runs=latest_codex_runs,
        )
        for issue in raw_issues
    ]
    candidates.sort(key=lambda item: item["sort_key"])

    selected = next((item for item in candidates if item.get("actionable")), None)
    stale_wip = [item for item in candidates if item.get("actionable") and item.get("stale_wip")]
    candidate_pool = candidates[:effective_candidate_limit]

    writebacks: list[dict[str, Any]] = []
    if selected and auto_delegate and hermes_id and selected.get("ownership") == "unowned":
        delegate_result = json.loads(
            linear_tool.linear_issue(
                {
                    "action": "issue_upsert",
                    "identifier": selected["identifier"],
                    "delegate_id": hermes_id,
                }
            )
        )
        writebacks.append(
            {
                "type": "delegate",
                "identifier": selected["identifier"],
                "result": delegate_result,
            }
        )
        issue_payload = delegate_result.get("issue")
        if isinstance(issue_payload, dict):
            selected["ownership"] = "hermes"
            selected["owner_label"] = "Hermes"

    counts = {
        "open": len(candidates),
        "started": sum(1 for item in candidates if item.get("state_type") == "started"),
        "hermes_owned": sum(1 for item in candidates if item.get("ownership") == "hermes"),
        "unowned": sum(1 for item in candidates if item.get("ownership") == "unowned"),
        "human_owned": sum(1 for item in candidates if item.get("ownership") == "human"),
        "other_agent_owned": sum(1 for item in candidates if item.get("ownership") == "other-agent"),
        "stale_wip": len(stale_wip),
    }

    if selected and write_status_comment:
        comment_result = json.loads(
            linear_tool.linear_issue(
                {
                    "action": "comment",
                    "identifier": selected["identifier"],
                    "body": _format_status_comment(selected=selected, counts=counts),
                    "dedupe_key": f"workspace-orchestrator:{selected['identifier']}",
                }
            )
        )
        writebacks.append(
            {
                "type": "status_comment",
                "identifier": selected["identifier"],
                "result": comment_result,
            }
        )

    state_payload = {
        "evaluated_at": current.isoformat(),
        "team_key": effective_team_key,
        "selected": selected,
        "counts": counts,
        "candidate_pool": candidate_pool,
        "writebacks": writebacks,
    }
    if persist:
        atomic_json_write(state_path, state_payload)

    result = {
        "evaluated_at": current.isoformat(),
        "team_key": effective_team_key,
        "hermes_delegate_id": hermes_id,
        "config": {
            "config_path": str(config_path),
            "state_path": str(state_path),
            "codex_runs_path": str(codex_runs_path),
            "issue_limit": effective_issue_limit,
            "candidate_limit": effective_candidate_limit,
            "stale_hours": effective_stale_hours,
            "project_repo_roots": cfg["project_repo_roots"],
            "project_priority": cfg["project_priority"],
        },
        "counts": counts,
        "selected_issue": selected,
        "stale_wip": stale_wip,
        "candidate_pool": candidate_pool,
        "writebacks": writebacks,
    }
    result["summary_markdown"] = _format_summary(
        counts=counts,
        selected=selected,
        stale_wip=stale_wip,
        candidate_pool=candidate_pool,
    )
    return result


def workspace_backlog_orchestrator(
    *,
    team_key: Optional[str] = None,
    config_path: Optional[str] = None,
    state_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    issue_limit: Optional[int] = None,
    candidate_limit: Optional[int] = None,
    stale_hours: Optional[int] = None,
    auto_delegate: Optional[bool] = None,
    write_status_comment: Optional[bool] = None,
    persist: Optional[bool] = None,
    now: Optional[str] = None,
    task_id: Optional[str] = None,
) -> str:
    result = evaluate_workspace_backlog(
        team_key=str(team_key or DEFAULT_TEAM_KEY),
        config_path=Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH,
        state_path=Path(state_path).expanduser() if state_path else DEFAULT_STATE_PATH,
        codex_runs_path=Path(codex_runs_path).expanduser() if codex_runs_path else DEFAULT_CODEX_RUNS_PATH,
        issue_limit=_coerce_positive_int(issue_limit, DEFAULT_ISSUE_LIMIT),
        candidate_limit=_coerce_positive_int(candidate_limit, DEFAULT_CANDIDATE_LIMIT),
        stale_hours=_coerce_positive_int(stale_hours, DEFAULT_STALE_HOURS),
        auto_delegate=False if auto_delegate is None else bool(auto_delegate),
        write_status_comment=False if write_status_comment is None else bool(write_status_comment),
        persist=True if persist is None else bool(persist),
        now=_parse_time(now) if now else None,
    )
    return json.dumps({"success": True, "result": result, "task_id": task_id}, ensure_ascii=False)


registry.register(
    name="workspace_backlog_orchestrator",
    toolset="linear",
    schema=WORKSPACE_BACKLOG_ORCHESTRATOR_SCHEMA,
    handler=lambda args, **kw: workspace_backlog_orchestrator(
        team_key=args.get("team_key"),
        config_path=args.get("config_path"),
        state_path=args.get("state_path"),
        codex_runs_path=args.get("codex_runs_path"),
        issue_limit=args.get("issue_limit"),
        candidate_limit=args.get("candidate_limit"),
        stale_hours=args.get("stale_hours"),
        auto_delegate=args.get("auto_delegate"),
        write_status_comment=args.get("write_status_comment"),
        persist=args.get("persist"),
        now=args.get("now"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_workspace_backlog_orchestrator_requirements,
    emoji="🗂️",
)
