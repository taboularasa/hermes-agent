"""Built-in Linear planning tool for durable issue and project write-back."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from hermes_cli.config import load_env
from tools.registry import registry

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
_MARKER_PREFIX = "<!-- hermes-linear:v1 "
_MARKER_SUFFIX = " -->"
# Linear rejects longer project descriptions with a generic Argument Validation
# Error. Keep project descriptions compact and reserve issue descriptions for
# the full detail.
_PROJECT_DESCRIPTION_MAX_CHARS = 240
_CTX_DAEMON_ENV_PATHS = (
    Path.home() / ".config" / "ctx-daemon.env",
)

_ISSUE_FIELDS = """
id
identifier
title
description
url
createdAt
updatedAt
completedAt
priority
assignee { id name displayName email }
delegate { id name displayName email }
state { id name type }
team { id key name }
project { id name }
labels(first: 20) { nodes { id name color } }
comments(first: 100) {
  nodes {
    id
    body
    url
    createdAt
    updatedAt
    user { id name displayName email app }
  }
}
"""

_ISSUE_MUTATION_FIELDS = """
id
identifier
title
description
url
createdAt
updatedAt
completedAt
priority
assignee { id name displayName email }
delegate { id name displayName email }
state { id name type }
team { id key name }
project { id name }
labels(first: 20) { nodes { id name color } }
"""

_PROJECT_FIELDS = """
id
name
description
url
progress
lead { id name displayName email }
teams(first: 10) { nodes { id key name } }
"""

_ISSUE_QUERY = f"""
query LinearIssue($issueId: String!) {{
  issue(id: $issueId) {{
{_ISSUE_FIELDS}
  }}
}}
"""

_VIEWER_QUERY = """
query LinearViewer {
  viewer {
    id
    name
    displayName
    email
  }
}
"""

_TEAMS_QUERY = """
query LinearTeams {
  teams {
    nodes {
      id
      key
      name
    }
  }
}
"""

_USERS_QUERY = """
query LinearUsers {
  users {
    nodes {
      id
      name
      displayName
      email
      active
    }
  }
}
"""

_PROJECTS_QUERY = f"""
query LinearProjects($limit: Int!) {{
  projects(first: $limit) {{
    nodes {{
{_PROJECT_FIELDS}
    }}
  }}
}}
"""

_PROJECT_ISSUES_QUERY = f"""
query LinearProjectIssues($projectId: String!, $limit: Int!) {{
  project(id: $projectId) {{
    id
    name
    issues(first: $limit) {{
      nodes {{
{_ISSUE_MUTATION_FIELDS}
      }}
    }}
  }}
}}
"""

_WORKFLOW_STATES_QUERY = """
query WorkflowStates($teamKey: String!) {
  workflowStates(filter: { team: { key: { eq: $teamKey } } }) {
    nodes {
      id
      name
      type
    }
  }
}
"""

_UPDATE_STATE_MUTATION = """
mutation UpdateIssueState($issueId: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $issueId, input: $input) {
    success
    issue {
      id
      identifier
      title
      url
      state { id name type }
      team { id key name }
    }
  }
}
"""

_CREATE_PROJECT_MUTATION = f"""
mutation CreateProject($input: ProjectCreateInput!) {{
  projectCreate(input: $input) {{
    success
    project {{
{_PROJECT_FIELDS}
    }}
  }}
}}
"""

_UPDATE_PROJECT_MUTATION = f"""
mutation UpdateProject($projectId: String!, $input: ProjectUpdateInput!) {{
  projectUpdate(id: $projectId, input: $input) {{
    success
    project {{
{_PROJECT_FIELDS}
    }}
  }}
}}
"""

_CREATE_ISSUE_MUTATION = f"""
mutation CreateIssue($input: IssueCreateInput!) {{
  issueCreate(input: $input) {{
    success
    issue {{
{_ISSUE_MUTATION_FIELDS}
    }}
  }}
}}
"""

_UPDATE_ISSUE_MUTATION = f"""
mutation UpdateIssue($issueId: String!, $input: IssueUpdateInput!) {{
  issueUpdate(id: $issueId, input: $input) {{
    success
    issue {{
{_ISSUE_MUTATION_FIELDS}
    }}
  }}
}}
"""

_CREATE_ISSUE_RELATION_MUTATION = """
mutation CreateIssueRelation($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) {
    success
    issueRelation {
      id
      type
    }
  }
}
"""

_CREATE_COMMENT_MUTATION = """
mutation CreateComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment {
      id
      body
      url
      createdAt
      updatedAt
    }
  }
}
"""

_UPDATE_COMMENT_MUTATION = """
mutation UpdateComment($commentId: String!, $body: String!) {
  commentUpdate(id: $commentId, input: { body: $body }) {
    success
    comment {
      id
      body
      url
      createdAt
      updatedAt
    }
  }
}
"""


def check_linear_issue_requirements() -> bool:
    """Expose the tool when a Linear API key is available."""
    return bool(_load_linear_api_key())


LINEAR_ISSUE_SCHEMA = {
    "name": "linear_issue",
    "description": (
        "Inspect a Linear issue and write durable progress updates back to it. "
        "Use this to fetch issue context, add or update a deduplicated status "
        "comment, or move an issue to another workflow state without relying on "
        "an external skill."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get",
                    "viewer",
                    "list_teams",
                    "list_users",
                    "list_projects",
                    "project_upsert",
                    "issue_upsert",
                    "issue_relation",
                    "comment",
                    "update_state",
                ],
                "description": "Which Linear issue action to perform.",
            },
            "issue_id": {
                "type": "string",
                "description": "Linear issue UUID or slug identifier such as HAD-123.",
            },
            "identifier": {
                "type": "string",
                "description": "Alias for issue_id when you want to pass a slug like HAD-123 explicitly.",
            },
            "body": {
                "type": "string",
                "description": "Comment body for action=comment.",
            },
            "comment_id": {
                "type": "string",
                "description": "Optional existing Linear comment ID to update directly for action=comment.",
            },
            "dedupe_key": {
                "type": "string",
                "description": (
                    "Optional stable key for idempotent comment updates. "
                    "When set, Hermes stores a hidden marker in the comment and "
                    "reuses that same comment on retries."
                ),
            },
            "update_existing": {
                "type": "boolean",
                "description": (
                    "For action=comment with dedupe_key, update the existing "
                    "comment in place instead of skipping it. Defaults to true."
                ),
            },
            "state_name": {
                "type": "string",
                "description": "Workflow state name for action=update_state, such as In Progress or Done.",
            },
            "state_id": {
                "type": "string",
                "description": "Workflow state UUID for action=update_state. Overrides state_name when provided.",
            },
            "comment_limit": {
                "type": "integer",
                "description": "Maximum number of recent comments to return for action=get. Default 10.",
                "minimum": 1,
            },
            "name": {
                "type": "string",
                "description": "Project name for action=project_upsert.",
            },
            "title": {
                "type": "string",
                "description": "Issue title for action=issue_upsert.",
            },
            "description": {
                "type": "string",
                "description": "Issue or project description for upsert actions.",
            },
            "team_id": {
                "type": "string",
                "description": "Linear team UUID for create or upsert actions.",
            },
            "team_key": {
                "type": "string",
                "description": "Linear team key such as HAD. Used when team_id is omitted.",
            },
            "project_id": {
                "type": "string",
                "description": "Linear project UUID for issue_upsert.",
            },
            "project_name": {
                "type": "string",
                "description": "Project name fallback for issue_upsert when project_id is omitted.",
            },
            "lead_id": {
                "type": "string",
                "description": "Optional Linear user UUID to set as project lead.",
            },
            "delegate_id": {
                "type": "string",
                "description": "Optional Linear delegate UUID for issue_upsert.",
            },
            "assignee_id": {
                "type": "string",
                "description": "Optional Linear assignee UUID for issue_upsert.",
            },
            "priority": {
                "type": "integer",
                "description": "Optional Linear priority value for issue_upsert or project_upsert.",
            },
            "label_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional label UUIDs for issue_upsert or project_upsert.",
            },
            "due_date": {
                "type": "string",
                "description": "Optional timeless due date (YYYY-MM-DD) for issue_upsert.",
            },
            "related_issue_id": {
                "type": "string",
                "description": "Related issue UUID for action=issue_relation.",
            },
            "relation_type": {
                "type": "string",
                "enum": ["blocks", "duplicate", "related", "similar"],
                "description": "Relation type for action=issue_relation.",
            },
        },
        "required": ["action"],
    },
}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _load_linear_api_key() -> str:
    token = os.getenv("LINEAR_API_KEY", "").strip()
    if token:
        return token

    hermes_env = load_env().get("LINEAR_API_KEY", "").strip()
    if hermes_env:
        return hermes_env

    for path in _CTX_DAEMON_ENV_PATHS:
        token = _load_env_file(path).get("LINEAR_API_KEY", "").strip()
        if token:
            return token
    return ""


def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    token = _load_linear_api_key()
    if not token:
        raise RuntimeError("LINEAR_API_KEY is not configured for Hermes")

    response = httpx.post(
        LINEAR_API_URL,
        json={"query": query, "variables": variables or {}},
        headers={
            "Content-Type": "application/json",
            "Authorization": token,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors")
    if errors:
        message = errors[0].get("message", "unknown error") if isinstance(errors, list) else str(errors)
        raise RuntimeError(f"Linear GraphQL error: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Linear GraphQL response did not contain a data object")
    return data


def _resolve_issue_ref(issue_id: Any, identifier: Any) -> str:
    issue_ref = str(issue_id or "").strip() or str(identifier or "").strip()
    if not issue_ref:
        raise RuntimeError("linear_issue requires issue_id or identifier")
    return issue_ref


def _normalize_project(project: dict[str, Any]) -> dict[str, Any]:
    teams = project.get("teams", {})
    team_nodes = teams.get("nodes", []) if isinstance(teams, dict) else teams
    result = dict(project)
    result["teams"] = [node for node in team_nodes if isinstance(node, dict)]
    return result


def _normalize_issue(issue: dict[str, Any], *, comment_limit: int = 10) -> dict[str, Any]:
    comments = issue.get("comments", {})
    comment_nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
    normalized_comments = comment_nodes[:comment_limit] if comment_limit > 0 else comment_nodes
    labels = issue.get("labels", {})
    label_nodes = labels.get("nodes", []) if isinstance(labels, dict) else labels
    result = dict(issue)
    result["comments"] = normalized_comments
    result["labels"] = [label for label in label_nodes if isinstance(label, dict)]
    return result


def _viewer() -> dict[str, Any]:
    data = _graphql(_VIEWER_QUERY)
    viewer = data.get("viewer")
    return viewer if isinstance(viewer, dict) else {}


def _list_teams() -> list[dict[str, Any]]:
    data = _graphql(_TEAMS_QUERY)
    teams = data.get("teams", {})
    nodes = teams.get("nodes", []) if isinstance(teams, dict) else []
    return [team for team in nodes if isinstance(team, dict)]


def _list_users() -> list[dict[str, Any]]:
    data = _graphql(_USERS_QUERY)
    users = data.get("users", {})
    nodes = users.get("nodes", []) if isinstance(users, dict) else []
    return [user for user in nodes if isinstance(user, dict)]


def _list_projects(limit: int = 100) -> list[dict[str, Any]]:
    data = _graphql(_PROJECTS_QUERY, {"limit": max(1, int(limit or 100))})
    projects = data.get("projects", {})
    nodes = projects.get("nodes", []) if isinstance(projects, dict) else []
    return [_normalize_project(project) for project in nodes if isinstance(project, dict)]


def _fetch_issue(issue_ref: str, *, comment_limit: int = 10) -> dict[str, Any]:
    data = _graphql(_ISSUE_QUERY, {"issueId": issue_ref})
    issue = data.get("issue")
    if not isinstance(issue, dict):
        raise RuntimeError(f"Linear issue not found: {issue_ref}")
    return _normalize_issue(issue, comment_limit=comment_limit)


def _project_issues(project_id: str, *, limit: int = 250) -> list[dict[str, Any]]:
    data = _graphql(_PROJECT_ISSUES_QUERY, {"projectId": project_id, "limit": max(1, int(limit or 250))})
    project = data.get("project")
    if not isinstance(project, dict):
        raise RuntimeError(f"Linear project not found: {project_id}")
    issues = project.get("issues", {})
    nodes = issues.get("nodes", []) if isinstance(issues, dict) else []
    return [_normalize_issue(issue, comment_limit=0) for issue in nodes if isinstance(issue, dict)]


def _fetch_workflow_states(team_key: str) -> list[dict[str, Any]]:
    if not team_key:
        return []
    data = _graphql(_WORKFLOW_STATES_QUERY, {"teamKey": team_key})
    workflow_states = data.get("workflowStates", {})
    nodes = workflow_states.get("nodes", []) if isinstance(workflow_states, dict) else []
    return [node for node in nodes if isinstance(node, dict)]


def _find_state_id(states: list[dict[str, Any]], state_name: str) -> str:
    wanted = state_name.strip().casefold()
    for state in states:
        if str(state.get("name") or "").strip().casefold() == wanted:
            return str(state.get("id") or "")
    available = ", ".join(sorted(str(state.get("name") or "") for state in states if state.get("name")))
    raise RuntimeError(f"Linear workflow state '{state_name}' was not found. Available: {available}")


def _build_marker(dedupe_key: str) -> str:
    payload = json.dumps({"dedupe_key": dedupe_key}, separators=(",", ":"), sort_keys=True)
    return f"{_MARKER_PREFIX}{payload}{_MARKER_SUFFIX}"


def _parse_marker(body: str) -> dict[str, Any] | None:
    if not body.startswith(_MARKER_PREFIX):
        return None
    end = body.find(_MARKER_SUFFIX, len(_MARKER_PREFIX))
    if end < 0:
        return None
    payload = body[len(_MARKER_PREFIX):end]
    try:
        parsed = json.loads(payload)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_marker(body: str) -> str:
    if not body.startswith(_MARKER_PREFIX):
        return body
    end = body.find(_MARKER_SUFFIX, len(_MARKER_PREFIX))
    if end < 0:
        return body
    return body[end + len(_MARKER_SUFFIX):].lstrip()


def _format_comment_body(body: str, dedupe_key: str | None) -> str:
    text = body.strip()
    if not dedupe_key:
        return text
    return f"{_build_marker(dedupe_key)}\n{text}"


def _format_marker_body(body: str, dedupe_key: str | None) -> str:
    return _format_comment_body(body, dedupe_key)


def _format_project_description(body: str, dedupe_key: str | None) -> str:
    marker = _build_marker(dedupe_key) if dedupe_key else ""
    text = body.strip()
    reserved = len(marker) + (1 if marker and text else 0)
    available = max(0, _PROJECT_DESCRIPTION_MAX_CHARS - reserved)
    if available and len(text) > available:
        text = text[: max(1, available - 1)].rstrip() + "…"
    if marker and text:
        return f"{marker}\n{text}"
    return marker or text


def _find_comment_by_dedupe(issue: dict[str, Any], dedupe_key: str) -> dict[str, Any] | None:
    comments = issue.get("comments", [])
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        marker = _parse_marker(str(comment.get("body") or ""))
        if marker and str(marker.get("dedupe_key") or "") == dedupe_key:
            return comment
    return None


def _find_project_by_name(projects: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = name.strip().casefold()
    for project in projects:
        if str(project.get("name") or "").strip().casefold() == wanted:
            return project
    return None


def _find_project_by_dedupe(projects: list[dict[str, Any]], dedupe_key: str) -> dict[str, Any] | None:
    for project in projects:
        marker = _parse_marker(str(project.get("description") or ""))
        if marker and str(marker.get("dedupe_key") or "") == dedupe_key:
            return project
    return None


def _find_issue_by_dedupe_or_title(
    issues: list[dict[str, Any]],
    *,
    dedupe_key: str | None,
    title: str,
) -> dict[str, Any] | None:
    if dedupe_key:
        for issue in issues:
            marker = _parse_marker(str(issue.get("description") or ""))
            if marker and str(marker.get("dedupe_key") or "") == dedupe_key:
                return issue
    wanted = title.strip().casefold()
    for issue in issues:
        if str(issue.get("title") or "").strip().casefold() == wanted:
            return issue
    return None


def _resolve_team_id(team_id: Any, team_key: Any) -> tuple[str, str]:
    raw_team_id = str(team_id or "").strip()
    raw_team_key = str(team_key or "").strip()
    teams = _list_teams()
    if raw_team_id:
        for team in teams:
            if str(team.get("id") or "") == raw_team_id:
                return raw_team_id, str(team.get("key") or raw_team_key)
        return raw_team_id, raw_team_key
    if not raw_team_key:
        raise RuntimeError("team_id or team_key is required")
    wanted = raw_team_key.casefold()
    for team in teams:
        if str(team.get("key") or "").strip().casefold() == wanted:
            return str(team.get("id") or ""), str(team.get("key") or raw_team_key)
    raise RuntimeError(f"Linear team '{raw_team_key}' was not found")


def _resolve_project_id(project_id: Any, project_name: Any) -> str:
    raw_project_id = str(project_id or "").strip()
    if raw_project_id:
        return raw_project_id
    raw_project_name = str(project_name or "").strip()
    if not raw_project_name:
        raise RuntimeError("project_id or project_name is required")
    project = _find_project_by_name(_list_projects(limit=100), raw_project_name)
    if not project:
        raise RuntimeError(f"Linear project '{raw_project_name}' was not found")
    return str(project.get("id") or "")


def _create_comment(issue_id: str, body: str) -> dict[str, Any]:
    data = _graphql(_CREATE_COMMENT_MUTATION, {"issueId": issue_id, "body": body})
    result = data.get("commentCreate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear commentCreate failed for issue {issue_id}")
    comment = result.get("comment")
    return comment if isinstance(comment, dict) else {}


def _update_comment(comment_id: str, body: str) -> dict[str, Any]:
    data = _graphql(_UPDATE_COMMENT_MUTATION, {"commentId": comment_id, "body": body})
    result = data.get("commentUpdate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear commentUpdate failed for comment {comment_id}")
    comment = result.get("comment")
    return comment if isinstance(comment, dict) else {}


def _update_issue_state(issue_id: str, state_id: str) -> dict[str, Any]:
    data = _graphql(_UPDATE_STATE_MUTATION, {"issueId": issue_id, "input": {"stateId": state_id}})
    result = data.get("issueUpdate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear issueUpdate failed for issue {issue_id}")
    issue = result.get("issue")
    return issue if isinstance(issue, dict) else {}


def _create_project(input_data: dict[str, Any]) -> dict[str, Any]:
    data = _graphql(_CREATE_PROJECT_MUTATION, {"input": input_data})
    result = data.get("projectCreate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Linear projectCreate failed")
    project = result.get("project")
    return _normalize_project(project) if isinstance(project, dict) else {}


def _update_project(project_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
    data = _graphql(_UPDATE_PROJECT_MUTATION, {"projectId": project_id, "input": input_data})
    result = data.get("projectUpdate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear projectUpdate failed for project {project_id}")
    project = result.get("project")
    return _normalize_project(project) if isinstance(project, dict) else {}


def _create_issue(input_data: dict[str, Any]) -> dict[str, Any]:
    data = _graphql(_CREATE_ISSUE_MUTATION, {"input": input_data})
    result = data.get("issueCreate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Linear issueCreate failed")
    issue = result.get("issue")
    return _normalize_issue(issue, comment_limit=0) if isinstance(issue, dict) else {}


def _update_issue(issue_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
    data = _graphql(_UPDATE_ISSUE_MUTATION, {"issueId": issue_id, "input": input_data})
    result = data.get("issueUpdate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear issueUpdate failed for issue {issue_id}")
    issue = result.get("issue")
    return _normalize_issue(issue, comment_limit=0) if isinstance(issue, dict) else {}


def _create_issue_relation(issue_id: str, related_issue_id: str, relation_type: str) -> dict[str, Any]:
    data = _graphql(
        _CREATE_ISSUE_RELATION_MUTATION,
        {
            "input": {
                "issueId": issue_id,
                "relatedIssueId": related_issue_id,
                "type": relation_type,
            }
        },
    )
    result = data.get("issueRelationCreate", {})
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Linear issueRelationCreate failed for issue {issue_id}")
    relation = result.get("issueRelation")
    return relation if isinstance(relation, dict) else {}


def linear_issue(args: dict[str, Any], **_kw) -> str:
    """Handle built-in Linear issue interactions."""
    action = str(args.get("action") or "").strip().lower()
    comment_limit = max(1, int(args.get("comment_limit") or 10))

    if action == "viewer":
        return json.dumps({"success": True, "viewer": _viewer()}, ensure_ascii=False)

    if action == "list_teams":
        return json.dumps({"success": True, "teams": _list_teams()}, ensure_ascii=False)

    if action == "list_users":
        return json.dumps({"success": True, "users": _list_users()}, ensure_ascii=False)

    if action == "list_projects":
        limit = max(1, int(args.get("limit") or 100))
        return json.dumps({"success": True, "projects": _list_projects(limit=limit)}, ensure_ascii=False)

    if action == "project_upsert":
        name = str(args.get("name") or "").strip()
        if not name:
            return json.dumps({"error": "name is required for action=project_upsert"}, ensure_ascii=False)

        description = str(args.get("description") or "").strip()
        dedupe_key = str(args.get("dedupe_key") or "").strip() or None
        lead_id = str(args.get("lead_id") or "").strip() or None
        team_id, _team_key = _resolve_team_id(args.get("team_id"), args.get("team_key"))
        existing_project = _find_project_by_dedupe(_list_projects(limit=100), dedupe_key) if dedupe_key else None
        if not existing_project:
            existing_project = _find_project_by_name(_list_projects(limit=100), name)

        desired_description = _format_project_description(description, dedupe_key)
        if existing_project:
            input_data: dict[str, Any] = {}
            if str(existing_project.get("name") or "").strip() != name:
                input_data["name"] = name
            if str(existing_project.get("description") or "").strip() != desired_description:
                input_data["description"] = desired_description
            current_team_ids = {str(team.get("id") or "") for team in existing_project.get("teams", []) if isinstance(team, dict)}
            if team_id and team_id not in current_team_ids:
                input_data["teamIds"] = sorted(current_team_ids | {team_id})
            current_lead = existing_project.get("lead") if isinstance(existing_project.get("lead"), dict) else {}
            current_lead_id = str(current_lead.get("id") or "") if isinstance(current_lead, dict) else ""
            if lead_id and lead_id != current_lead_id:
                input_data["leadId"] = lead_id

            if not input_data:
                return json.dumps(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": "project_already_matches",
                        "project": existing_project,
                    },
                    ensure_ascii=False,
                )

            project = _update_project(str(existing_project.get("id") or ""), input_data)
            return json.dumps(
                {
                    "success": True,
                    "updated_existing": True,
                    "project": project,
                },
                ensure_ascii=False,
            )

        input_data = {
            "name": name,
            "description": desired_description,
            "teamIds": [team_id],
        }
        if lead_id:
            input_data["leadId"] = lead_id
        label_ids = args.get("label_ids")
        if isinstance(label_ids, list) and label_ids:
            input_data["labelIds"] = [str(item).strip() for item in label_ids if str(item).strip()]
        priority = args.get("priority")
        if priority is not None:
            input_data["priority"] = int(priority)
        project = _create_project(input_data)
        return json.dumps(
            {
                "success": True,
                "created": True,
                "project": project,
            },
            ensure_ascii=False,
        )

    if action == "issue_upsert":
        title = str(args.get("title") or "").strip()
        if not title:
            return json.dumps({"error": "title is required for action=issue_upsert"}, ensure_ascii=False)

        issue_ref = str(args.get("issue_id") or "").strip() or str(args.get("identifier") or "").strip()
        explicit_issue = _fetch_issue(issue_ref, comment_limit=1) if issue_ref else None
        project_id = str(args.get("project_id") or "").strip()
        if not project_id and not explicit_issue:
            try:
                project_id = _resolve_project_id(args.get("project_id"), args.get("project_name"))
            except RuntimeError:
                project_id = ""

        description = str(args.get("description") or "").strip()
        dedupe_key = str(args.get("dedupe_key") or "").strip() or None
        priority = args.get("priority")
        delegate_present = "delegate_id" in args
        assignee_present = "assignee_id" in args
        delegate_id = str(args.get("delegate_id") or "").strip()
        assignee_id = str(args.get("assignee_id") or "").strip()
        due_date_present = "due_date" in args
        due_date = str(args.get("due_date") or "").strip()
        label_ids = args.get("label_ids")
        labels_present = isinstance(label_ids, list)
        label_values = [str(item).strip() for item in label_ids or [] if str(item).strip()]

        existing_issue = explicit_issue
        if not existing_issue and project_id:
            existing_issue = _find_issue_by_dedupe_or_title(
                _project_issues(project_id, limit=250),
                dedupe_key=dedupe_key,
                title=title,
            )

        team_id = str(args.get("team_id") or "").strip()
        team_key = str(args.get("team_key") or "").strip()
        if existing_issue:
            issue_team = existing_issue.get("team") if isinstance(existing_issue.get("team"), dict) else {}
            if not team_id:
                team_id = str(issue_team.get("id") or "")
            if not team_key:
                team_key = str(issue_team.get("key") or "")
        elif not team_id or not team_key:
            try:
                team_id, team_key = _resolve_team_id(args.get("team_id"), args.get("team_key"))
            except RuntimeError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        state_id = str(args.get("state_id") or "").strip()
        state_name = str(args.get("state_name") or "").strip()
        if not state_id and state_name:
            try:
                state_id = _find_state_id(_fetch_workflow_states(team_key), state_name)
            except RuntimeError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        desired_description = _format_marker_body(description, dedupe_key)
        if existing_issue:
            input_data: dict[str, Any] = {}
            if str(existing_issue.get("title") or "").strip() != title:
                input_data["title"] = title
            if _strip_marker(str(existing_issue.get("description") or "")).strip() != description:
                input_data["description"] = desired_description
            current_priority = existing_issue.get("priority")
            if priority is not None and int(priority) != (int(current_priority) if current_priority is not None else None):
                input_data["priority"] = int(priority)
            current_state = existing_issue.get("state") if isinstance(existing_issue.get("state"), dict) else {}
            current_state_id = str(current_state.get("id") or "") if isinstance(current_state, dict) else ""
            if state_id and state_id != current_state_id:
                input_data["stateId"] = state_id
            current_project = existing_issue.get("project") if isinstance(existing_issue.get("project"), dict) else {}
            current_project_id = str(current_project.get("id") or "") if isinstance(current_project, dict) else ""
            if project_id and project_id != current_project_id:
                input_data["projectId"] = project_id
            current_delegate = existing_issue.get("delegate") if isinstance(existing_issue.get("delegate"), dict) else {}
            current_delegate_id = str(current_delegate.get("id") or "") if isinstance(current_delegate, dict) else ""
            if delegate_present:
                input_data["delegateId"] = delegate_id or None
                if (delegate_id or "") == current_delegate_id:
                    input_data.pop("delegateId", None)
            current_assignee = existing_issue.get("assignee") if isinstance(existing_issue.get("assignee"), dict) else {}
            current_assignee_id = str(current_assignee.get("id") or "") if isinstance(current_assignee, dict) else ""
            if assignee_present:
                input_data["assigneeId"] = assignee_id or None
                if (assignee_id or "") == current_assignee_id:
                    input_data.pop("assigneeId", None)
            if due_date_present and due_date:
                input_data["dueDate"] = due_date
            if labels_present:
                current_labels = [str(label.get("id") or "") for label in existing_issue.get("labels", []) if isinstance(label, dict)]
                if current_labels != label_values:
                    input_data["labelIds"] = label_values

            if not input_data:
                return json.dumps(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": "issue_already_matches",
                        "issue": existing_issue,
                    },
                    ensure_ascii=False,
                )

            issue = _update_issue(str(existing_issue.get("id") or ""), input_data)
            return json.dumps(
                {
                    "success": True,
                    "updated_existing": True,
                    "issue": issue,
                },
                ensure_ascii=False,
            )

        if not team_id:
            return json.dumps({"error": "team_id or team_key is required for action=issue_upsert"}, ensure_ascii=False)
        input_data = {
            "teamId": team_id,
            "title": title,
            "description": desired_description,
        }
        if project_id:
            input_data["projectId"] = project_id
        if priority is not None:
            input_data["priority"] = int(priority)
        if state_id:
            input_data["stateId"] = state_id
        if delegate_present:
            input_data["delegateId"] = delegate_id or None
        if assignee_present:
            input_data["assigneeId"] = assignee_id or None
        if due_date_present and due_date:
            input_data["dueDate"] = due_date
        if labels_present:
            input_data["labelIds"] = label_values
        issue = _create_issue(input_data)
        return json.dumps(
            {
                "success": True,
                "created": True,
                "issue": issue,
            },
            ensure_ascii=False,
        )

    if action == "issue_relation":
        issue_ref = str(args.get("issue_id") or "").strip() or str(args.get("identifier") or "").strip()
        related_issue_id = str(args.get("related_issue_id") or "").strip()
        relation_type = str(args.get("relation_type") or "").strip() or "related"
        if not issue_ref:
            return json.dumps({"error": "issue_id or identifier is required for action=issue_relation"}, ensure_ascii=False)
        if not related_issue_id:
            return json.dumps({"error": "related_issue_id is required for action=issue_relation"}, ensure_ascii=False)
        left_issue = _fetch_issue(issue_ref, comment_limit=1)
        right_issue = _fetch_issue(related_issue_id, comment_limit=1)
        relation = _create_issue_relation(
            str(left_issue.get("id") or issue_ref),
            str(right_issue.get("id") or related_issue_id),
            relation_type,
        )
        return json.dumps({"success": True, "relation": relation}, ensure_ascii=False)

    issue_ref = _resolve_issue_ref(args.get("issue_id"), args.get("identifier"))

    if action == "get":
        issue = _fetch_issue(issue_ref, comment_limit=comment_limit)
        team = issue.get("team", {})
        team_key = str(team.get("key") or "") if isinstance(team, dict) else ""
        states = _fetch_workflow_states(team_key)
        return json.dumps(
            {
                "success": True,
                "issue": issue,
                "team_states": states,
            },
            ensure_ascii=False,
        )

    if action == "comment":
        body = str(args.get("body") or "").strip()
        if not body:
            return json.dumps({"error": "body is required for action=comment"}, ensure_ascii=False)

        issue = _fetch_issue(issue_ref, comment_limit=100)
        issue_id = str(issue.get("id") or "")
        comment_id = str(args.get("comment_id") or "").strip()
        dedupe_key = str(args.get("dedupe_key") or "").strip() or None
        update_existing = args.get("update_existing")
        if update_existing is None:
            update_existing = True
        else:
            update_existing = bool(update_existing)

        formatted_body = _format_comment_body(body, dedupe_key)
        existing_comment = _find_comment_by_dedupe(issue, dedupe_key) if dedupe_key and not comment_id else None
        target_comment_id = comment_id or (str(existing_comment.get("id") or "") if existing_comment else "")

        if target_comment_id:
            existing_body = ""
            if existing_comment:
                existing_body = str(existing_comment.get("body") or "")
            elif comment_id:
                for comment in issue.get("comments", []):
                    if isinstance(comment, dict) and str(comment.get("id") or "") == comment_id:
                        existing_body = str(comment.get("body") or "")
                        break

            if existing_body and existing_body.strip() == formatted_body.strip():
                return json.dumps(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": "comment_body_unchanged",
                        "comment_id": target_comment_id,
                    },
                    ensure_ascii=False,
                )
            if not update_existing and existing_comment:
                return json.dumps(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": "duplicate_dedupe_key",
                        "comment_id": target_comment_id,
                    },
                    ensure_ascii=False,
                )
            comment = _update_comment(target_comment_id, formatted_body)
            return json.dumps(
                {
                    "success": True,
                    "updated_existing": True,
                    "issue_id": issue_id,
                    "issue_identifier": issue.get("identifier"),
                    "comment": comment,
                },
                ensure_ascii=False,
            )

        comment = _create_comment(issue_id, formatted_body)
        return json.dumps(
            {
                "success": True,
                "created": True,
                "issue_id": issue_id,
                "issue_identifier": issue.get("identifier"),
                "comment": comment,
            },
            ensure_ascii=False,
        )

    if action == "update_state":
        issue = _fetch_issue(issue_ref, comment_limit=1)
        issue_id = str(issue.get("id") or "")
        current_state = issue.get("state", {})
        current_state_id = str(current_state.get("id") or "") if isinstance(current_state, dict) else ""
        state_id = str(args.get("state_id") or "").strip()
        state_name = str(args.get("state_name") or "").strip()

        states: list[dict[str, Any]] = []
        if not state_id:
            team = issue.get("team", {})
            team_key = str(team.get("key") or "") if isinstance(team, dict) else ""
            states = _fetch_workflow_states(team_key)
            if not state_name:
                return json.dumps({"error": "state_id or state_name is required for action=update_state"}, ensure_ascii=False)
            state_id = _find_state_id(states, state_name)

        if current_state_id and current_state_id == state_id:
            return json.dumps(
                {
                    "success": True,
                    "skipped": True,
                    "reason": "issue_already_in_state",
                    "issue_id": issue_id,
                    "issue_identifier": issue.get("identifier"),
                    "state": current_state,
                },
                ensure_ascii=False,
            )

        updated_issue = _update_issue_state(issue_id, state_id)
        return json.dumps(
            {
                "success": True,
                "issue": updated_issue,
                "team_states": states,
            },
            ensure_ascii=False,
        )

    return json.dumps({"error": f"Unknown linear_issue action: {action}"}, ensure_ascii=False)


registry.register(
    name="linear_issue",
    toolset="linear",
    schema=LINEAR_ISSUE_SCHEMA,
    handler=linear_issue,
    check_fn=check_linear_issue_requirements,
    emoji="📎",
)
