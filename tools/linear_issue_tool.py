"""Built-in Linear issue tool for durable issue inspection and write-back."""

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
_CTX_DAEMON_ENV_PATHS = (
    Path.home() / ".config" / "ctx-daemon.env",
)

_ISSUE_QUERY = """
query LinearIssue($issueId: String!) {
  issue(id: $issueId) {
    id
    identifier
    title
    description
    url
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
  }
}
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
                "enum": ["get", "comment", "update_state"],
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


def _normalize_issue(issue: dict[str, Any], *, comment_limit: int = 10) -> dict[str, Any]:
    comments = issue.get("comments", {})
    comment_nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
    normalized_comments = comment_nodes[:comment_limit] if comment_limit > 0 else comment_nodes
    result = dict(issue)
    result["comments"] = normalized_comments
    return result


def _fetch_issue(issue_ref: str, *, comment_limit: int = 10) -> dict[str, Any]:
    data = _graphql(_ISSUE_QUERY, {"issueId": issue_ref})
    issue = data.get("issue")
    if not isinstance(issue, dict):
        raise RuntimeError(f"Linear issue not found: {issue_ref}")
    return _normalize_issue(issue, comment_limit=comment_limit)


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


def _find_comment_by_dedupe(issue: dict[str, Any], dedupe_key: str) -> dict[str, Any] | None:
    comments = issue.get("comments", [])
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        marker = _parse_marker(str(comment.get("body") or ""))
        if marker and str(marker.get("dedupe_key") or "") == dedupe_key:
            return comment
    return None


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


def linear_issue(args: dict[str, Any], **_kw) -> str:
    """Handle built-in Linear issue interactions."""
    action = str(args.get("action") or "").strip().lower()
    issue_ref = _resolve_issue_ref(args.get("issue_id"), args.get("identifier"))
    comment_limit = max(1, int(args.get("comment_limit") or 10))

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
