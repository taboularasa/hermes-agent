import io
import json
import urllib.error

import pytest

from model_tools import get_tool_definitions
from tools import hubspot_tool
from tools.hubspot_tool import HubSpotAPIError, HubSpotClient, hubspot_crm


def _http_error(status, payload):
    return urllib.error.HTTPError(
        "https://api.hubapi.com/test",
        status,
        "HubSpot error",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def test_client_surfaces_missing_scope_error():
    def transport(_request, _timeout):
        raise _http_error(
            403,
            {
                "status": "error",
                "category": "MISSING_SCOPES",
                "message": "This oauth-token requires all of [crm.objects.tasks.read]",
                "correlationId": "abc-123",
            },
        )

    client = HubSpotClient("token", transport=transport)

    with pytest.raises(HubSpotAPIError) as raised:
        client.search("tasks", filters=[], properties=["hs_task_subject"])

    exc = raised.value
    assert exc.error_type == "missing_scopes"
    assert exc.status_code == 403
    assert exc.missing_scopes == ["crm.objects.tasks.read"]
    assert exc.correlation_id == "abc-123"


def test_client_surfaces_auth_failure():
    def transport(_request, _timeout):
        raise _http_error(
            401,
            {
                "status": "error",
                "message": "Authentication credentials not found",
                "correlationId": "auth-1",
            },
        )

    client = HubSpotClient("bad-token", transport=transport)

    with pytest.raises(HubSpotAPIError) as raised:
        client.search("contacts", query="david")

    exc = raised.value
    assert exc.error_type == "auth_failed"
    assert exc.status_code == 401
    assert "Authentication credentials" in exc.message


def test_handler_reports_missing_credentials(monkeypatch):
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)

    result = json.loads(hubspot_crm({"action": "auth_check"}))

    assert result["ok"] is False
    assert result["error_type"] == "missing_credentials"
    assert result["required_env"] == ["HUBSPOT_ACCESS_TOKEN"]


def test_auth_check_reports_missing_recommended_scopes(monkeypatch):
    class FakeClient:
        client_secret = "secret"

        def access_token_metadata(self):
            return {
                "hub_id": 123,
                "user": "sales@example.com",
                "scopes": ["crm.objects.contacts.read"],
                "token_type": "access",
            }

    monkeypatch.setattr(hubspot_tool.HubSpotClient, "from_env", staticmethod(lambda: FakeClient()))

    result = json.loads(hubspot_crm({"action": "auth_check"}))

    assert result["ok"] is True
    assert result["authenticated"] is True
    assert result["client_secret_present"] is True
    assert result["client_secret_used"] is False
    assert result["ready_for_first_slice"] is False
    assert "crm.objects.tasks.read" in result["missing_recommended_scopes"]


def test_create_task_builds_hubspot_association(monkeypatch):
    calls = []

    class FakeClient:
        def create(self, object_type, properties, *, associations=None):
            calls.append(
                {
                    "object_type": object_type,
                    "properties": properties,
                    "associations": associations,
                }
            )
            return {"id": "task-1", "properties": properties}

    monkeypatch.setattr(hubspot_tool.HubSpotClient, "from_env", staticmethod(lambda: FakeClient()))

    result = json.loads(
        hubspot_crm(
            {
                "action": "create_task",
                "task_subject": "Call Acme",
                "task_body": "Ask about renewal timing.",
                "due_at": "2026-04-24T17:00:00Z",
                "task_type": "CALL",
                "associated_contact_id": "101",
            }
        )
    )

    assert result["ok"] is True
    assert calls == [
        {
            "object_type": "tasks",
            "properties": {
                "hs_task_subject": "Call Acme",
                "hs_timestamp": "2026-04-24T17:00:00Z",
                "hs_task_status": "NOT_STARTED",
                "hs_task_body": "Ask about renewal timing.",
                "hs_task_type": "CALL",
            },
            "associations": [
                {
                    "to": {"id": "101"},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 204,
                        }
                    ],
                }
            ],
        }
    ]


def test_sales_call_reminder_formats_call_tasks(monkeypatch):
    class FakeClient:
        def search(self, object_type, **kwargs):
            assert object_type == "tasks"
            assert kwargs["filters"][0] == {
                "propertyName": "hs_timestamp",
                "operator": "LTE",
                "value": "2026-04-24T18:00:00Z",
            }
            return {
                "results": [
                    {
                        "id": "1",
                        "properties": {
                            "hs_task_subject": "Call Acme",
                            "hs_task_body": "Follow up",
                            "hs_timestamp": "2026-04-24T17:00:00Z",
                            "hs_task_status": "NOT_STARTED",
                            "hs_task_type": "CALL",
                        },
                    },
                    {
                        "id": "2",
                        "properties": {
                            "hs_task_subject": "Email Beta",
                            "hs_timestamp": "2026-04-24T17:30:00Z",
                            "hs_task_status": "NOT_STARTED",
                            "hs_task_type": "EMAIL",
                        },
                    },
                ]
            }

    monkeypatch.setattr(hubspot_tool.HubSpotClient, "from_env", staticmethod(lambda: FakeClient()))

    result = json.loads(
        hubspot_crm(
            {
                "action": "sales_call_reminder",
                "due_before": "2026-04-24T18:00:00Z",
            }
        )
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert "Call Acme" in result["reminder_text"]
    assert "Email Beta" not in result["reminder_text"]


def test_hubspot_toolset_resolves_when_token_is_set(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "token")

    definitions = get_tool_definitions(enabled_toolsets=["hubspot"], quiet_mode=True)
    names = {tool["function"]["name"] for tool in definitions}

    assert "hubspot_crm" in names
