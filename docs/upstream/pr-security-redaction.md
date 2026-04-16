## Summary

Adds defense-in-depth secret redaction on persistence paths and tightens local
session artifact permissions.

### What changed
- `agent/redact.py`
  - introduces an always-on critical redaction tier for private keys, database
    credentials, and AWS access key IDs
  - keeps broader token/API-key masking under the existing redaction toggle
  - adds generic long-hex credential detection
- `hermes_state.py`
  - redacts message content before writing it to SQLite
- `agent/trajectory.py`
  - redacts trajectory content before writing JSONL samples
- `gateway/session.py`
  - writes `sessions.json` and transcript JSONL files with owner-only (`0600`)
    permissions

## Why

Hermes already tries to avoid emitting secrets in normal operation, but that is
not a sufficient guarantee for persistence. Tool output, copied credentials,
misconfigured prompts, or model mistakes can still place sensitive values on a
write path. This patch makes persistence defensive:

- secrets that reach the write path are redacted before they land in SQLite or
  trajectory logs
- local session artifacts are restricted to the owning user by default

That reduces the blast radius of accidental secret disclosure in databases,
transcripts, and local support artifacts.

## Threat model / attack surface

This addresses accidental persistence of:
- API keys and bearer tokens
- private key material
- database connection passwords
- long credential-like hex secrets

These values should not appear in model-visible content, but if they do, the
redaction layer prevents durable storage of the raw secret.

## Compatibility and performance

- backward compatible: no API changes
- low overhead: regex scanning runs only on write paths, not on every read
- session file permissions are standard POSIX owner-only permissions

## Testing

- `python -m pytest tests/agent/test_redact.py tests/agent/test_trajectory.py tests/test_hermes_state.py tests/gateway/test_session.py -q`
