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
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_cli.config import get_hermes_home, load_config
from hermes_cli.ctx_runtime import describe_existing_ctx_binding
from tools.process_registry import process_registry
from tools.registry import registry
from tools.terminal_tool import get_task_cwd
from utils import atomic_json_write

logger = logging.getLogger(__name__)


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


def _runs_path() -> Path:
    return get_hermes_home() / "codex" / "runs.json"


def _load_runs() -> Dict[str, Any]:
    path = _runs_path()
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


def _save_runs(data: Dict[str, Any]) -> None:
    atomic_json_write(_runs_path(), data)


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _resolve_workdir(workdir: Optional[str], task_id: Optional[str]) -> str:
    candidate = workdir or get_task_cwd(task_id, os.getcwd()) or os.getcwd()
    return _normalize_path(candidate)


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


def _persist_record(record: Dict[str, Any]) -> None:
    data = _load_runs()
    runs = data.setdefault("runs", {})
    runs[record["run_id"]] = record
    _save_runs(data)

    record_path = record.get("record_path")
    latest_path = record.get("latest_path")
    if record_path:
        atomic_json_write(record_path, record)
    if latest_path:
        atomic_json_write(latest_path, record)


def _load_record(run_id: str) -> Dict[str, Any]:
    data = _load_runs()
    record = data.get("runs", {}).get(run_id)
    if not isinstance(record, dict):
        raise RuntimeError(f"No Codex supervisor run found for {run_id}")
    return dict(record)


def _refresh_record(record: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(record.get("process_session_id") or "")
    session = process_registry.get(session_id) if session_id else None
    output_buffer = session.output_buffer if session and session.output_buffer else ""
    parsed = _parse_codex_events(output_buffer)

    if parsed["codex_session_id"]:
        record["codex_session_id"] = parsed["codex_session_id"]
    if parsed["last_agent_message"]:
        record["last_agent_message"] = parsed["last_agent_message"]
    if parsed["usage"] is not None:
        record["usage"] = parsed["usage"]
    record["recent_event_types"] = parsed["recent_event_types"]
    record["completed_turns"] = parsed["completed_turns"]

    if session:
        record["pid"] = session.pid
        record["process_started_at"] = session.started_at
        record["uptime_seconds"] = int(time.time() - session.started_at)
        if session.exited:
            record["status"] = "completed" if session.exit_code == 0 else "failed"
            record["exit_code"] = session.exit_code
            record["completed_at"] = record.get("completed_at") or _utc_now()
        else:
            record["status"] = "running"
            record["exit_code"] = None
    elif record.get("status") == "running":
        record["status"] = "unknown"

    final_message = _read_text(record.get("last_message_path"))
    if final_message:
        record["final_message"] = final_message.strip()
    _persist_record(record)
    return record


def _build_response(record: Dict[str, Any], **extra: Any) -> str:
    payload = {
        "run_id": record["run_id"],
        "status": record.get("status"),
        "phase": record.get("phase"),
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
    task_id: str,
    parent_run_id: str = "",
    codex_session_id: str = "",
) -> Dict[str, Any]:
    run_id = f"codex_{uuid.uuid4().hex[:12]}"
    artifacts = _artifact_paths(workdir, run_id)
    ctx_binding = describe_existing_ctx_binding(task_id) if task_id else None
    command = _build_codex_command(
        subcommand="resume" if parent_run_id else "exec",
        workdir=workdir,
        prompt=prompt,
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
        env_vars=None,
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
    timeout: int = 900,
    limit: int = 10,
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
        runs.sort(key=lambda record: record.get("started_at", 0), reverse=True)
        trimmed = [
            {
                "run_id": record.get("run_id"),
                "status": record.get("status"),
                "phase": record.get("phase"),
                "workdir": record.get("workdir"),
                "process_session_id": record.get("process_session_id"),
                "codex_session_id": record.get("codex_session_id"),
                "ctx_task_id": record.get("ctx_task_id"),
                "started_at": record.get("started_at"),
            }
            for record in runs[: max(1, int(limit or 10))]
        ]
        return json.dumps({"runs": trimmed}, ensure_ascii=False)

    if action == "start":
        prompt = str(prompt or "").strip()
        if not prompt:
            return json.dumps({"error": "prompt is required for action=start"}, ensure_ascii=False)
        resolved_workdir = _resolve_workdir(workdir, task_id)
        _resolve_git_dir(resolved_workdir)
        effective_model = str(model or _load_codex_config()["default_model"]).strip()
        record = _start_run(
            prompt=prompt,
            phase=str(phase or "implement").strip() or "implement",
            model=effective_model,
            workdir=resolved_workdir,
            task_id=task_id,
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
            task_id=str(record.get("task_id") or task_id),
            parent_run_id=record["run_id"],
            codex_session_id=codex_session_id,
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
        timeout=args.get("timeout", 900),
        limit=args.get("limit", 10),
        task_id=kw.get("task_id", ""),
    ),
    check_fn=check_codex_delegate_requirements,
    emoji="🧠",
)
