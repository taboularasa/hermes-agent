# Zep Memory Provider

Zep-backed long-term memory for Hermes using Zep's current `user` + `thread` + `context` API.

## Requirements

- `pip install zep-cloud`
- Zep API key from [app.getzep.com](https://app.getzep.com)

## Setup

```bash
hermes memory setup    # select "zep"
```

Or manually:

```bash
hermes config set memory.provider zep
# Prefer Doppler-backed runtime env on this host; otherwise write the key into $HERMES_HOME/.env
export ZEP_API_KEY=***
```

Optional custom API URL:

```bash
export ZEP_API_URL=https://api.getzep.com
```

## Config

Config file: `$HERMES_HOME/zep.json`

| Key | Default | Description |
|-----|---------|-------------|
| `api_url` | `https://api.getzep.com` | Base Zep API URL. Hermes appends `/api/v2` for the SDK when needed. |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ZEP_API_KEY` | API key (required) |
| `ZEP_API_URL` | Optional API URL override |

## Behavior

When enabled, Hermes:

- creates one stable Zep user per Hermes profile/user scope
- creates one Zep thread per Hermes session
- mirrors built-in memory writes into a dedicated Zep notes thread
- injects Zep user-context recall before turns when relevant
- skips external reads and writes for non-primary contexts like cron, flush, and subagents

This provider is context-only. Hermes keeps its built-in memory tools, and Zep supplies the long-term recall layer underneath them.
