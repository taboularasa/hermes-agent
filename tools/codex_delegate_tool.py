"""Supervise local Codex CLI implementation runs from Hermes.

Hermes stays in the EM role and uses this tool to launch bounded local Codex
worker runs in the current ctx-managed worktree (when available). The tool
persists machine-readable run metadata so Hermes can inspect progress, wait for
completion, and launch corrective follow-up runs without exposing Codex as a
first-class Linear delegate.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_cli.config import get_hermes_home, load_config
from hermes_cli.ctx_runtime import describe_existing_ctx_binding, maybe_bind_ctx_session
from tools.process_registry import process_registry
from tools.registry import registry
from tools.terminal_tool import get_task_cwd
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_PROBE_TTL_SECONDS = int(os.getenv("HERMES_CODEX_PROBE_TTL_SECONDS", "600"))
_CTX_CODEX_TOOLSETS = ("terminal", "file", "code_execution")


def _is_probe_phase(phase: Any) -> bool:
    return str(phase or "").strip().lower() == "probe"


def _is_probe_record(record: Dict[str, Any]) -> bool:
    if str(record.get("run_kind") or "").strip().lower() == "probe":
        return True
    return _is_probe_phase(record.get("phase"))


def check_codex_delegate_requirements() -> bool:
    """The Codex supervisor requires the local Codex CLI binary."""
    cfg = _load_codex_config()
    return bool(cfg["enabled"] and shutil.which("codex"))


CODEX_DELEGATE_SCHEMA = {
    "name": "codex_delegate",
    "description": (
        "Launch and supervise local Codex CLI implementation runs on the Lenovo host. "
        "Use this when you should act as the engineering manager and Codex should act "
        "as the implementation worker inside the current ctx-managed coding worktree. "
        "Start or resume a Codex run, inspect status, wait for completion, or stop it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "resume", "status", "wait", "kill", "list"],
                "description": "Which Codex supervisor action to perform.",
            },
            "prompt": {
                "type": "string",
                "description": "Instructions for Codex. Required for start and resume.",
            },
            "run_id": {
                "type": "string",
                "description": "Existing Codex supervisor run ID. Required for resume, status, wait, and kill.",
            },
            "phase": {
                "type": "string",
                "description": "Short label for the Codex run, such as implement, fix, review, or follow-up.",
            },
            "model": {
                "type": "string",
                "description": "Optional Codex model override for this run.",
            },
            "workdir": {
                "type": "string",
                "description": "Optional working directory override. Defaults to the current ctx-bound worktree or terminal cwd.",
            },
            "external_key": {
                "type": "string",
                "description": "Optional stable work-item key such as linear:HAD-123. Active runs with the same external_key and workdir are reused instead of duplicated.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to wait for action=wait before returning. Default 900.",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of runs to return for action=list. Default 10.",
                "minimum": 1,
            },
            "include_probes": {
                "type": "boolean",
                "description": "When listing, include probe runs inside the main runs list.",
            },
            "refresh": {
                "type": "boolean",
                "description": "When listing, refresh run status before returning. Default true.",
            },
        },
        "required": ["action"],
    },
}


def _load_codex_config() -> Dict[str, Any]:
    cfg = load_config()
    raw = cfg.get("codex_delegate", {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "prefer_for_coding": bool(raw.get("prefer_for_coding", True)),
        "dangerous_bypass": bool(raw.get("dangerous_bypass", True)),
        "default_model": str(raw.get("model") or "").strip(),
    }


def _ctx_enabled() -> bool:
    cfg = load_config()
    raw = cfg.get("ctx", {}) if isinstance(cfg, dict) else {}
    return isinstance(raw, dict) and bool(raw.get("enabled", False))


def _runs_path(runs_path: Optional[Path] = None) -> Path:
    if runs_path is not None:
        return Path(runs_path).expanduser()
    return get_hermes_home() / "codex" / "runs.json"


def _load_runs(runs_path: Optional[Path] = None) -> Dict[str, Any]:
    path = _runs_path(runs_path)
    if not path.exists():
        return {"version": 1, "runs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read codex run metadata from %s", path, exc_info=True)
        return {"version": 1, "runs": {}}
    if not isinstance(data, dict):
        return {"version": 1, "runs": {}}
    data.setdefault("version", 1)
    data.setdefault("runs", {})
    if not isinstance(data["runs"], dict):
        data["runs"] = {}
    return data


def _save_runs(data: Dict[str, Any], runs_path: Optional[Path] = None) -> None:
    atomic_json_write(_runs_path(runs_path), data)


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _resolve_workdir(workdir: Optional[str], task_id: Optional[str]) -> str:
    candidate = workdir or get_task_cwd(task_id, os.getcwd()) or os.getcwd()
    return _normalize_path(candidate)


def _resolve_repo_root(workdir: str) -> str:
    result = _run_git(workdir, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        candidate = Path(workdir).expanduser().resolve()
        if (candidate / ".git").exists():
            return str(candidate)
        raise RuntimeError(
            "codex_delegate requires a git repository. "
            "Use it from a ctx-managed coding worktree or another git checkout."
        )
    return _normalize_path(result.stdout.strip())


def _run_git(workdir: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )


def _resolve_git_dir(workdir: str) -> Path:
    result = _run_git(workdir, "rev-parse", "--git-dir")
    if result.returncode != 0:
        raise RuntimeError(
            "codex_delegate requires a git repository. "
            "Use it from a ctx-managed coding worktree or another git checkout."
        )
    git_dir = result.stdout.strip()
    path = Path(git_dir)
    if not path.is_absolute():
        path = Path(workdir) / path
    return path.resolve()


def _artifact_dir(workdir: str) -> Path:
    return _resolve_git_dir(workdir) / "hermes-codex"


def _artifact_paths(workdir: str, run_id: str) -> Dict[str, str]:
    artifact_dir = _artifact_dir(workdir)
    return {
        "artifact_dir": str(artifact_dir),
        "last_message_path": str(artifact_dir / f"{run_id}.last-message.txt"),
        "record_path": str(artifact_dir / f"{run_id}.json"),
        "latest_path": str(artifact_dir / "latest.json"),
    }


def _utc_now() -> float:
    return time.time()


def _coerce_now_epoch(now: Optional[Any] = None) -> float:
    if now is None:
        return _utc_now()
    if isinstance(now, datetime):
        return now.timestamp()
    try:
        return float(now)
    except (TypeError, ValueError):
        return _utc_now()


def _infer_ctx_platform(task_id: str, binding: Optional[Any] = None) -> str:
    source = str(os.getenv("HERMES_SESSION_SOURCE") or "").strip().lower()
    if source:
        return source
    binding_platform = str(getattr(binding, "platform", "") or "").strip().lower()
    if binding_platform:
        return binding_platform
    if str(task_id or "").startswith("cron_"):
        return "cron"
    return "cli"


def _binding_repo_root(binding: Optional[Any]) -> str:
    for value in (
        getattr(binding, "workspace_root", None),
        getattr(binding, "repo_root", None),
    ):
        if value:
            return _normalize_path(str(value))
    return ""


def _map_workdir_to_ctx_worktree(requested_workdir: str, repo_root: str, binding: Any) -> str:
    worktree_path = _normalize_path(getattr(binding, "worktree_path", None) or "")
    if not worktree_path:
        raise RuntimeError("ctx binding did not expose a worktree path")

    requested_path = Path(requested_workdir).resolve()
    worktree_root = Path(worktree_path).resolve()
    try:
        requested_path.relative_to(worktree_root)
        return str(requested_path)
    except ValueError:
        pass

    binding_root = _binding_repo_root(binding)
    repo_root = _normalize_path(repo_root)
    if binding_root and repo_root != binding_root:
        raise RuntimeError(
            "current Hermes session is already bound to a different ctx workspace "
            f"({binding_root}) and cannot launch Codex in {repo_root}"
        )

    relative = requested_path.relative_to(Path(repo_root).resolve())
    return str((worktree_root / relative).resolve())


def _resolve_ctx_workdir(
    *,
    requested_workdir: str,
    task_id: str,
    prompt: str,
) -> tuple[Optional[Any], str, str]:
    repo_root = _resolve_repo_root(requested_workdir)
    binding = describe_existing_ctx_binding(task_id) if task_id else None
    if binding and getattr(binding, "active", False):
        return binding, repo_root, _map_workdir_to_ctx_worktree(requested_workdir, repo_root, binding)

    if not _ctx_enabled():
        return binding, repo_root, requested_workdir

    if not task_id:
        raise RuntimeError(
            "ctx-native Codex delegation requires a Hermes task id so the repo can be bound to a ctx worktree"
        )

    binding = maybe_bind_ctx_session(
        session_id=task_id,
        enabled_toolsets=_CTX_CODEX_TOOLSETS,
        platform=_infer_ctx_platform(task_id, binding),
        prompt=prompt,
        repo_root=repo_root,
    )
    if not getattr(binding, "active", False) or not getattr(binding, "worktree_path", None):
        raise RuntimeError(
            "ctx-native Codex delegation requires an active ctx worktree for "
            f"{repo_root}: {getattr(binding, 'reason', 'ctx binding unavailable')}"
        )
    return binding, repo_root, _map_workdir_to_ctx_worktree(requested_workdir, repo_root, binding)


def _repo_virtualenv(repo_root: str) -> str:
    root = Path(repo_root)
    for name in ("venv", ".venv"):
        candidate = root / name
        if (candidate / "bin" / "python").exists():
            return str(candidate)
    return ""


def _codex_prompt(
    prompt: str,
    *,
    repo_root: str,
    workdir: str,
    virtualenv: str,
) -> str:
    notes = []
    normalized_repo_root = _normalize_path(repo_root)
    normalized_workdir = _normalize_path(workdir)
    if normalized_repo_root and normalized_workdir and normalized_repo_root != normalized_workdir:
        notes.append(
            f"Run inside the ctx worktree `{normalized_workdir}` for repo `{normalized_repo_root}`. "
            "Treat the ctx worktree as the source of truth."
        )
    if virtualenv:
        notes.append(
            f"The repo virtualenv `{virtualenv}` is exported on PATH. "
            "Use `python` or `pytest`; do not rely on `./venv/bin/python` inside the worktree."
        )
    if not notes:
        return prompt
    return "\n\n".join(
        [
            "[Local execution note]\n- " + "\n- ".join(notes),
            prompt.strip(),
        ]
    ).strip()


def _codex_env_vars(*, repo_root: str, workdir: str, virtualenv: str) -> Dict[str, str]:
    env_vars: Dict[str, str] = {
        "HERMES_CODEX_REPO_ROOT": repo_root,
        "HERMES_CODEX_WORKTREE": workdir,
    }
    if virtualenv:
        env_vars["VIRTUAL_ENV"] = virtualenv
        env_vars["PATH"] = f"{Path(virtualenv) / 'bin'}:{os.environ.get('PATH', '')}"
    return env_vars


def _read_text(path: Optional[str]) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Failed to read %s", p, exc_info=True)
        return ""


def _parse_codex_events(text: str) -> Dict[str, Any]:
    thread_id = ""
    last_agent_message = ""
    usage = None
    event_types: list[str] = []
    completed_turns = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type") or "")
        if not event_type:
            continue
        event_types.append(event_type)
        if event_type == "thread.started":
            thread_id = str(event.get("thread_id") or thread_id)
        elif event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                last_agent_message = str(item.get("text") or last_agent_message)
        elif event_type == "turn.completed":
            completed_turns += 1
            usage = event.get("usage")
    return {
        "codex_session_id": thread_id,
        "last_agent_message": last_agent_message,
        "completed_turns": completed_turns,
        "usage": usage,
        "recent_event_types": event_types[-12:],
    }


def _persist_record(record: Dict[str, Any], runs_path: Optional[Path] = None) -> None:
    data = _load_runs(runs_path)
    runs = data.setdefault("runs", {})
    runs[record["run_id"]] = record
    _save_runs(data, runs_path)

    record_path = record.get("record_path")
    latest_path = record.get("latest_path")
    if record_path:
        atomic_json_write(record_path, record)
    if latest_path:
        atomic_json_write(latest_path, record)


def _load_record(run_id: str, runs_path: Optional[Path] = None) -> Dict[str, Any]:
    data = _load_runs(runs_path)
    record = data.get("runs", {}).get(run_id)
    if not isinstance(record, dict):
        raise RuntimeError(f"No Codex supervisor run found for {run_id}")
    return dict(record)


def _find_active_record_by_external_key(
    external_key: str,
    workdir: str,
    runs_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    if not external_key:
        return None
    normalized_workdir = _normalize_path(workdir)
    data = _load_runs(runs_path)
    records = list(data.get("runs", {}).values())
    records.sort(key=lambda item: item.get("started_at", 0), reverse=True)
    for raw_record in records:
        if not isinstance(raw_record, dict):
            continue
        if str(raw_record.get("external_key") or "") != external_key:
            continue
        if _normalize_path(str(raw_record.get("workdir") or "")) != normalized_workdir:
            continue
        refreshed = _refresh_record(dict(raw_record), runs_path=runs_path)
        if refreshed.get("status") == "running":
            return refreshed
    return None


def _pid_is_running(pid: Any) -> bool:
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized_pid <= 0:
        return False
    try:
        os.kill(normalized_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _infer_terminal_state(record: Dict[str, Any]) -> tuple[str, Optional[int]]:
    exit_code = record.get("exit_code")
    try:
        normalized_exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        normalized_exit_code = None

    if normalized_exit_code == 0:
        return "completed", 0
    if normalized_exit_code is not None:
        return "failed", normalized_exit_code

    if str(record.get("final_message") or "").strip():
        return "completed", 0
    if int(record.get("completed_turns") or 0) > 0 and str(record.get("last_agent_message") or "").strip():
        return "completed", 0
    return "failed", None


def _normalize_stale_record(record: Dict[str, Any], *, reason: str, now: Optional[Any] = None) -> None:
    status, exit_code = _infer_terminal_state(record)
    record["status"] = status
    if exit_code is not None:
        record["exit_code"] = exit_code
    record["completed_at"] = record.get("completed_at") or _coerce_now_epoch(now)
    record["stale_reason"] = reason


def _apply_probe_expiry(record: Dict[str, Any], *, now: Optional[Any] = None) -> None:
    if not _is_probe_record(record):
        return
    if record.get("status") not in {"running", "unknown"}:
        return
    started_at = record.get("started_at") or record.get("process_started_at") or 0
    try:
        started_at = float(started_at)
    except (TypeError, ValueError):
        started_at = 0
    if started_at <= 0:
        return
    current_time = _coerce_now_epoch(now)
    if current_time - started_at < _PROBE_TTL_SECONDS:
        return
    _normalize_stale_record(record, reason="probe_timeout", now=current_time)
    record["probe_timeout_seconds"] = _PROBE_TTL_SECONDS


def _refresh_record(
    record: Dict[str, Any],
    runs_path: Optional[Path] = None,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    record.setdefault("run_kind", "probe" if _is_probe_phase(record.get("phase")) else "delegated")
    session_id = str(record.get("process_session_id") or "")
    session = process_registry.get(session_id) if session_id else None
    if session is None and session_id:
        recover_session = getattr(process_registry, "recover_session_from_checkpoint", None)
        if callable(recover_session):
            try:
                session = recover_session(session_id)
            except Exception:
                logger.debug("Failed to recover detached process session %s from checkpoint", session_id, exc_info=True)
    output_buffer = session.output_buffer if session and session.output_buffer else ""
    parsed = _parse_codex_events(output_buffer)
    current_time = _coerce_now_epoch(now)

    if parsed["codex_session_id"]:
        record["codex_session_id"] = parsed["codex_session_id"]
    if parsed["last_agent_message"]:
        record["last_agent_message"] = parsed["last_agent_message"]
    if parsed["usage"] is not None:
        record["usage"] = parsed["usage"]
    record["recent_event_types"] = parsed["recent_event_types"]
    record["completed_turns"] = parsed["completed_turns"]

    final_message = _read_text(record.get("last_message_path"))
    if final_message:
        record["final_message"] = final_message.strip()

    if session:
        record["pid"] = session.pid
        record["process_started_at"] = session.started_at
        record["uptime_seconds"] = int(current_time - session.started_at)
        if session.exited:
            record["status"] = "completed" if session.exit_code == 0 else "failed"
            record["exit_code"] = session.exit_code
            record["completed_at"] = record.get("completed_at") or current_time
        elif not _pid_is_running(session.pid):
            _normalize_stale_record(record, reason="process_missing", now=current_time)
        else:
            record["status"] = "running"
            record["exit_code"] = None
    elif record.get("status") in {"running", "unknown"}:
        if _pid_is_running(record.get("pid")):
            record["status"] = "unknown"
        else:
            _normalize_stale_record(record, reason="process_missing", now=current_time)
    _apply_probe_expiry(record, now=current_time)
    _persist_record(record, runs_path=runs_path)
    return record


def normalize_codex_runs(
    *,
    run_id: str = "",
    runs_path: Optional[Path] = None,
    now: Optional[Any] = None,
) -> Dict[str, str]:
    data = _load_runs(runs_path)
    runs = data.get("runs", {})
    if not isinstance(runs, dict):
        return {}
    wanted_run_id = str(run_id or "").strip()
    run_ids = [wanted_run_id] if wanted_run_id else list(runs.keys())
    statuses: Dict[str, str] = {}
    for current_run_id in run_ids:
        record = runs.get(current_run_id)
        if not isinstance(record, dict):
            continue
        refreshed = _refresh_record(dict(record), runs_path=runs_path, now=now)
        statuses[current_run_id] = str(refreshed.get("status") or "")
    return statuses


def _build_response(record: Dict[str, Any], **extra: Any) -> str:
    payload = {
        "run_id": record["run_id"],
        "status": record.get("status"),
        "phase": record.get("phase"),
        "run_kind": record.get("run_kind"),
        "is_probe": _is_probe_record(record),
        "external_key": record.get("external_key"),
        "workdir": record.get("workdir"),
        "process_session_id": record.get("process_session_id"),
        "codex_session_id": record.get("codex_session_id"),
        "ctx_task_id": record.get("ctx_task_id"),
        "ctx_session_id": record.get("ctx_session_id"),
        "ctx_worktree_id": record.get("ctx_worktree_id"),
        "last_agent_message": record.get("last_agent_message"),
        "final_message": record.get("final_message"),
        "exit_code": record.get("exit_code"),
        "recent_event_types": record.get("recent_event_types", []),
        "record_path": record.get("record_path"),
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _build_codex_command(
    *,
    subcommand: str,
    workdir: str,
    prompt: str,
    last_message_path: str,
    model: str,
    codex_session_id: str = "",
) -> str:
    cfg = _load_codex_config()
    command = ["codex", "exec"]
    if subcommand == "resume":
        command.append("resume")
    command.extend(["--json", "-C", workdir, "--output-last-message", last_message_path])
    if cfg["dangerous_bypass"]:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.append("--full-auto")
    if model:
        command.extend(["-m", model])
    if subcommand == "resume":
        command.append(codex_session_id)
    command.append(prompt)
    return shlex.join(command)


def _start_run(
    *,
    prompt: str,
    phase: str,
    model: str,
    workdir: str,
    repo_root: str,
    task_id: str,
    parent_run_id: str = "",
    codex_session_id: str = "",
    external_key: str = "",
) -> Dict[str, Any]:
    run_id = f"codex_{uuid.uuid4().hex[:12]}"
    artifacts = _artifact_paths(workdir, run_id)
    ctx_binding = describe_existing_ctx_binding(task_id) if task_id else None
    virtualenv = _repo_virtualenv(repo_root)
    command = _build_codex_command(
        subcommand="resume" if parent_run_id else "exec",
        workdir=workdir,
        prompt=_codex_prompt(prompt, repo_root=repo_root, workdir=workdir, virtualenv=virtualenv),
        last_message_path=artifacts["last_message_path"],
        model=model,
        codex_session_id=codex_session_id,
    )

    session_key = os.getenv("HERMES_SESSION_KEY", "")
    proc_session = process_registry.spawn_local(
        command=command,
        cwd=workdir,
        task_id=task_id,
        session_key=session_key,
        env_vars=_codex_env_vars(repo_root=repo_root, workdir=workdir, virtualenv=virtualenv),
        use_pty=False,
    )
    # codex exec treats an open stdin pipe as additional prompt input and can
    # wait indefinitely for EOF. Close the write side immediately so the run
    # stays non-interactive unless we explicitly choose a PTY flow later.
    if getattr(proc_session, "process", None) and getattr(proc_session.process, "stdin", None):
        try:
            proc_session.process.stdin.close()
        except Exception:
            logger.debug("Failed to close stdin for Codex run %s", run_id, exc_info=True)

    record = {
        "run_id": run_id,
        "status": "running",
        "phase": phase,
        "run_kind": "probe" if _is_probe_phase(phase) else "delegated",
        "external_key": external_key,
        "prompt": prompt,
        "model": model,
        "workdir": workdir,
        "task_id": task_id,
        "process_session_id": proc_session.id,
        "pid": proc_session.pid,
        "command": command,
        "started_at": _utc_now(),
        "process_started_at": proc_session.started_at,
        "last_message_path": artifacts["last_message_path"],
        "record_path": artifacts["record_path"],
        "latest_path": artifacts["latest_path"],
        "parent_run_id": parent_run_id,
        "codex_session_id": codex_session_id,
        "ctx_task_id": getattr(ctx_binding, "task_id", None),
        "ctx_session_id": getattr(ctx_binding, "ctx_session_id", None),
        "ctx_worktree_id": getattr(ctx_binding, "worktree_id", None),
        "ctx_worktree_path": getattr(ctx_binding, "worktree_path", None),
    }
    _persist_record(record)
    return record


def codex_delegate(
    *,
    action: str,
    prompt: str = "",
    run_id: str = "",
    phase: str = "",
    model: str = "",
    workdir: str = "",
    external_key: str = "",
    timeout: int = 900,
    limit: int = 10,
    include_probes: bool = False,
    refresh: bool = True,
    task_id: str = "",
) -> str:
    if not check_codex_delegate_requirements():
        return json.dumps({
            "error": "Local Codex CLI is unavailable. Install Codex and ensure it is on PATH.",
        }, ensure_ascii=False)

    action = str(action or "").strip().lower()
    if action not in {"start", "resume", "status", "wait", "kill", "list"}:
        return json.dumps({"error": f"Unknown codex_delegate action: {action}"}, ensure_ascii=False)

    if action == "list":
        data = _load_runs()
        runs = list(data.get("runs", {}).values())
        if task_id:
            runs = [record for record in runs if record.get("task_id") == task_id]
        refreshed = []
        if refresh:
            for record in runs:
                if not isinstance(record, dict):
                    continue
                refreshed.append(_refresh_record(dict(record)))
        else:
            refreshed = [record for record in runs if isinstance(record, dict)]
        runs = refreshed
        runs.sort(key=lambda record: record.get("started_at", 0), reverse=True)
        trimmed = [
            {
                "run_id": record.get("run_id"),
                "status": record.get("status"),
                "phase": record.get("phase"),
                "run_kind": record.get("run_kind"),
                "is_probe": _is_probe_record(record),
                "external_key": record.get("external_key"),
                "workdir": record.get("workdir"),
                "process_session_id": record.get("process_session_id"),
                "codex_session_id": record.get("codex_session_id"),
                "ctx_task_id": record.get("ctx_task_id"),
                "started_at": record.get("started_at"),
            }
            for record in runs[: max(1, int(limit or 10))]
        ]
        probe_runs = [record for record in trimmed if record.get("is_probe")]
        if not include_probes:
            trimmed = [record for record in trimmed if not record.get("is_probe")]
        return json.dumps({
            "runs": trimmed,
            "probe_runs": probe_runs,
            "include_probes": bool(include_probes),
        }, ensure_ascii=False)

    if action == "start":
        prompt = str(prompt or "").strip()
        if not prompt:
            return json.dumps({"error": "prompt is required for action=start"}, ensure_ascii=False)
        requested_workdir = _resolve_workdir(workdir, task_id)
        _resolve_git_dir(requested_workdir)
        try:
            _ctx_binding, repo_root, resolved_workdir = _resolve_ctx_workdir(
                requested_workdir=requested_workdir,
                task_id=task_id,
                prompt=prompt,
            )
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        stable_key = str(external_key or "").strip()
        existing_active = _find_active_record_by_external_key(stable_key, resolved_workdir) if stable_key else None
        if existing_active:
            return _build_response(existing_active, skipped_existing=True)
        effective_model = str(model or _load_codex_config()["default_model"]).strip()
        record = _start_run(
            prompt=prompt,
            phase=str(phase or "implement").strip() or "implement",
            model=effective_model,
            workdir=resolved_workdir,
            repo_root=repo_root,
            task_id=task_id,
            external_key=stable_key,
        )
        return _build_response(record)

    record = _load_record(str(run_id or "").strip())
    record = _refresh_record(record)

    if action == "resume":
        prompt = str(prompt or "").strip()
        if not prompt:
            return json.dumps({"error": "prompt is required for action=resume"}, ensure_ascii=False)
        codex_session_id = str(record.get("codex_session_id") or "").strip()
        if not codex_session_id:
            return json.dumps({
                "error": f"Run {record['run_id']} has no resumable Codex session id yet. Wait for thread.started first.",
            }, ensure_ascii=False)
        effective_model = str(model or record.get("model") or _load_codex_config()["default_model"]).strip()
        next_record = _start_run(
            prompt=prompt,
            phase=str(phase or "follow-up").strip() or "follow-up",
            model=effective_model,
            workdir=str(record["workdir"]),
            repo_root=_resolve_repo_root(str(record["workdir"])),
            task_id=str(record.get("task_id") or task_id),
            parent_run_id=record["run_id"],
            codex_session_id=codex_session_id,
            external_key=str(external_key or record.get("external_key") or ""),
        )
        return _build_response(next_record, resumed_from=record["run_id"])

    if action == "wait":
        wait_result = process_registry.wait(str(record.get("process_session_id") or ""), timeout=max(1, int(timeout or 900)))
        record = _refresh_record(record)
        return _build_response(record, wait_result=wait_result)

    if action == "kill":
        kill_result = process_registry.kill_process(str(record.get("process_session_id") or ""))
        record = _refresh_record(record)
        return _build_response(record, kill_result=kill_result)

    return _build_response(record)


registry.register(
    name="codex_delegate",
    toolset="codex",
    schema=CODEX_DELEGATE_SCHEMA,
    handler=lambda args, **kw: codex_delegate(
        action=args.get("action", ""),
        prompt=args.get("prompt", ""),
        run_id=args.get("run_id", ""),
        phase=args.get("phase", ""),
        model=args.get("model", ""),
        workdir=args.get("workdir", ""),
        external_key=args.get("external_key", ""),
        timeout=args.get("timeout", 900),
        limit=args.get("limit", 10),
        include_probes=bool(args.get("include_probes", False)),
        refresh=bool(args.get("refresh", True)),
        task_id=kw.get("task_id", ""),
    ),
    check_fn=check_codex_delegate_requirements,
    emoji="🧠",
)
