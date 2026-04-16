## Summary

Hardens a few high-leverage input and startup safety paths without changing
normal workflows or adding new required configuration.

### What changed
- `tools/url_safety.py`
  - rejects non-`http`/`https` URL schemes before DNS resolution or fetch
- `tools/file_tools.py`
  - blocks reads of common host credential locations by default
  - allows an explicit `HERMES_ALLOW_SENSITIVE_READS=true` override for local
    debugging workflows
- `tools/tirith_security.py`
  - changes the default scanner posture to fail-closed on operational failures
- `gateway/builtin_hooks/boot_md.py`
  - adds optional `BOOT_MD_SHA256` integrity verification before BOOT.md runs

## Why

These are small guardrails that close common security gaps:

- URL scheme restriction prevents accidental `file://` or similar local reads
  through fetch-like paths
- file read deny-lists reduce the chance that an agent can exfiltrate host
  credentials from standard locations such as `~/.ssh/` or `.env`
- fail-closed scanner defaults ensure missing Tirith coverage blocks rather
  than silently allowing
- BOOT.md integrity checking gives operators an opt-in way to pin the exact
  startup instructions that should run

## Threat model / attack surface

This is aimed at defense against:
- SSRF-style local file access via alternate URL schemes
- inadvertent exposure of workstation credentials to the model
- silent degradation when the command scanner is unavailable
- tampering with BOOT.md startup instructions on disk

## Compatibility

- backward compatible for standard `http`/`https` fetches
- sensitive file reads can still be explicitly re-enabled with an env var
- BOOT.md integrity verification is opt-in; if `BOOT_MD_SHA256` is unset,
  startup behavior is unchanged

## Testing

- `python -m pytest tests/tools/test_url_safety.py tests/tools/test_file_read_guards.py tests/tools/test_tirith_security.py tests/gateway/test_boot_md.py -q`
