"""HubSpot CRM tool for outbound sales workflows.

The first slice is intentionally narrow: contacts, companies, notes, tasks,
call logging, auth diagnostics, and reminder-ready due task lookup.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.registry import registry, tool_error

HUBSPOT_API_BASE_URL = "https://api.hubapi.com"

_SCOPE_RE = re.compile(r"(?<![a-z0-9_.-])(?:crm\.[a-z0-9_.-]+|oauth)(?![a-z0-9_.-])")

FIRST_SLICE_SCOPES = [
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.companies.read",
    "crm.objects.companies.write",
    "crm.objects.notes.read",
    "crm.objects.notes.write",
    "crm.objects.tasks.read",
    "crm.objects.tasks.write",
    "crm.objects.calls.read",
    "crm.objects.calls.write",
]

ACTION_REQUIRED_SCOPES = {
    "auth_check": [],
    "search_contacts": ["crm.objects.contacts.read"],
    "upsert_contact": ["crm.objects.contacts.read", "crm.objects.contacts.write"],
    "search_companies": ["crm.objects.companies.read"],
    "upsert_company": ["crm.objects.companies.read", "crm.objects.companies.write"],
    "create_note": ["crm.objects.notes.write"],
    "create_task": ["crm.objects.tasks.write"],
    "update_task": ["crm.objects.tasks.write"],
    "log_call": ["crm.objects.calls.write"],
    "list_due_tasks": ["crm.objects.tasks.read"],
    "sales_call_reminder": ["crm.objects.tasks.read"],
}

DEFAULT_PROPERTIES = {
    "contacts": ["email", "firstname", "lastname", "phone", "company", "lifecyclestage"],
    "companies": ["name", "domain", "phone", "city", "state", "industry", "lifecyclestage"],
    "tasks": [
        "hs_task_subject",
        "hs_task_body",
        "hs_timestamp",
        "hs_task_status",
        "hs_task_priority",
        "hs_task_type",
        "hubspot_owner_id",
    ],
    "notes": ["hs_note_body", "hs_timestamp", "hubspot_owner_id"],
    "calls": [
        "hs_call_title",
        "hs_call_body",
        "hs_timestamp",
        "hs_call_direction",
        "hs_call_status",
        "hs_call_duration",
        "hs_call_from_number",
        "hs_call_to_number",
        "hubspot_owner_id",
    ],
}

# HubSpot-defined association type IDs for the direction activity -> object.
ACTIVITY_ASSOCIATION_TYPE_IDS = {
    ("notes", "contact"): 202,
    ("notes", "contacts"): 202,
    ("notes", "company"): 190,
    ("notes", "companies"): 190,
    ("tasks", "contact"): 204,
    ("tasks", "contacts"): 204,
    ("tasks", "company"): 192,
    ("tasks", "companies"): 192,
    ("calls", "contact"): 194,
    ("calls", "contacts"): 194,
    ("calls", "company"): 182,
    ("calls", "companies"): 182,
}


class HubSpotConfigError(RuntimeError):
    """Raised when local HubSpot configuration is incomplete."""


class HubSpotAPIError(RuntimeError):
    """Structured HubSpot API failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        category: Optional[str] = None,
        correlation_id: Optional[str] = None,
        missing_scopes: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.category = category
        self.correlation_id = correlation_id
        self.missing_scopes = missing_scopes or []
        self.payload = payload or {}

    @property
    def error_type(self) -> str:
        category = (self.category or "").upper()
        if self.status_code in {401, 407} or "AUTH" in category:
            return "auth_failed"
        if self.missing_scopes or "SCOPE" in category or self.status_code == 403:
            return "missing_scopes"
        if self.status_code == 429:
            return "rate_limited"
        return "hubspot_error"

    def to_result(self, *, required_scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": False,
            "error_type": self.error_type,
            "message": self.message,
            "status_code": self.status_code,
        }
        if self.category:
            result["category"] = self.category
        if self.correlation_id:
            result["correlation_id"] = self.correlation_id
        if self.missing_scopes:
            result["missing_scopes"] = self.missing_scopes
        if required_scopes:
            result["required_scopes"] = required_scopes
        return result


