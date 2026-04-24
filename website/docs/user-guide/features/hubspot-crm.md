---
title: HubSpot CRM
description: Use Hermes Agent to manage HubSpot CRM records for outbound sales calling and Slack reminders.
sidebar_label: HubSpot CRM
---

# HubSpot CRM

Hermes Agent includes a `hubspot_crm` tool for a focused outbound sales workflow:

- search and upsert contacts
- search and upsert companies
- create notes
- create and update tasks
- log calls
- find due follow-up tasks and format sales-call reminder text

This is not a generic CRM abstraction. It is a narrow HubSpot surface for sales calling and follow-up work.

## Setup

Add HubSpot credentials to `~/.hermes/.env`:

```bash
HUBSPOT_ACCESS_TOKEN=pat-...
HUBSPOT_CLIENT_SECRET=...
```

`HUBSPOT_ACCESS_TOKEN` is required. It can be a HubSpot private app access token or an OAuth access token. Current API calls use it as the bearer token for HubSpot requests.

`HUBSPOT_CLIENT_SECRET` is optional for this first implementation. Hermes records it in setup/UI metadata for a future OAuth refresh flow, but the current tool does not use it.

The `hubspot` toolset is available when `HUBSPOT_ACCESS_TOKEN` is set. Default Hermes CLI and messaging toolsets include `hubspot_crm`, so Slack cron runs can use the same tool after the token is present.

## Required scopes

Grant these HubSpot scopes for the first implementation slice:

| Scope | Used for |
| --- | --- |
| `crm.objects.contacts.read` | Search contacts and find existing contacts before upsert |
| `crm.objects.contacts.write` | Create or update contacts |
| `crm.objects.companies.read` | Search companies and find existing companies before upsert |
| `crm.objects.companies.write` | Create or update companies |
| `crm.objects.notes.read` | Token readiness for the notes surface |
| `crm.objects.notes.write` | Create notes on CRM records |
| `crm.objects.tasks.read` | Find due follow-up tasks and sales-call reminders |
| `crm.objects.tasks.write` | Create and update tasks |
| `crm.objects.calls.read` | Token readiness for call-log retrieval support |
| `crm.objects.calls.write` | Log calls on CRM records |

For a public OAuth app install, include `oauth` as required by HubSpot's OAuth flow. For a private app token, configure the CRM scopes directly on the private app.

HubSpot association defaults used by the tool:

| Activity | Contact association | Company association |
| --- | --- | --- |
| Note | `202` | `190` |
| Task | `204` | `192` |
| Call | `194` | `182` |

If you use custom association labels, pass explicit `association_type_id` values in the `associations` argument.

## Tool actions

`hubspot_crm` is action-based. Supported actions:

| Action | Purpose |
| --- | --- |
| `auth_check` | Validate token metadata, show granted scopes, and report missing recommended scopes |
| `search_contacts` | Search contacts by query or exact email |
| `upsert_contact` | Update by `contact_id`, or create/update by email |
| `search_companies` | Search companies by query or exact domain |
| `upsert_company` | Update by `company_id`, update by domain when found, or create a company |
| `create_note` | Create a note and optionally associate it to a contact/company |
| `create_task` | Create a task and optionally associate it to a contact/company |
| `update_task` | Update task status, due date, body, subject, priority, owner, or raw properties |
| `log_call` | Create a HubSpot call activity and optionally associate it to a contact/company |
| `list_due_tasks` | Return open tasks due before a timestamp |
| `sales_call_reminder` | Return Slack-friendly reminder text for due call-like tasks |

Examples:

```text
Check whether HubSpot auth is ready.
```

```text
Find the HubSpot contact for david@example.com.
```

```text
Create a HubSpot CALL task due tomorrow at 9am for contact 12345.
```

```text
Log a completed outbound call for contact 12345 with notes from this conversation.
```

## Error handling

HubSpot failures are returned as structured JSON. Missing permissions and auth failures are not collapsed into opaque network errors.

Missing scopes look like:

```json
{
  "ok": false,
  "error_type": "missing_scopes",
  "message": "This oauth-token requires all of [crm.objects.tasks.read]",
  "missing_scopes": ["crm.objects.tasks.read"],
  "required_scopes": ["crm.objects.tasks.read"]
}
```

Auth failures use `error_type: "auth_failed"`. Missing local credentials use `error_type: "missing_credentials"`.

## Slack reminders

Use the `sales_call_reminder` action from a regular cron job, then send the returned `reminder_text` through the existing `send_message` tool.

Example cron prompt:

```text
Every weekday at 8:30am, check HubSpot for sales calls due before the end of today in America/Los_Angeles. Use hubspot_crm action=sales_call_reminder. Send the returned reminder_text to David in Slack with send_message. If there are no calls due, send the no-calls message.
```

Equivalent tool-level shape:

```python
cronjob(
    action="create",
    name="HubSpot sales call reminder",
    schedule="30 8 * * 1-5",
    prompt=(
        "Use hubspot_crm action=sales_call_reminder with due_before set to the "
        "end of today in America/Los_Angeles. Send reminder_text to David in Slack "
        "using send_message."
    ),
)
```

The gateway must be running with Slack configured so `send_message` can deliver the reminder.
