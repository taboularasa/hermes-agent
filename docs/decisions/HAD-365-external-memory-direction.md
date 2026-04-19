# HAD-365: External Memory Direction

## Context

Hermes's built-in durable memory is intentionally small and prompt-resident:

- `memory_char_limit`: 2200
- `user_char_limit`: 1375

That budget is useful for fast, curated facts, but it is not a practical long-term memory store for multi-session agent work. HAD-365 needs a durable direction that fits Hermes's existing memory-provider model without pretending a live integration exists when credentials are absent.

## Decision

Prefer the Zep Memory API as Hermes's default external-memory direction for user/session memory.

Do not treat Zep Docs MCP as a memory backend. Do not default to Graphiti for this use case. Graphiti is only the right default when the product requirement is explicitly graph-native search or reasoning over entities and edges.

## Why Zep Fits Hermes

Zep maps cleanly onto Hermes's existing runtime identifiers:

- Hermes `user_id` -> Zep user
- Hermes `session_id` -> Zep session
- Hermes turn sync -> Zep `memory.add`
- Hermes prefetch -> Zep `memory.get`

This matches Hermes's current provider lifecycle:

1. create or upsert the user
2. create or upsert the session
3. add chat messages as turns complete
4. retrieve relevant memory for the next prompt

The built-in `MEMORY.md` and `USER.md` stores should remain active as the fast local cache even when an external backend is enabled. External memory stays additive, not substitutive.

## Integration Posture

This change does not ship a bundled Zep memory provider.

Reason:

- the workstation does not currently have `ZEP_API_KEY`
- we should not merge a "live" provider path we cannot verify end to end
- the bounded, mergeable step is to land the decision plus config/docs hooks that make a later provider straightforward

## Safe Hooks Landed With HAD-365

- reserve `ZEP_API_KEY` in Hermes's optional env-var registry
- reserve `ZEP_BASE_URL` for self-hosted or non-default deployments
- document Zep as the preferred custom-provider direction when built-in memory is too small

## Follow-on Provider Shape

When a real Zep provider is implemented, it should:

1. read `ZEP_API_KEY` and optional `ZEP_BASE_URL`
2. use Hermes gateway `user_id` when available, otherwise fall back to a profile-scoped local default
3. create a Zep session per Hermes session
4. write turns asynchronously after each completed response
5. use `memory.get` for prefetch, not raw graph APIs by default
6. keep graph-specific search as an optional extension, not the baseline path

## Non-Goals

- bundling Graphiti as the default durable-memory answer
- inventing a second always-on prompt memory alongside Hermes built-in memory
- faking a provider activation path without credentials or verified transport behavior