Transport = Callable[[urllib.request.Request, float], Tuple[int, bytes]]


def _default_transport(request: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.getcode(), response.read()


class HubSpotClient:
    """Small synchronous HubSpot CRM client."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        *,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 20.0,
        transport: Optional[Transport] = None,
    ) -> None:
        self.access_token = access_token or ""
        self.client_secret = client_secret or ""
        self.base_url = (base_url or HUBSPOT_API_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.transport = transport or _default_transport

    @classmethod
    def from_env(cls) -> "HubSpotClient":
        return cls(
            os.getenv("HUBSPOT_ACCESS_TOKEN", ""),
            client_secret=os.getenv("HUBSPOT_CLIENT_SECRET", ""),
            base_url=os.getenv("HUBSPOT_API_BASE_URL", HUBSPOT_API_BASE_URL),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        include_auth: bool = True,
    ) -> Dict[str, Any]:
        if include_auth and not self.access_token:
            raise HubSpotConfigError("HUBSPOT_ACCESS_TOKEN is not set")

        url = self._build_url(path, params)
        headers = {
            "Accept": "application/json",
            "User-Agent": "hermes-agent-hubspot/1.0",
        }
        if include_auth:
            headers["Authorization"] = f"Bearer {self.access_token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            _status, raw = self.transport(request, self.timeout)
        except urllib.error.HTTPError as exc:
            raise self._api_error_from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise HubSpotAPIError(
                f"HubSpot network error: {getattr(exc, 'reason', exc)}",
                category="NETWORK_ERROR",
            ) from exc

        return _decode_json(raw)

    def _build_url(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params, doseq=True)}"
        return url

    def _api_error_from_http_error(self, exc: urllib.error.HTTPError) -> HubSpotAPIError:
        raw = exc.read()
        payload = _decode_json(raw)
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        message = (
            payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or exc.reason
            or f"HubSpot API returned HTTP {exc.code}"
        )
        details = payload.get("errors")
        scope_text = f"{message} {json.dumps(details, ensure_ascii=False) if details else ''}"
        return HubSpotAPIError(
            str(message),
            status_code=exc.code,
            category=payload.get("category") or payload.get("status"),
            correlation_id=payload.get("correlationId"),
            missing_scopes=_extract_scopes(scope_text),
            payload=payload,
        )

    def access_token_metadata(self) -> Dict[str, Any]:
        if not self.access_token:
            raise HubSpotConfigError("HUBSPOT_ACCESS_TOKEN is not set")
        token = urllib.parse.quote(self.access_token, safe="")
        return self.request("GET", f"/oauth/v1/access-tokens/{token}", include_auth=False)

    def search(
        self,
        object_type: str,
        *,
        query: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        properties: Optional[List[str]] = None,
        limit: int = 10,
        sorts: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"limit": _limit(limit)}
        if query:
            payload["query"] = query
        if filters:
            payload["filterGroups"] = [{"filters": filters}]
        if properties:
            payload["properties"] = properties
        if sorts:
            payload["sorts"] = sorts
        return self.request("POST", f"/crm/v3/objects/{object_type}/search", body=payload)

    def create(
        self,
        object_type: str,
        properties: Dict[str, Any],
        *,
        associations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"properties": properties}
        if associations:
            payload["associations"] = associations
        return self.request("POST", f"/crm/v3/objects/{object_type}", body=payload)

    def update(self, object_type: str, object_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.request(
            "PATCH",
            f"/crm/v3/objects/{object_type}/{urllib.parse.quote(str(object_id), safe='')}",
            body={"properties": properties},
        )


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _extract_scopes(text: str) -> List[str]:
    return sorted(set(_SCOPE_RE.findall(text or "")))


def _check_hubspot_available() -> bool:
    return bool(os.getenv("HUBSPOT_ACCESS_TOKEN"))


def _limit(value: Any, default: int = 10, maximum: int = 200) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _clean_properties(properties: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(properties, dict):
        return {}
    return {str(key): value for key, value in properties.items() if value is not None}


def _add_if_present(properties: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "":
        properties[key] = value


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _association(
    to_id: Any,
    association_type_id: Any,
    *,
    association_category: str = "HUBSPOT_DEFINED",
) -> Dict[str, Any]:
    return {
        "to": {"id": str(to_id)},
        "types": [
            {
                "associationCategory": association_category,
                "associationTypeId": int(association_type_id),
            }
        ],
    }


def _build_activity_associations(activity_type: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    associations: List[Dict[str, Any]] = []

    for contact_id in _as_list(args.get("associated_contact_ids") or args.get("associated_contact_id")):
        associations.append(_association(contact_id, ACTIVITY_ASSOCIATION_TYPE_IDS[(activity_type, "contact")]))
    for company_id in _as_list(args.get("associated_company_ids") or args.get("associated_company_id")):
        associations.append(_association(company_id, ACTIVITY_ASSOCIATION_TYPE_IDS[(activity_type, "company")]))

    raw_associations = args.get("associations")
    if isinstance(raw_associations, list):
        for item in raw_associations:
            if not isinstance(item, dict):
                raise ValueError("associations entries must be objects")
            if "to" in item and "types" in item:
                associations.append(item)
                continue

            to_id = item.get("object_id") or item.get("to_object_id") or item.get("id")
            object_type = item.get("object_type") or item.get("to_object_type")
            association_type_id = item.get("association_type_id") or item.get("associationTypeId")
            category = item.get("association_category") or item.get("associationCategory") or "HUBSPOT_DEFINED"
            if not to_id:
                raise ValueError("association object_id is required")
            if not association_type_id:
                if not object_type:
                    raise ValueError("association object_type or association_type_id is required")
                association_type_id = ACTIVITY_ASSOCIATION_TYPE_IDS.get((activity_type, str(object_type).lower()))
            if not association_type_id:
                raise ValueError(
                    f"No default association type ID for {activity_type} -> {object_type}; "
                    "pass association_type_id explicitly"
                )
            associations.append(
                _association(to_id, association_type_id, association_category=str(category))
            )

    return associations


def _properties_arg(args: Dict[str, Any], object_type: str) -> List[str]:
    raw = args.get("properties_to_return")
    if raw is None:
        raw = args.get("return_properties")
    if raw is None:
        raw = args.get("properties")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return DEFAULT_PROPERTIES.get(object_type, [])


def _success(action: str, **payload: Any) -> str:
    return json.dumps({"ok": True, "action": action, **payload}, ensure_ascii=False)


def _failure(
    error_type: str,
    message: str,
    *,
    required_scopes: Optional[List[str]] = None,
    **extra: Any,
) -> str:
    payload: Dict[str, Any] = {"ok": False, "error_type": error_type, "message": message}
    if required_scopes:
        payload["required_scopes"] = required_scopes
    payload.update({key: value for key, value in extra.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False)


def _api_failure(exc: HubSpotAPIError, action: str) -> str:
    result = exc.to_result(required_scopes=ACTION_REQUIRED_SCOPES.get(action))
    result["action"] = action
    return json.dumps(result, ensure_ascii=False)


def _auth_check(client: HubSpotClient) -> str:
    metadata = client.access_token_metadata()
    granted_scopes = sorted(set(metadata.get("scopes") or []))
    missing = [scope for scope in FIRST_SLICE_SCOPES if scope not in granted_scopes]
    return _success(
        "auth_check",
        authenticated=True,
        hub_id=metadata.get("hub_id"),
        hub_domain=metadata.get("hub_domain"),
        user=metadata.get("user"),
        user_id=metadata.get("user_id"),
        app_id=metadata.get("app_id"),
        token_type=metadata.get("token_type"),
        expires_in=metadata.get("expires_in"),
        granted_scopes=granted_scopes,
        missing_recommended_scopes=missing,
        ready_for_first_slice=not missing,
        client_secret_present=bool(client.client_secret),
        client_secret_used=False,
        credential_note=(
            "Current HubSpot CRM calls authenticate with HUBSPOT_ACCESS_TOKEN as a Bearer token. "
            "HUBSPOT_CLIENT_SECRET is tracked for a future OAuth refresh flow and is not used by this tool yet."
        ),
    )


def _search_contacts(client: HubSpotClient, args: Dict[str, Any]) -> str:
    email = args.get("email")
    filters = [{"propertyName": "email", "operator": "EQ", "value": email}] if email else None
    data = client.search(
        "contacts",
        query=None if email else args.get("query"),
        filters=filters,
        properties=_properties_arg(args, "contacts"),
        limit=_limit(args.get("limit")),
    )
    return _success("search_contacts", result=data)


def _upsert_contact(client: HubSpotClient, args: Dict[str, Any]) -> str:
    contact_id = args.get("contact_id") or args.get("id")
    properties = _clean_properties(args.get("properties"))
    _add_if_present(properties, "email", args.get("email"))
    _add_if_present(properties, "firstname", args.get("first_name") or args.get("firstname"))
    _add_if_present(properties, "lastname", args.get("last_name") or args.get("lastname"))
    _add_if_present(properties, "phone", args.get("phone"))
    _add_if_present(properties, "company", args.get("company"))

    if not properties:
        return _failure("validation_error", "At least one contact property is required")

    if contact_id:
        data = client.update("contacts", str(contact_id), properties)
        return _success("upsert_contact", operation="updated", result=data)

    email = properties.get("email")
    if not email:
        return _failure("validation_error", "email or contact_id is required for contact upsert")

    existing = client.search(
        "contacts",
        filters=[{"propertyName": "email", "operator": "EQ", "value": email}],
        properties=["email"],
        limit=1,
    )
    results = existing.get("results") or []
    if results:
        data = client.update("contacts", results[0]["id"], properties)
        return _success("upsert_contact", operation="updated", result=data)

    data = client.create("contacts", properties)
    return _success("upsert_contact", operation="created", result=data)


def _search_companies(client: HubSpotClient, args: Dict[str, Any]) -> str:
    domain = args.get("domain")
    filters = [{"propertyName": "domain", "operator": "EQ", "value": domain}] if domain else None
    data = client.search(
        "companies",
        query=None if domain else (args.get("query") or args.get("name")),
        filters=filters,
        properties=_properties_arg(args, "companies"),
        limit=_limit(args.get("limit")),
    )
    return _success("search_companies", result=data)


def _upsert_company(client: HubSpotClient, args: Dict[str, Any]) -> str:
    company_id = args.get("company_id") or args.get("id")
    properties = _clean_properties(args.get("properties"))
    _add_if_present(properties, "name", args.get("name"))
    _add_if_present(properties, "domain", args.get("domain"))
    _add_if_present(properties, "phone", args.get("phone"))
    _add_if_present(properties, "city", args.get("city"))
    _add_if_present(properties, "state", args.get("state"))
    _add_if_present(properties, "industry", args.get("industry"))

    if not properties:
        return _failure("validation_error", "At least one company property is required")

    if company_id:
        data = client.update("companies", str(company_id), properties)
        return _success("upsert_company", operation="updated", result=data)

    domain = properties.get("domain")
    if domain:
        existing = client.search(
            "companies",
            filters=[{"propertyName": "domain", "operator": "EQ", "value": domain}],
            properties=["domain"],
            limit=1,
        )
        results = existing.get("results") or []
        if results:
            data = client.update("companies", results[0]["id"], properties)
            return _success("upsert_company", operation="updated", result=data)

    data = client.create("companies", properties)
    return _success("upsert_company", operation="created", result=data)


def _create_note(client: HubSpotClient, args: Dict[str, Any]) -> str:
    body = args.get("note_body") or args.get("body")
    if not body:
        return _failure("validation_error", "note_body is required")
    properties = _clean_properties(args.get("properties"))
    properties["hs_note_body"] = body
    properties.setdefault("hs_timestamp", args.get("timestamp") or _utc_now())
    _add_if_present(properties, "hubspot_owner_id", args.get("owner_id") or args.get("hubspot_owner_id"))
    data = client.create("notes", properties, associations=_build_activity_associations("notes", args))
    return _success("create_note", result=data)


def _create_task(client: HubSpotClient, args: Dict[str, Any]) -> str:
    subject = args.get("task_subject") or args.get("subject")
    due_at = args.get("due_at") or args.get("timestamp")
    if not subject:
        return _failure("validation_error", "task_subject is required")
    if not due_at:
        return _failure("validation_error", "due_at is required")

    properties = _clean_properties(args.get("properties"))
    properties["hs_task_subject"] = subject
    properties["hs_timestamp"] = due_at
    properties.setdefault("hs_task_status", args.get("status") or "NOT_STARTED")
    _add_if_present(properties, "hs_task_body", args.get("task_body") or args.get("body"))
    _add_if_present(properties, "hs_task_priority", args.get("priority"))
    _add_if_present(properties, "hs_task_type", args.get("task_type"))
    _add_if_present(properties, "hubspot_owner_id", args.get("owner_id") or args.get("hubspot_owner_id"))
    data = client.create("tasks", properties, associations=_build_activity_associations("tasks", args))
    return _success("create_task", result=data)


def _update_task(client: HubSpotClient, args: Dict[str, Any]) -> str:
    task_id = args.get("task_id") or args.get("id")
    if not task_id:
        return _failure("validation_error", "task_id is required")
    properties = _clean_properties(args.get("properties"))
    _add_if_present(properties, "hs_task_status", args.get("status"))
    _add_if_present(properties, "hs_timestamp", args.get("due_at") or args.get("timestamp"))
    _add_if_present(properties, "hs_task_subject", args.get("task_subject") or args.get("subject"))
    _add_if_present(properties, "hs_task_body", args.get("task_body") or args.get("body"))
    _add_if_present(properties, "hs_task_priority", args.get("priority"))
    _add_if_present(properties, "hs_task_type", args.get("task_type"))
    _add_if_present(properties, "hubspot_owner_id", args.get("owner_id") or args.get("hubspot_owner_id"))
    if not properties:
        return _failure("validation_error", "At least one task property is required")
    data = client.update("tasks", str(task_id), properties)
    return _success("update_task", result=data)


def _log_call(client: HubSpotClient, args: Dict[str, Any]) -> str:
    properties = _clean_properties(args.get("properties"))
    properties["hs_timestamp"] = args.get("timestamp") or _utc_now()
    properties.setdefault("hs_call_status", args.get("status") or "COMPLETED")
    properties.setdefault("hs_call_direction", args.get("direction") or "OUTBOUND")
    _add_if_present(properties, "hs_call_title", args.get("call_title") or args.get("title"))
    _add_if_present(properties, "hs_call_body", args.get("call_body") or args.get("body"))
    _add_if_present(properties, "hs_call_duration", args.get("duration_ms") or args.get("call_duration_ms"))
    _add_if_present(properties, "hs_call_from_number", args.get("from_number"))
    _add_if_present(properties, "hs_call_to_number", args.get("to_number"))
    _add_if_present(properties, "hubspot_owner_id", args.get("owner_id") or args.get("hubspot_owner_id"))
    data = client.create("calls", properties, associations=_build_activity_associations("calls", args))
    return _success("log_call", result=data)


def _list_due_tasks(client: HubSpotClient, args: Dict[str, Any], *, reminder: bool = False) -> str:
    due_before = args.get("due_before") or args.get("before") or _utc_now()
    filters = [{"propertyName": "hs_timestamp", "operator": "LTE", "value": due_before}]
    if args.get("due_after") or args.get("after"):
        filters.append(
            {
                "propertyName": "hs_timestamp",
                "operator": "GTE",
                "value": args.get("due_after") or args.get("after"),
            }
        )
    if not bool(args.get("include_completed", False)):
        filters.append({"propertyName": "hs_task_status", "operator": "NEQ", "value": "COMPLETED"})
    if args.get("owner_id") or args.get("hubspot_owner_id"):
        filters.append(
            {
                "propertyName": "hubspot_owner_id",
                "operator": "EQ",
                "value": args.get("owner_id") or args.get("hubspot_owner_id"),
            }
        )

    data = client.search(
        "tasks",
        filters=filters,
        properties=DEFAULT_PROPERTIES["tasks"],
        limit=_limit(args.get("limit"), default=25, maximum=200),
        sorts=["hs_timestamp"],
    )
    tasks = [_task_summary(task) for task in data.get("results") or []]
    call_only = bool(args.get("call_only", reminder))
    if call_only:
        tasks = [task for task in tasks if _is_call_task(task)]

    action = "sales_call_reminder" if reminder else "list_due_tasks"
    payload: Dict[str, Any] = {
        "tasks": tasks,
        "count": len(tasks),
        "due_before": due_before,
        "include_completed": bool(args.get("include_completed", False)),
        "call_only": call_only,
    }
    if reminder:
        payload["reminder_text"] = _format_sales_reminder(tasks, due_before=due_before)
    return _success(action, **payload)


def _task_summary(task: Dict[str, Any]) -> Dict[str, Any]:
    props = task.get("properties") or {}
    return {
        "id": task.get("id"),
        "subject": props.get("hs_task_subject"),
        "body": props.get("hs_task_body"),
        "due_at": props.get("hs_timestamp"),
        "status": props.get("hs_task_status"),
        "priority": props.get("hs_task_priority"),
        "task_type": props.get("hs_task_type"),
        "owner_id": props.get("hubspot_owner_id"),
        "archived": task.get("archived", False),
    }


def _is_call_task(task: Dict[str, Any]) -> bool:
    task_type = str(task.get("task_type") or "").upper()
    subject = str(task.get("subject") or "").lower()
    body = str(task.get("body") or "").lower()
    return task_type == "CALL" or "call" in subject or "call" in body


def _format_sales_reminder(tasks: List[Dict[str, Any]], *, due_before: str) -> str:
    if not tasks:
        return f"No HubSpot sales calls are due before {due_before}."

    lines = [f"*HubSpot sales calls due before {due_before}*"]
    for task in tasks[:20]:
        due = task.get("due_at") or "no due date"
        subject = task.get("subject") or "Untitled task"
        task_id = task.get("id") or "unknown id"
        priority = f" [{task['priority']}]" if task.get("priority") else ""
        lines.append(f"- {due}: {subject}{priority} (task {task_id})")
    if len(tasks) > 20:
        lines.append(f"- ...and {len(tasks) - 20} more.")
    return "\n".join(lines)


ACTION_HANDLERS = {
    "auth_check": lambda client, args: _auth_check(client),
    "search_contacts": _search_contacts,
    "upsert_contact": _upsert_contact,
    "search_companies": _search_companies,
    "upsert_company": _upsert_company,
    "create_note": _create_note,
    "create_task": _create_task,
    "update_task": _update_task,
    "log_call": _log_call,
    "list_due_tasks": lambda client, args: _list_due_tasks(client, args, reminder=False),
    "sales_call_reminder": lambda client, args: _list_due_tasks(client, args, reminder=True),
}


def hubspot_crm(args: Dict[str, Any], **_kw: Any) -> str:
    action = str(args.get("action") or "").strip()
    if not action:
        return tool_error("Missing required parameter: action")
    if action not in ACTION_HANDLERS:
        return _failure(
            "validation_error",
            f"Unsupported HubSpot CRM action: {action}",
            supported_actions=sorted(ACTION_HANDLERS),
        )

    try:
        client = HubSpotClient.from_env()
        return ACTION_HANDLERS[action](client, args)
    except HubSpotConfigError as exc:
        return _failure(
            "missing_credentials",
            str(exc),
            required_env=["HUBSPOT_ACCESS_TOKEN"],
            client_secret_note="HUBSPOT_CLIENT_SECRET is optional in the current implementation.",
        )
    except HubSpotAPIError as exc:
        return _api_failure(exc, action)
    except ValueError as exc:
        return _failure("validation_error", str(exc), required_scopes=ACTION_REQUIRED_SCOPES.get(action))


HUBSPOT_CRM_SCHEMA = {
    "name": "hubspot_crm",
    "description": (
        "Manage the first-class HubSpot CRM sales surface: auth checks, contacts, companies, "
        "notes, tasks, call logging, and reminder-ready due sales call lookup. "
        "Uses HUBSPOT_ACCESS_TOKEN; HUBSPOT_CLIENT_SECRET is recorded for future OAuth refresh flows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(ACTION_HANDLERS),
                "description": "HubSpot CRM operation to run.",
            },
            "query": {"type": "string", "description": "Search query for contacts or companies."},
            "id": {"type": "string", "description": "Generic HubSpot record ID for update actions."},
            "contact_id": {"type": "string", "description": "HubSpot contact ID."},
            "company_id": {"type": "string", "description": "HubSpot company ID."},
            "task_id": {"type": "string", "description": "HubSpot task ID."},
            "email": {"type": "string", "description": "Contact email for search/upsert."},
            "first_name": {"type": "string", "description": "Contact first name."},
            "last_name": {"type": "string", "description": "Contact last name."},
            "phone": {"type": "string", "description": "Phone number for contacts or companies."},
            "name": {"type": "string", "description": "Company name."},
            "domain": {"type": "string", "description": "Company domain for exact search/upsert."},
            "note_body": {"type": "string", "description": "Body text for create_note."},
            "task_subject": {"type": "string", "description": "Task title for create_task/update_task."},
            "task_body": {"type": "string", "description": "Task notes."},
            "task_type": {"type": "string", "description": "Optional HubSpot task type, e.g. CALL or TODO."},
            "call_title": {"type": "string", "description": "Call title for log_call."},
            "call_body": {"type": "string", "description": "Call notes for log_call."},
            "timestamp": {"type": "string", "description": "HubSpot timestamp as ISO UTC or epoch milliseconds."},
            "due_at": {"type": "string", "description": "Task due timestamp as ISO UTC or epoch milliseconds."},
            "due_before": {"type": "string", "description": "Latest due timestamp for due-task lookup."},
            "due_after": {"type": "string", "description": "Earliest due timestamp for due-task lookup."},
            "status": {"type": "string", "description": "Task or call status."},
            "priority": {"type": "string", "description": "HubSpot task priority, e.g. HIGH."},
            "owner_id": {"type": "string", "description": "HubSpot owner/user ID."},
            "direction": {"type": "string", "description": "Call direction, e.g. OUTBOUND or INBOUND."},
            "duration_ms": {"type": "string", "description": "Call duration in milliseconds."},
            "from_number": {"type": "string", "description": "Call source phone number."},
            "to_number": {"type": "string", "description": "Call destination phone number."},
            "associated_contact_id": {
                "type": "string",
                "description": "Contact ID to associate with a note, task, or call.",
            },
            "associated_company_id": {
                "type": "string",
                "description": "Company ID to associate with a note, task, or call.",
            },
            "associations": {
                "type": "array",
                "description": (
                    "Optional HubSpot association specs. Each item may be HubSpot's native "
                    "{to, types} shape or {object_type, object_id, association_type_id}."
                ),
                "items": {"type": "object", "additionalProperties": True},
            },
            "properties": {
                "type": "object",
                "description": "Raw HubSpot property map to merge into create/update requests.",
                "additionalProperties": True,
            },
            "properties_to_return": {
                "type": "array",
                "description": "Optional property names to include in search responses.",
                "items": {"type": "string"},
            },
            "limit": {"type": "integer", "description": "Maximum records to return, 1-200."},
            "include_completed": {"type": "boolean", "description": "Include completed tasks in due lookup."},
            "call_only": {"type": "boolean", "description": "Filter due task lookup to call-like tasks."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="hubspot_crm",
    toolset="hubspot",
    schema=HUBSPOT_CRM_SCHEMA,
    handler=hubspot_crm,
    check_fn=_check_hubspot_available,
    requires_env=["HUBSPOT_ACCESS_TOKEN"],
    description="HubSpot CRM contacts, companies, notes, tasks, call logs, and due sales call reminders.",
)
