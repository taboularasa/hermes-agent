import json

from tools import linear_issue_tool


def test_check_linear_issue_requirements_uses_ctx_daemon_env_fallback(monkeypatch, tmp_path):
    env_path = tmp_path / "ctx-daemon.env"
    env_path.write_text("LINEAR_API_KEY=lin_test_from_ctx\n", encoding="utf-8")

    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setattr(linear_issue_tool, "_CTX_DAEMON_ENV_PATHS", (env_path,))
    monkeypatch.setattr(linear_issue_tool, "load_env", lambda: {})

    assert linear_issue_tool.check_linear_issue_requirements() is True


def test_linear_issue_comment_updates_existing_deduped_comment(monkeypatch):
    issue = {
        "id": "issue-1",
        "identifier": "HAD-123",
        "comments": [
            {
                "id": "comment-1",
                "body": linear_issue_tool._format_comment_body("old body", "status:had-123"),
            }
        ],
    }
    update_calls = []

    monkeypatch.setattr(linear_issue_tool, "_fetch_issue", lambda issue_ref, comment_limit=10: issue)
    monkeypatch.setattr(
        linear_issue_tool,
        "_update_comment",
        lambda comment_id, body: update_calls.append((comment_id, body)) or {"id": comment_id, "body": body},
    )

    result = json.loads(
        linear_issue_tool.linear_issue(
            {
                "action": "comment",
                "identifier": "HAD-123",
                "body": "new body",
                "dedupe_key": "status:had-123",
            }
        )
    )

    assert result["success"] is True
    assert result["updated_existing"] is True
    assert update_calls == [
        ("comment-1", linear_issue_tool._format_comment_body("new body", "status:had-123"))
    ]


def test_linear_issue_comment_creates_when_no_deduped_comment_exists(monkeypatch):
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_issue",
        lambda issue_ref, comment_limit=10: {
            "id": "issue-1",
            "identifier": "HAD-123",
            "comments": [],
        },
    )
    create_calls = []
    monkeypatch.setattr(
        linear_issue_tool,
        "_create_comment",
        lambda issue_id, body: create_calls.append((issue_id, body)) or {"id": "comment-2", "body": body},
    )

    result = json.loads(
        linear_issue_tool.linear_issue(
            {
                "action": "comment",
                "identifier": "HAD-123",
                "body": "fresh body",
                "dedupe_key": "status:had-123",
            }
        )
    )

    assert result["success"] is True
    assert result["created"] is True
    assert create_calls == [
        ("issue-1", linear_issue_tool._format_comment_body("fresh body", "status:had-123"))
    ]


def test_linear_issue_update_state_skips_when_issue_already_in_target_state(monkeypatch):
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_issue",
        lambda issue_ref, comment_limit=10: {
            "id": "issue-1",
            "identifier": "HAD-123",
            "state": {"id": "state-2", "name": "Done", "type": "completed"},
            "team": {"key": "HAD"},
            "comments": [],
        },
    )
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_workflow_states",
        lambda team_key: [
            {"id": "state-1", "name": "In Progress", "type": "started"},
            {"id": "state-2", "name": "Done", "type": "completed"},
        ],
    )

    result = json.loads(
        linear_issue_tool.linear_issue(
            {
                "action": "update_state",
                "identifier": "HAD-123",
                "state_name": "Done",
            }
        )
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["reason"] == "issue_already_in_state"


def test_linear_issue_get_returns_issue_and_team_states(monkeypatch):
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_issue",
        lambda issue_ref, comment_limit=10: {
            "id": "issue-1",
            "identifier": "HAD-123",
            "team": {"key": "HAD"},
            "comments": [{"id": "comment-1"}],
        },
    )
    monkeypatch.setattr(
        linear_issue_tool,
        "_fetch_workflow_states",
        lambda team_key: [{"id": "state-1", "name": "In Progress", "type": "started"}],
    )

    result = json.loads(
        linear_issue_tool.linear_issue(
            {
                "action": "get",
                "identifier": "HAD-123",
            }
        )
    )

    assert result["success"] is True
    assert result["issue"]["identifier"] == "HAD-123"
    assert result["team_states"][0]["name"] == "In Progress"


def test_normalize_issue_keeps_newest_comments_first():
    issue = {
        "comments": {
            "nodes": [
                {"id": "comment-newest"},
                {"id": "comment-middle"},
                {"id": "comment-oldest"},
            ]
        }
    }

    normalized = linear_issue_tool._normalize_issue(issue, comment_limit=2)

    assert [comment["id"] for comment in normalized["comments"]] == [
        "comment-newest",
        "comment-middle",
    ]
