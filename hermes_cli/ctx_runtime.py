"""ctx-native runtime helpers for Hermes coding sessions.

This module binds Hermes coding sessions to ctx-managed tasks and worktrees.
It is intentionally conservative:

- Only sessions with coding-capable toolsets are eligible by default.
- ACP/editor sessions are excluded because they already carry an explicit cwd.
- ctx session creation is optional and only happens when a configured provider
  is explicitly supported by the daemon.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib import error, request

from hermes_cli.config import get_hermes_home, load_config
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_BINDINGS_LOCK = threading.Lock()
_DEFAULT_DAEMON_URL = "http://127.0.0.1:19876"
_DEFAULT_DATA_DIR = "~/.ctx-data"


@dataclass
class CtxBinding:
    """Resolved ctx binding for a Hermes session."""

    active: bool
    reason: str
    session_id: str
    platform: str = ""
    repo_root: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_root: Optional[str] = None
    task_id: Optional[str] = None
    worktree_id: Optional[str] = None
    worktree_path: Optional[str] = None
    ctx_session_id: Optional[str] = None
    ctx_session_provider_id: Optional[str] = None
    ctx_session_model_id: Optional[str] = None
    daemon_url: Optional[str] = None
    source: str = "none"

    @property
    def signature(self) -> str:
        if not self.active:
            return f"inactive:{self.reason}"
        parts = [
            self.workspace_id or "",
            self.task_id or "",
            self.worktree_id or "",
            self.ctx_session_id or "",
            self.ctx_session_provider_id or "",
            self.ctx_session_model_id or "",
        ]
        return "|".join(parts)

    def system_prompt_note(self) -> str:
        if not self.active:
            return ""
        details = [
            f"ctx task `{self.task_id}`",
            f"workspace `{self.workspace_id}`",
            f"worktree `{self.worktree_path}`",
        ]
        if self.ctx_session_id:
            details.append(
                f"ctx session `{self.ctx_session_id}`"
            )
        return (
            "[System note: This coding session is attached to "
            + ", ".join(details)
            + ". Use the ctx-managed worktree as the source of truth. "
            "Do not create a nested Hermes worktree.]"
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ctx_bindings_path() -> Path:
    return get_hermes_home() / "ctx" / "session_bindings.json"


def _load_bindings() -> Dict[str, Any]:
    path = _ctx_bindings_path()
    if not path.exists():
        return {"version": 1, "sessions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read ctx bindings from %s", path, exc_info=True)
        return {"version": 1, "sessions": {}}
    if not isinstance(data, dict):
        return {"version": 1, "sessions": {}}
    data.setdefault("version", 1)
    data.setdefault("sessions", {})
    if not isinstance(data["sessions"], dict):
        data["sessions"] = {}
    return data


def _save_bindings(data: Dict[str, Any]) -> None:
    atomic_json_write(_ctx_bindings_path(), data)


def _load_binding_record(session_id: str) -> Optional[Dict[str, Any]]:
    with _BINDINGS_LOCK:
        data = _load_bindings()
        record = data.get("sessions", {}).get(session_id)
        return dict(record) if isinstance(record, dict) else None


def _persist_binding(binding: CtxBinding) -> None:
    with _BINDINGS_LOCK:
        data = _load_bindings()
        sessions = data.setdefault("sessions", {})
        record = asdict(binding)
        record["updated_at"] = _utcnow_iso()
        existing = sessions.get(binding.session_id)
        if isinstance(existing, dict) and "created_at" in existing:
            record["created_at"] = existing["created_at"]
        else:
            record["created_at"] = record["updated_at"]
        sessions[binding.session_id] = record
        _save_bindings(data)


def describe_existing_ctx_binding(session_id: str) -> Optional[CtxBinding]:
    record = _load_binding_record(session_id)
    if not record:
        return None
    return _binding_from_record(record, reason=str(record.get("reason") or "existing"))


def _binding_from_record(record: Dict[str, Any], *, reason: str) -> CtxBinding:
    return CtxBinding(
        active=bool(record.get("active")),
        reason=reason,
        session_id=str(record.get("session_id") or ""),
        platform=str(record.get("platform") or ""),
        repo_root=record.get("repo_root"),
        workspace_id=record.get("workspace_id"),
        workspace_root=record.get("workspace_root"),
        task_id=record.get("task_id"),
        worktree_id=record.get("worktree_id"),
        worktree_path=record.get("worktree_path"),
        ctx_session_id=record.get("ctx_session_id"),
        ctx_session_provider_id=record.get("ctx_session_provider_id"),
        ctx_session_model_id=record.get("ctx_session_model_id"),
        daemon_url=record.get("daemon_url"),
        source=str(record.get("source") or "record"),
    )


def _normalize_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return str(Path(path).expanduser())


def _path_within_root(path: Optional[str], root: Optional[str]) -> bool:
    if not path or not root:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def _guess_repo_root(candidate: Optional[str]) -> Optional[str]:
    start = _normalize_path(candidate) or _normalize_path(os.getenv("TERMINAL_CWD")) or _normalize_path(os.getcwd())
    if not start:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return _normalize_path(result.stdout.strip())
    except Exception:
        pass
    return _normalize_path(start)


def _load_ctx_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or load_config()
    raw = cfg.get("ctx", {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "coding_mode": str(raw.get("coding_mode", "auto") or "auto").strip().lower(),
        "coding_toolsets": [
            str(item).strip()
            for item in (raw.get("coding_toolsets") or ["terminal", "file", "code_execution"])
            if str(item).strip()
        ],
        "daemon_url": str(raw.get("daemon_url") or "").strip(),
        "auth_token": str(raw.get("auth_token") or "").strip(),
        "workspace_id": str(raw.get("workspace_id") or "").strip(),
        "data_dir": str(raw.get("data_dir") or _DEFAULT_DATA_DIR).strip(),
        "session_provider_id": str(raw.get("session_provider_id") or "").strip(),
        "session_model_id": str(raw.get("session_model_id") or "").strip(),
        "session_execution_environment": str(raw.get("session_execution_environment") or "host").strip(),
    }


def is_ctx_candidate(
    *,
    enabled_toolsets: Optional[Iterable[str]],
    platform: Optional[str],
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    ctx_cfg = _load_ctx_config(config)
    if not ctx_cfg["enabled"]:
        return False
    if str(platform or "").strip().lower() == "acp":
        return False

    mode = ctx_cfg["coding_mode"]
    if mode in {"always", "on", "true", "1"}:
        return True
    if mode in {"off", "false", "0", "disabled"}:
        return False

    toolsets = {str(item).strip() for item in (enabled_toolsets or []) if str(item).strip()}
    return bool(toolsets & set(ctx_cfg["coding_toolsets"]))


def _find_auth_material(ctx_cfg: Dict[str, Any]) -> tuple[str, str]:
    daemon_url = (
        os.getenv("CTX_DAEMON_URL")
        or ctx_cfg.get("daemon_url")
        or ""
    ).strip()
    token = (
        os.getenv("CTX_DAEMON_AUTH_TOKEN")
        or ctx_cfg.get("auth_token")
        or ""
    ).strip()

    probe_paths = [
        Path("~/.ctx/auth.json").expanduser(),
        Path("~/.config/ctx/auth.json").expanduser(),
        Path(os.getenv("CTX_DATA_DIR") or ctx_cfg.get("data_dir") or _DEFAULT_DATA_DIR).expanduser() / "daemon_auth.json",
        Path("~/.ctx-data/daemon_auth.json").expanduser(),
    ]
    for path in probe_paths:
        if token and daemon_url:
            break
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not daemon_url:
            daemon_url = str(payload.get("daemon_url") or "").strip()
        if not token:
            token = str(payload.get("token") or "").strip()

    daemon_url = daemon_url or _DEFAULT_DAEMON_URL
    return daemon_url.rstrip("/"), token


class _CtxDaemonClient:
    def __init__(self, daemon_url: str, token: str):
        self.daemon_url = daemon_url.rstrip("/")
        self.token = token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.daemon_url}{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with request.urlopen(req, timeout=20) as resp:
                body = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ctx daemon {method.upper()} {path} -> HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"ctx daemon unreachable at {url}: {exc}") from exc

        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def list_workspaces(self) -> list[dict]:
        data = self._request_json("GET", "/api/workspaces")
        return data if isinstance(data, list) else []

    def get_workspace(self, workspace_id: str) -> Optional[dict]:
        data = self._request_json("GET", f"/api/workspaces/{workspace_id}")
        return data if isinstance(data, dict) else None

    def create_task(self, workspace_id: str, title: str, prompt: str) -> dict:
        data = self._request_json(
            "POST",
            f"/api/workspaces/{workspace_id}/tasks",
            payload={"title": title, "prompt": prompt},
        )
        if not isinstance(data, dict):
            raise RuntimeError("ctx daemon returned an unexpected task payload")
        return data

    def list_providers(self) -> list[dict]:
        data = self._request_json("GET", "/api/providers")
        return data if isinstance(data, list) else []

    def create_session(
        self,
        task_id: str,
        *,
        provider_id: str,
        model_id: str,
        execution_environment: str,
    ) -> dict:
        data = self._request_json(
            "POST",
            f"/api/tasks/{task_id}/sessions",
            payload={
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_environment": execution_environment,
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("ctx daemon returned an unexpected session payload")
        return data


def _resolve_workspace(
    client: _CtxDaemonClient,
    *,
    workspace_id: str,
    repo_root: Optional[str],
) -> Optional[dict]:
    if workspace_id:
        workspace = client.get_workspace(workspace_id)
        if workspace:
            return workspace
        raise RuntimeError(f"Configured ctx workspace {workspace_id} was not found")

    candidate_root = _normalize_path(repo_root)
    if not candidate_root:
        return None

    for workspace in client.list_workspaces():
        root_path = _normalize_path(workspace.get("root_path"))
        if not root_path:
            continue
        if candidate_root == root_path or _path_within_root(candidate_root, root_path):
            return workspace
    return None


def _resolve_provider_session(
    client: _CtxDaemonClient,
    ctx_cfg: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    provider_id = (
        os.getenv("HERMES_CTX_SESSION_PROVIDER_ID")
        or ctx_cfg.get("session_provider_id")
        or ""
    ).strip()
    model_id = (
        os.getenv("HERMES_CTX_SESSION_MODEL_ID")
        or ctx_cfg.get("session_model_id")
        or ""
    ).strip()
    if not provider_id or not model_id:
        return None, None

    providers = client.list_providers()
    for provider in providers:
        if provider.get("provider_id") == provider_id:
            usable = bool((provider.get("usability") or {}).get("usable"))
            if usable and provider.get("installed"):
                return provider_id, model_id
            logger.info(
                "ctx provider %s configured for Hermes but not usable: %s",
                provider_id,
                provider.get("usability"),
            )
            return None, None
    logger.info("ctx provider %s not advertised by daemon; skipping ctx session creation", provider_id)
    return None, None


def _task_title_for_session(session_id: str, prompt: str, repo_root: Optional[str]) -> str:
    line = (prompt or "").strip().splitlines()[0] if prompt else ""
    line = line.strip()
    if line:
        return line[:96]
    repo_name = Path(repo_root).name if repo_root else "session"
    return f"Hermes coding session {repo_name} {session_id}"[:96]


def maybe_bind_ctx_session(
    *,
    session_id: str,
    enabled_toolsets: Optional[Iterable[str]],
    platform: Optional[str],
    prompt: str = "",
    repo_root: Optional[str] = None,
    dry_run: bool = False,
    config: Optional[Dict[str, Any]] = None,
) -> CtxBinding:
    """Resolve and optionally create a ctx binding for a Hermes session."""

    session_id = str(session_id or "").strip()
    platform = str(platform or "").strip()
    if not session_id:
        return CtxBinding(active=False, reason="missing session id", session_id="", platform=platform)

    if not is_ctx_candidate(enabled_toolsets=enabled_toolsets, platform=platform, config=config):
        return CtxBinding(active=False, reason="ctx disabled for this session", session_id=session_id, platform=platform)

    record = _load_binding_record(session_id)
    if record:
        binding = _binding_from_record(record, reason="reused persisted binding")
        if binding.worktree_path and Path(binding.worktree_path).exists():
            if not dry_run:
                from tools.terminal_tool import register_task_env_overrides

                register_task_env_overrides(session_id, {"cwd": binding.worktree_path})
            return binding

    ctx_cfg = _load_ctx_config(config)
    daemon_url, token = _find_auth_material(ctx_cfg)
    if not token:
        return CtxBinding(
            active=False,
            reason="ctx auth token not found",
            session_id=session_id,
            platform=platform,
            daemon_url=daemon_url,
        )

    resolved_repo_root = _guess_repo_root(repo_root)
    client = _CtxDaemonClient(daemon_url, token)

    try:
        workspace = _resolve_workspace(
            client,
            workspace_id=ctx_cfg["workspace_id"],
            repo_root=resolved_repo_root,
        )
    except Exception as exc:
        logger.warning("ctx workspace resolution failed for session %s: %s", session_id, exc)
        return CtxBinding(
            active=False,
            reason=str(exc),
            session_id=session_id,
            platform=platform,
            repo_root=resolved_repo_root,
            daemon_url=daemon_url,
        )

    if not workspace:
        return CtxBinding(
            active=False,
            reason="no matching ctx workspace",
            session_id=session_id,
            platform=platform,
            repo_root=resolved_repo_root,
            daemon_url=daemon_url,
        )

    workspace_id = str(workspace.get("id") or "")
    workspace_root = _normalize_path(workspace.get("root_path"))
    if dry_run:
        return CtxBinding(
            active=True,
            reason="ctx workspace available",
            session_id=session_id,
            platform=platform,
            repo_root=resolved_repo_root,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            daemon_url=daemon_url,
            source="dry-run",
        )

    try:
        task = client.create_task(
            workspace_id,
            title=_task_title_for_session(session_id, prompt, resolved_repo_root),
            prompt=prompt or f"Hermes coding session {session_id}",
        )
    except Exception as exc:
        logger.warning("ctx task creation failed for session %s: %s", session_id, exc)
        return CtxBinding(
            active=False,
            reason=f"ctx task creation failed: {exc}",
            session_id=session_id,
            platform=platform,
            repo_root=resolved_repo_root,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            daemon_url=daemon_url,
        )

    task_id = str(task.get("id") or "")
    worktree_id = str(task.get("primary_worktree_id") or "")
    data_dir = Path(os.getenv("CTX_DATA_DIR") or ctx_cfg.get("data_dir") or _DEFAULT_DATA_DIR).expanduser()
    worktree_path = str(data_dir / "worktrees" / workspace_id / worktree_id) if worktree_id else None

    provider_id, model_id = _resolve_provider_session(client, ctx_cfg)
    ctx_session_id = None
    if provider_id and model_id:
        try:
            session_payload = client.create_session(
                task_id,
                provider_id=provider_id,
                model_id=model_id,
                execution_environment=ctx_cfg["session_execution_environment"],
            )
            ctx_session_id = str(session_payload.get("id") or "")
            worktree_id = str(session_payload.get("worktree_id") or worktree_id)
            if worktree_id:
                worktree_path = str(data_dir / "worktrees" / workspace_id / worktree_id)
            logger.info(
                "Bound Hermes session %s to ctx task %s and ctx session %s",
                session_id,
                task_id,
                ctx_session_id,
            )
        except Exception as exc:
            logger.warning(
                "ctx session creation skipped for Hermes session %s: %s",
                session_id,
                exc,
            )
            provider_id = None
            model_id = None

    binding = CtxBinding(
        active=True,
        reason="ctx task bound",
        session_id=session_id,
        platform=platform,
        repo_root=resolved_repo_root,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        task_id=task_id,
        worktree_id=worktree_id,
        worktree_path=worktree_path,
        ctx_session_id=ctx_session_id,
        ctx_session_provider_id=provider_id,
        ctx_session_model_id=model_id,
        daemon_url=daemon_url,
        source="ctx-daemon",
    )
    _persist_binding(binding)

    if binding.worktree_path:
        from tools.terminal_tool import register_task_env_overrides

        register_task_env_overrides(session_id, {"cwd": binding.worktree_path})

    logger.info(
        "ctx binding active for Hermes session %s: workspace=%s task=%s worktree=%s",
        session_id,
        workspace_id,
        task_id,
        worktree_id,
    )
    return binding
