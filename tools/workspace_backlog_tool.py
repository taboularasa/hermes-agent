"""Workspace-wide Linear backlog orchestration for Hermes."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from hermes_cli.ctx_runtime import normalize_ctx_bindings
from hermes_constants import display_hermes_home, get_hermes_home
from tools.codex_delegate_tool import normalize_codex_runs
from tools import linear_issue_tool as linear_tool
from tools.registry import registry
from utils import atomic_json_write

logger = logging.getLogger(__name__)

DEFAULT_TEAM_KEY = "HAD"
DEFAULT_ISSUE_LIMIT = 200
DEFAULT_CANDIDATE_LIMIT = 10
DEFAULT_STALE_HOURS = 24
DEFAULT_DIRTY_STALE_HOURS = 24
DEFAULT_CONFIG_PATH = get_hermes_home() / "notes" / "hadto-workspace-orchestrator.yaml"
DEFAULT_STATE_PATH = get_hermes_home() / "backlog" / "workspace_orchestrator_state.json"
DEFAULT_CODEX_RUNS_PATH = get_hermes_home() / "codex" / "runs.json"
DEFAULT_CTX_BINDINGS_PATH = get_hermes_home() / "ctx" / "session_bindings.json"
DEFAULT_GIT_TIMEOUT_SECONDS = 8
ACTIVE_CODEX_STATUSES = {"running", "active", "started"}

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
            "ctx_bindings_path": {
                "type": "string",
                "description": (
                    "Optional path to the ctx session binding registry "
                    f"(default: {display_hermes_home()}/ctx/session_bindings.json)."
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
            "dirty_stale_hours": {
                "type": "integer",
                "description": (
                    "Hours before unowned dirty repo state is considered stale enough "
                    "to preempt backlog work (default 24)."
                ),
                "minimum": 1,
            },
            "auto_delegate": {
                "type": "boolean",
                "description": "Delegate the selected unowned issue to Hermes when possible.",
            },
            "write_status_comment": {
                "type": "boolean",
                "description": (
                    "Write a deduplicated status comment onto the selected work item's "
                    "linked Linear issue when possible."
                ),
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


def _normalize_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve(strict=False))


def _default_branch_guess(repo_root: str) -> str:
    return "master" if repo_root.endswith("/hadto-pipeline") else "main"


def _load_orchestrator_config(path: Path) -> dict[str, Any]:
    raw = _load_yaml(path)
    repo_map = dict(DEFAULT_PROJECT_REPO_MAP)
    for project_name, repo_root in (raw.get("project_repo_roots") or {}).items():
        if str(project_name).strip() and str(repo_root).strip():
            repo_map[str(project_name).strip()] = str(repo_root).strip()

    managed_repo_roots: list[str] = []
    seen_repo_roots: set[str] = set()
    for repo_root in (raw.get("managed_repo_roots") or list(repo_map.values())):
        normalized = _normalize_path(repo_root)
        if normalized and normalized not in seen_repo_roots:
            managed_repo_roots.append(normalized)
            seen_repo_roots.add(normalized)

    default_branches: dict[str, str] = {}
    for repo_root in managed_repo_roots:
        default_branches[repo_root] = _default_branch_guess(repo_root)
    for repo_root, branch_name in (raw.get("default_branches") or {}).items():
        normalized_repo = _normalize_path(repo_root)
        normalized_branch = str(branch_name or "").strip()
        if normalized_repo and normalized_branch:
            default_branches[normalized_repo] = normalized_branch

    project_priority = [
        str(item).strip()
        for item in (raw.get("project_priority") or [])
        if str(item).strip()
    ]

    return {
        "team_key": str(raw.get("team_key") or DEFAULT_TEAM_KEY).strip() or DEFAULT_TEAM_KEY,
        "project_repo_roots": repo_map,
        "managed_repo_roots": managed_repo_roots,
        "default_branches": default_branches,
        "project_priority": project_priority,
        "stale_hours": _coerce_positive_int(raw.get("stale_hours"), DEFAULT_STALE_HOURS),
        "dirty_stale_hours": _coerce_positive_int(raw.get("dirty_stale_hours"), DEFAULT_DIRTY_STALE_HOURS),
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


def _load_latest_codex_runs(path: Path, *, now: Optional[datetime] = None) -> dict[str, dict[str, Any]]:
    latest_by_issue: dict[str, dict[str, Any]] = {}
    normalize_codex_runs(runs_path=path, now=now)
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


def _iter_codex_records(payload: Any) -> Iterable[dict[str, Any]]:
    runs = payload.get("runs", {}) if isinstance(payload, dict) else {}
    if not isinstance(runs, dict):
        return []
    return [record for record in runs.values() if isinstance(record, dict)]


def _is_active_codex_status(value: Any) -> bool:
    return str(value or "").strip().casefold() in ACTIVE_CODEX_STATUSES


def _load_codex_run_indexes(path: Path, *, now: Optional[datetime] = None) -> dict[str, dict[str, dict[str, Any]]]:
    normalize_codex_runs(runs_path=path, now=now)
    payload = _load_json(path)
    indexes: dict[str, dict[str, dict[str, Any]]] = {
        "by_issue": {},
        "by_workdir": {},
        "active_by_workdir": {},
    }

    for record in _iter_codex_records(payload):
        candidate_ts = _parse_run_timestamp(record)

        external_key = str(record.get("external_key") or "").strip()
        if external_key.startswith("linear:"):
            identifier = external_key.split(":", 1)[1].strip().upper()
            if identifier:
                existing = indexes["by_issue"].get(identifier)
                existing_ts = _parse_run_timestamp(existing) if isinstance(existing, dict) else None
                if existing is None or (candidate_ts and (existing_ts is None or candidate_ts > existing_ts)):
                    indexes["by_issue"][identifier] = record

        normalized_workdir = _normalize_path(record.get("workdir"))
        if not normalized_workdir:
            continue
        existing = indexes["by_workdir"].get(normalized_workdir)
        existing_ts = _parse_run_timestamp(existing) if isinstance(existing, dict) else None
        if existing is None or (candidate_ts and (existing_ts is None or candidate_ts > existing_ts)):
            indexes["by_workdir"][normalized_workdir] = record

        if _is_active_codex_status(record.get("status")):
            active_existing = indexes["active_by_workdir"].get(normalized_workdir)
            active_existing_ts = _parse_run_timestamp(active_existing) if isinstance(active_existing, dict) else None
            if active_existing is None or (candidate_ts and (active_existing_ts is None or candidate_ts > active_existing_ts)):
                indexes["active_by_workdir"][normalized_workdir] = record

    return indexes


def _load_active_ctx_bindings(path: Path, *, now: Optional[datetime] = None) -> dict[str, list[dict[str, Any]]]:
    normalize_ctx_bindings(bindings_path=path, now=now)
    payload = _load_json(path)
    sessions = payload.get("sessions", {}) if isinstance(payload, dict) else {}
    if not isinstance(sessions, dict):
        return {}

    active_by_worktree: dict[str, list[dict[str, Any]]] = {}
    for record in sessions.values():
        if not isinstance(record, dict) or not bool(record.get("active")):
            continue
        worktree_path = _normalize_path(record.get("worktree_path"))
        if not worktree_path:
            continue
        active_by_worktree.setdefault(worktree_path, []).append(record)
    return active_by_worktree


def _run_git(path: str, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            timeout=DEFAULT_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def _parse_branch_line(line: str) -> dict[str, Any]:
    summary = {
        "branch": "",
        "upstream": "",
        "ahead": 0,
        "behind": 0,
        "detached": False,
        "raw": line,
    }
    if not line.startswith("## "):
        return summary

    payload = line[3:].strip()
    relation = ""
    if " [" in payload and payload.endswith("]"):
        payload, relation = payload.rsplit(" [", 1)
        relation = relation[:-1]
    if "..." in payload:
        branch_name, upstream = payload.split("...", 1)
        summary["branch"] = branch_name.strip()
        summary["upstream"] = upstream.strip()
    else:
        summary["branch"] = payload.strip()
    summary["detached"] = summary["branch"].startswith("HEAD")

    for fragment in [item.strip() for item in relation.split(",") if item.strip()]:
        if fragment.startswith("ahead "):
            summary["ahead"] = _coerce_positive_int(fragment.split(" ", 1)[1], 0)
        elif fragment.startswith("behind "):
            summary["behind"] = _coerce_positive_int(fragment.split(" ", 1)[1], 0)
    return summary


def _git_status_summary(worktree_path: str) -> dict[str, Any]:
    exit_code, stdout, stderr = _run_git(worktree_path, "status", "--short", "--branch")
    if exit_code != 0:
        return {
            "worktree_path": worktree_path,
            "available": False,
            "error": (stderr or stdout).strip(),
        }

    lines = stdout.splitlines()
    branch_summary = _parse_branch_line(lines[0]) if lines else _parse_branch_line("")
    tracked_dirty = 0
    untracked = 0
    staged = 0
    unstaged = 0
    entries: list[str] = []
    for line in lines[1:]:
        entry = line.rstrip()
        if not entry:
            continue
        entries.append(entry)
        prefix = entry[:2]
        if prefix == "??":
            untracked += 1
            continue
        tracked_dirty += 1
        if len(prefix) >= 1 and prefix[0] != " ":
            staged += 1
        if len(prefix) >= 2 and prefix[1] != " ":
            unstaged += 1

    return {
        "worktree_path": worktree_path,
        "available": True,
        "branch": branch_summary.get("branch") or "",
        "upstream": branch_summary.get("upstream") or "",
        "ahead": int(branch_summary.get("ahead") or 0),
        "behind": int(branch_summary.get("behind") or 0),
        "detached": bool(branch_summary.get("detached")),
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
        "staged": staged,
        "unstaged": unstaged,
        "dirty": bool(tracked_dirty or untracked),
        "entries": entries,
        "branch_line": branch_summary.get("raw") or "",
    }


def _git_worktree_entries(repo_root: str) -> list[dict[str, Any]]:
    exit_code, stdout, stderr = _run_git(repo_root, "worktree", "list", "--porcelain")
    if exit_code != 0:
        logger.warning("Failed to enumerate git worktrees for %s: %s", repo_root, (stderr or stdout).strip())
        return []

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {
                "repo_root": repo_root,
                "worktree_path": _normalize_path(line.partition(" ")[2]),
            }
            continue
        if current is None or " " not in line:
            continue
        key, _, value = line.partition(" ")
        if key == "branch":
            current["branch_ref"] = value.strip()
            current["branch"] = value.strip().removeprefix("refs/heads/")
        elif key == "HEAD":
            current["head"] = value.strip()
        elif key == "prunable":
            current["prunable"] = value.strip()
        elif key == "detached":
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def _detect_default_branch(repo_root: str, overrides: dict[str, str]) -> str:
    normalized_root = _normalize_path(repo_root)
    override = str(overrides.get(normalized_root) or "").strip()
    if override:
        return override

    exit_code, stdout, _stderr = _run_git(repo_root, "symbolic-ref", "refs/remotes/origin/HEAD", "--short")
    if exit_code == 0 and stdout.strip():
        ref = stdout.strip()
        if "/" in ref:
            return ref.split("/", 1)[1]
        return ref

    for candidate in ("main", "master"):
        exit_code, _, _ = _run_git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}")
        if exit_code == 0:
            return candidate
    return _default_branch_guess(normalized_root)


def _git_branch_divergence(worktree_path: str, default_branch: str) -> dict[str, Optional[int]]:
    exit_code, stdout, stderr = _run_git(worktree_path, "rev-list", "--left-right", "--count", f"{default_branch}...HEAD")
    if exit_code != 0:
        return {"ahead": None, "behind": None, "error": (stderr or stdout).strip()}
    parts = stdout.strip().split()
    if len(parts) != 2:
        return {"ahead": None, "behind": None, "error": stdout.strip()}
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except Exception:
        return {"ahead": None, "behind": None, "error": stdout.strip()}
    return {"ahead": ahead, "behind": behind, "error": ""}


def _git_head_merged_to_default(worktree_path: str, default_branch: str) -> Optional[bool]:
    exit_code, _stdout, stderr = _run_git(worktree_path, "merge-base", "--is-ancestor", "HEAD", default_branch)
    if exit_code == 0:
        return True
    if exit_code == 1:
        return False
    logger.debug(
        "Failed to determine merge status for %s against %s: %s",
        worktree_path,
        default_branch,
        stderr.strip(),
    )
    return None


def _age_hours(timestamp: Optional[datetime], *, now: datetime) -> Optional[float]:
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 3600.0)


def _linked_issue_lookup(
    identifier: str,
    *,
    open_issue_index: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    normalized = str(identifier or "").strip().upper()
    if not normalized:
        return None
    if normalized in open_issue_index:
        return open_issue_index[normalized]
    if normalized in cache:
        return cache[normalized]
    try:
        cache[normalized] = linear_tool._fetch_issue(normalized, comment_limit=0)
    except Exception:
        logger.debug("Failed to fetch linked Linear issue %s during git hygiene inspection", normalized, exc_info=True)
        cache[normalized] = None
    return cache[normalized]


def _git_hygiene_incident(
    *,
    repo_root: str,
    worktree: dict[str, Any],
    status: dict[str, Any],
    default_branch: str,
    active_ctx_records: list[dict[str, Any]],
    active_codex: Optional[dict[str, Any]],
    latest_codex: Optional[dict[str, Any]],
    linked_issue: Optional[dict[str, Any]],
    now: datetime,
    dirty_stale_hours: int,
) -> Optional[dict[str, Any]]:
    worktree_path = str(status.get("worktree_path") or worktree.get("worktree_path") or "").strip()
    if not worktree_path or not bool(status.get("available")):
        return None

    tracked_dirty = int(status.get("tracked_dirty") or 0)
    untracked = int(status.get("untracked") or 0)
    dirty = bool(status.get("dirty"))
    is_root = _normalize_path(worktree_path) == _normalize_path(repo_root)
    branch = str(status.get("branch") or worktree.get("branch") or "").strip()
    branch_divergence = _git_branch_divergence(worktree_path, default_branch)
    head_merged_to_default = _git_head_merged_to_default(worktree_path, default_branch)

    linked_issue_identifier = ""
    linked_issue_state_name = ""
    linked_issue_state_type = ""
    linked_issue_open = None
    linked_issue_ownership = ""
    if isinstance(linked_issue, dict):
        linked_issue_identifier = str(linked_issue.get("identifier") or "").strip()
        linked_issue_state_name = str((linked_issue.get("state") or {}).get("name") or "").strip()
        linked_issue_state_type = str((linked_issue.get("state") or {}).get("type") or "").strip().casefold()
        linked_issue_open = linked_issue_state_type not in {"completed", "canceled"} if linked_issue_state_type else None
        linked_issue_ownership = _classify_ownership(linked_issue, "")[0]

    latest_evidence_ts = None
    if isinstance(active_codex, dict):
        latest_evidence_ts = _parse_run_timestamp(active_codex)
    if latest_evidence_ts is None and isinstance(latest_codex, dict):
        latest_evidence_ts = _parse_run_timestamp(latest_codex)
    if latest_evidence_ts is None and active_ctx_records:
        latest_evidence_ts = max(
            (_parse_time(record.get("updated_at") or record.get("created_at")) for record in active_ctx_records),
            default=None,
        )
    evidence_age_hours = _age_hours(latest_evidence_ts, now=now)

    active_ctx = bool(active_ctx_records)
    active_codex_run = isinstance(active_codex, dict)
    active_owner = active_ctx or active_codex_run
    stale_unowned = dirty and not active_owner and (
        evidence_age_hours is None or evidence_age_hours >= dirty_stale_hours
    )
    stale_clean_worktree = (
        not dirty
        and not is_root
        and not active_owner
        and evidence_age_hours is not None
        and evidence_age_hours >= dirty_stale_hours
    )

    if not dirty and not stale_clean_worktree:
        return None

    deletion_blockers: list[str] = []
    if active_ctx:
        deletion_blockers.append("active ctx binding")
    if active_codex_run:
        deletion_blockers.append("active Codex run")
    if linked_issue_open is True:
        deletion_blockers.append(f"linked Linear issue {linked_issue_identifier or 'unknown'} still open")
    if head_merged_to_default is False:
        deletion_blockers.append(f"HEAD not merged into {default_branch}")
    if tracked_dirty:
        deletion_blockers.append("tracked file modifications still present")

    if active_owner:
        selection_bucket = "observe_owned_wip"
        recommended_action = "observe_active_wip"
        selection_reason = (
            f"Owned WIP is still live in {worktree_path}; keep backlog selection elsewhere until the "
            "ctx/Codex owner finishes or goes stale."
        )
        preempts_backlog = False
        severity_rank = 6
    elif linked_issue_identifier and linked_issue_open:
        selection_bucket = "reconcile_linked_wip"
        recommended_action = "resume_or_merge_linked_wip"
        selection_reason = (
            f"{worktree_path} is dirty but unowned while linked issue {linked_issue_identifier} "
            f"remains {linked_issue_state_name or 'open'}."
        )
        preempts_backlog = True
        severity_rank = 1
    elif linked_issue_identifier and linked_issue_open is False:
        selection_bucket = "verify_completed_linked_wip"
        recommended_action = (
            "review_dirty_residue_after_merge" if deletion_blockers else "cleanup_post_merge_residue"
        )
        selection_reason = (
            f"{worktree_path} is still dirty after linked issue {linked_issue_identifier} reached "
            f"{linked_issue_state_name or 'a closed state'}."
        )
        preempts_backlog = True
        severity_rank = 2
    elif stale_clean_worktree:
        selection_bucket = "cleanup_stale_worktree"
        recommended_action = "remove_stale_worktree"
        selection_reason = (
            f"{worktree_path} is an unowned clean worktree with no recent execution evidence for "
            f"{evidence_age_hours:.1f}h."
        )
        preempts_backlog = False
        severity_rank = 5
    elif stale_unowned:
        selection_bucket = "resolve_orphaned_dirty_wip"
        recommended_action = "investigate_unowned_dirty_state"
        selection_reason = (
            f"{worktree_path} has dirty local state with no active ctx/Codex owner and no safe merge "
            "evidence yet."
        )
        preempts_backlog = True
        severity_rank = 0
    else:
        selection_bucket = "observe_recent_unowned_wip"
        recommended_action = "investigate_recent_dirty_state"
        selection_reason = (
            f"{worktree_path} is dirty without an active owner, but the latest execution evidence is still recent."
        )
        preempts_backlog = False
        severity_rank = 4

    title_branch = branch or default_branch or "unknown-branch"
    identifier = linked_issue_identifier or f"git:{worktree_path}"
    return {
        "kind": "git_hygiene",
        "identifier": identifier,
        "title": f"Reconcile repo hygiene for {title_branch}",
        "repo_root": repo_root,
        "worktree_path": worktree_path,
        "branch": branch,
        "upstream": str(status.get("upstream") or ""),
        "default_branch": default_branch,
        "is_repo_root": is_root,
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
        "dirty": dirty,
        "active_ctx": active_ctx,
        "active_ctx_session_ids": [str(record.get("session_id") or "").strip() for record in active_ctx_records],
        "active_codex": active_codex,
        "latest_codex": latest_codex,
        "linked_issue_identifier": linked_issue_identifier,
        "linked_issue_state_name": linked_issue_state_name,
        "linked_issue_state_type": linked_issue_state_type,
        "linked_issue_open": linked_issue_open,
        "linked_issue_ownership": linked_issue_ownership,
        "branch_divergence": branch_divergence,
        "upstream_ahead": int(status.get("ahead") or 0),
        "upstream_behind": int(status.get("behind") or 0),
        "head_merged_to_default": head_merged_to_default,
        "evidence_age_hours": round(evidence_age_hours, 2) if evidence_age_hours is not None else None,
        "selection_bucket": selection_bucket,
        "execution_mode": "investigate_merge_or_delete",
        "recommended_action": recommended_action,
        "selection_reason": selection_reason,
        "deletion_candidate": recommended_action in {"cleanup_post_merge_residue", "remove_stale_worktree"},
        "deletion_blockers": deletion_blockers,
        "preempts_backlog": preempts_backlog,
        "actionable": not active_owner and (dirty or stale_clean_worktree),
        "severity_rank": severity_rank,
        "sort_key": (
            severity_rank,
            0 if preempts_backlog else 1,
            0 if dirty else 1,
            0 if tracked_dirty else 1,
            -float(evidence_age_hours or 0.0),
            worktree_path,
        ),
    }


def _collect_git_hygiene(
    *,
    managed_repo_roots: list[str],
    default_branches: dict[str, str],
    codex_indexes: dict[str, dict[str, dict[str, Any]]],
    ctx_active_by_worktree: dict[str, list[dict[str, Any]]],
    open_issue_index: dict[str, dict[str, Any]],
    now: datetime,
    dirty_stale_hours: int,
) -> dict[str, Any]:
    linked_issue_cache: dict[str, dict[str, Any] | None] = {}
    incidents: list[dict[str, Any]] = []

    for repo_root in managed_repo_roots:
        if not Path(repo_root).exists():
            continue
        default_branch = _detect_default_branch(repo_root, default_branches)
        worktrees = _git_worktree_entries(repo_root)
        if not worktrees:
            continue
        for worktree in worktrees:
            worktree_path = _normalize_path(worktree.get("worktree_path"))
            if not worktree_path or not Path(worktree_path).exists():
                continue
            status = _git_status_summary(worktree_path)
            active_ctx_records = ctx_active_by_worktree.get(worktree_path, [])
            active_codex = codex_indexes["active_by_workdir"].get(worktree_path)
            latest_codex = codex_indexes["by_workdir"].get(worktree_path)
            external_key = str((active_codex or latest_codex or {}).get("external_key") or "").strip()
            linked_issue_identifier = external_key.split(":", 1)[1].strip().upper() if external_key.startswith("linear:") else ""
            linked_issue = _linked_issue_lookup(
                linked_issue_identifier,
                open_issue_index=open_issue_index,
                cache=linked_issue_cache,
            ) if linked_issue_identifier else None
            incident = _git_hygiene_incident(
                repo_root=repo_root,
                worktree=worktree,
                status=status,
                default_branch=default_branch,
                active_ctx_records=active_ctx_records,
                active_codex=active_codex,
                latest_codex=latest_codex,
                linked_issue=linked_issue,
                now=now,
                dirty_stale_hours=dirty_stale_hours,
            )
            if incident:
                incidents.append(incident)

    incidents.sort(key=lambda item: item["sort_key"])
    selected = next((item for item in incidents if item.get("preempts_backlog")), None)
    if selected is None:
        selected = next((item for item in incidents if item.get("actionable")), None)

    counts = {
        "incidents": len(incidents),
        "orphaned_dirty": sum(1 for item in incidents if item.get("selection_bucket") == "resolve_orphaned_dirty_wip"),
        "linked_wip": sum(1 for item in incidents if item.get("selection_bucket") == "reconcile_linked_wip"),
        "cleanup_candidates": sum(1 for item in incidents if item.get("deletion_candidate")),
        "active_owned": sum(1 for item in incidents if item.get("active_ctx") or item.get("active_codex")),
    }
    return {
        "counts": counts,
        "incidents": incidents,
        "selected": selected,
    }


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


def _format_git_hygiene_comment(*, selected: dict[str, Any], counts: dict[str, int], hygiene_counts: dict[str, int]) -> str:
    lines = [
        "Status: selected by the HAD workspace backlog orchestrator for repo hygiene reconciliation.",
        "",
        (
            "Workspace snapshot: "
            f"{counts.get('open', 0)} open; {counts.get('started', 0)} active; "
            f"{counts.get('hermes_owned', 0)} Hermes-owned; {counts.get('unowned', 0)} unowned"
        ),
        (
            "Repo hygiene snapshot: "
            f"{hygiene_counts.get('incidents', 0)} incidents; "
            f"{hygiene_counts.get('orphaned_dirty', 0)} orphaned dirty; "
            f"{hygiene_counts.get('cleanup_candidates', 0)} cleanup candidates"
        ),
        f"Selection bucket: `{selected.get('selection_bucket')}`",
        f"Recommended action: `{selected.get('recommended_action')}`",
        f"Why now: {selected.get('selection_reason')}",
    ]
    if selected.get("repo_root"):
        lines.append(f"Repo root: `{selected.get('repo_root')}`")
    if selected.get("worktree_path"):
        lines.append(f"Worktree: `{selected.get('worktree_path')}`")
    branch = str(selected.get("branch") or "").strip()
    default_branch = str(selected.get("default_branch") or "").strip()
    if branch or default_branch:
        lines.append(f"Branch: `{branch or 'unknown'}` vs default `{default_branch or 'unknown'}`")
    blockers = selected.get("deletion_blockers") if isinstance(selected.get("deletion_blockers"), list) else []
    if blockers:
        lines.append("Cleanup blockers: " + ", ".join(str(item) for item in blockers))
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
    selected_work: Optional[dict[str, Any]],
    stale_wip: list[dict[str, Any]],
    candidate_pool: list[dict[str, Any]],
    git_hygiene: dict[str, Any],
) -> str:
    lines = [
        (
            "Workspace backlog: "
            f"{counts.get('open', 0)} open; {counts.get('started', 0)} started; "
            f"{counts.get('hermes_owned', 0)} Hermes-owned; {counts.get('unowned', 0)} unowned"
        )
    ]
    hygiene_counts = git_hygiene.get("counts", {}) if isinstance(git_hygiene, dict) else {}
    if hygiene_counts:
        lines.append(
            "- repo hygiene: "
            f"{hygiene_counts.get('incidents', 0)} incidents; "
            f"{hygiene_counts.get('orphaned_dirty', 0)} orphaned dirty; "
            f"{hygiene_counts.get('cleanup_candidates', 0)} cleanup candidates"
        )
    if stale_wip:
        lines.append(
            "- stale WIP: "
            + ", ".join(
                f"{item.get('identifier')} ({item.get('age_hours')}h)"
                for item in stale_wip[:3]
            )
        )
    if selected_work:
        lines.append(
            f"- selected: {selected_work.get('identifier')} [{selected_work.get('selection_bucket')}] -> {selected_work.get('execution_mode')}"
        )
        lines.append(f"- why: {selected_work.get('selection_reason')}")
    elif selected:
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
    ctx_bindings_path: Path = DEFAULT_CTX_BINDINGS_PATH,
    issue_limit: int = DEFAULT_ISSUE_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    stale_hours: int = DEFAULT_STALE_HOURS,
    dirty_stale_hours: int = DEFAULT_DIRTY_STALE_HOURS,
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
    effective_dirty_stale_hours = _coerce_positive_int(
        dirty_stale_hours or cfg["dirty_stale_hours"],
        DEFAULT_DIRTY_STALE_HOURS,
    )

    users = linear_tool._list_users()
    hermes_id = _resolve_hermes_delegate_id(users)
    raw_issues = linear_tool._list_issues(
        limit=effective_issue_limit,
        filter_input={
            "team": {"key": {"eq": effective_team_key}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
    )
    codex_indexes = _load_codex_run_indexes(codex_runs_path, now=current)
    latest_codex_runs = codex_indexes["by_issue"]
    ctx_active_by_worktree = _load_active_ctx_bindings(ctx_bindings_path, now=current)
    open_issue_index = {
        str(issue.get("identifier") or "").strip().upper(): issue
        for issue in raw_issues
        if str(issue.get("identifier") or "").strip()
    }

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
    git_hygiene = _collect_git_hygiene(
        managed_repo_roots=cfg["managed_repo_roots"],
        default_branches=cfg["default_branches"],
        codex_indexes=codex_indexes,
        ctx_active_by_worktree=ctx_active_by_worktree,
        open_issue_index=open_issue_index,
        now=current,
        dirty_stale_hours=effective_dirty_stale_hours,
    )
    selected_work = git_hygiene.get("selected")
    if not isinstance(selected_work, dict) and selected:
        selected_work = dict(selected)
        selected_work["kind"] = "linear_issue"

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

    if selected and write_status_comment and (not selected_work or selected_work.get("kind") != "git_hygiene"):
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
    elif (
        write_status_comment
        and isinstance(selected_work, dict)
        and selected_work.get("kind") == "git_hygiene"
        and selected_work.get("linked_issue_identifier")
        and selected_work.get("linked_issue_open") is True
    ):
        linked_identifier = str(selected_work["linked_issue_identifier"])
        comment_result = json.loads(
            linear_tool.linear_issue(
                {
                    "action": "comment",
                    "identifier": linked_identifier,
                    "body": _format_git_hygiene_comment(
                        selected=selected_work,
                        counts=counts,
                        hygiene_counts=git_hygiene.get("counts", {}),
                    ),
                    "dedupe_key": f"workspace-orchestrator:git-hygiene:{linked_identifier}",
                }
            )
        )
        writebacks.append(
            {
                "type": "git_hygiene_comment",
                "identifier": linked_identifier,
                "result": comment_result,
            }
        )

    state_payload = {
        "evaluated_at": current.isoformat(),
        "team_key": effective_team_key,
        "selected": selected_work,
        "selected_issue": selected,
        "selected_work": selected_work,
        "counts": counts,
        "git_hygiene": git_hygiene,
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
            "ctx_bindings_path": str(ctx_bindings_path),
            "issue_limit": effective_issue_limit,
            "candidate_limit": effective_candidate_limit,
            "stale_hours": effective_stale_hours,
            "dirty_stale_hours": effective_dirty_stale_hours,
            "project_repo_roots": cfg["project_repo_roots"],
            "managed_repo_roots": cfg["managed_repo_roots"],
            "default_branches": cfg["default_branches"],
            "project_priority": cfg["project_priority"],
        },
        "counts": counts,
        "selected_issue": selected,
        "selected_work": selected_work,
        "stale_wip": stale_wip,
        "git_hygiene": git_hygiene,
        "candidate_pool": candidate_pool,
        "writebacks": writebacks,
    }
    result["summary_markdown"] = _format_summary(
        counts=counts,
        selected=selected,
        selected_work=selected_work,
        stale_wip=stale_wip,
        candidate_pool=candidate_pool,
        git_hygiene=git_hygiene,
    )
    return result


def workspace_backlog_orchestrator(
    *,
    team_key: Optional[str] = None,
    config_path: Optional[str] = None,
    state_path: Optional[str] = None,
    codex_runs_path: Optional[str] = None,
    ctx_bindings_path: Optional[str] = None,
    issue_limit: Optional[int] = None,
    candidate_limit: Optional[int] = None,
    stale_hours: Optional[int] = None,
    dirty_stale_hours: Optional[int] = None,
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
        ctx_bindings_path=Path(ctx_bindings_path).expanduser() if ctx_bindings_path else DEFAULT_CTX_BINDINGS_PATH,
        issue_limit=_coerce_positive_int(issue_limit, DEFAULT_ISSUE_LIMIT),
        candidate_limit=_coerce_positive_int(candidate_limit, DEFAULT_CANDIDATE_LIMIT),
        stale_hours=_coerce_positive_int(stale_hours, DEFAULT_STALE_HOURS),
        dirty_stale_hours=_coerce_positive_int(dirty_stale_hours, DEFAULT_DIRTY_STALE_HOURS),
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
        ctx_bindings_path=args.get("ctx_bindings_path"),
        issue_limit=args.get("issue_limit"),
        candidate_limit=args.get("candidate_limit"),
        stale_hours=args.get("stale_hours"),
        dirty_stale_hours=args.get("dirty_stale_hours"),
        auto_delegate=args.get("auto_delegate"),
        write_status_comment=args.get("write_status_comment"),
        persist=args.get("persist"),
        now=args.get("now"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_workspace_backlog_orchestrator_requirements,
    emoji="🗂️",
)
