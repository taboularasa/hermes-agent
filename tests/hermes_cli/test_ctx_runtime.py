import io
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from hermes_cli import ctx_runtime
from hermes_cli.config import load_config, save_config
from hermes_state import SessionDB
from tools.terminal_tool import clear_task_env_overrides, get_task_cwd


class _FakeCtxClient:
    def __init__(self, providers=None):
        self.providers = providers or []
        self.created_tasks = []
        self.created_sessions = []
        self.deleted_tasks = []

    def get_workspace(self, workspace_id: str):
        return None

    def list_workspaces(self):
        return [
            {
                "id": "ws-1",
                "root_path": "/tmp/project",
            }
        ]

    def create_task(self, workspace_id: str, title: str, prompt: str):
        self.created_tasks.append((workspace_id, title, prompt))
        return {
            "id": "task-1",
            "workspace_id": workspace_id,
            "primary_worktree_id": "wt-1",
        }

    def list_providers(self):
        return self.providers

    def create_session(self, task_id: str, *, provider_id: str, model_id: str, execution_environment: str):
        self.created_sessions.append((task_id, provider_id, model_id, execution_environment))
        return {
            "id": "ctx-session-1",
            "task_id": task_id,
            "worktree_id": "wt-1",
        }

    def delete_task(self, task_id: str):
        self.deleted_tasks.append(task_id)


class _FakeHTTPResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _write_ctx_config(tmp_path, **overrides):
    config = load_config()
    config["ctx"].update(
        {
            "enabled": True,
            "coding_mode": "auto",
            "coding_toolsets": ["terminal", "file", "code_execution"],
            "data_dir": str(tmp_path / "ctx-data"),
        }
    )
    config["ctx"].update(overrides)
    save_config(config)


def _write_binding_record(tmp_path, session_id: str, payload: dict):
    record_path = tmp_path / "ctx" / "session_bindings.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps({"version": 1, "sessions": {session_id: payload}}),
        encoding="utf-8",
    )
    return record_path


def test_is_ctx_candidate_requires_coding_toolsets(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(tmp_path)

    assert ctx_runtime.is_ctx_candidate(
        enabled_toolsets=["terminal"],
        platform="cli",
    )
    assert not ctx_runtime.is_ctx_candidate(
        enabled_toolsets=["web"],
        platform="cli",
    )
    assert not ctx_runtime.is_ctx_candidate(
        enabled_toolsets=["terminal"],
        platform="acp",
    )


def test_maybe_bind_ctx_session_persists_and_registers_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(tmp_path)

    fake_client = _FakeCtxClient()
    monkeypatch.setattr(ctx_runtime, "_CtxDaemonClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(ctx_runtime, "_find_auth_material", lambda _cfg: ("http://ctx.local", "token"))
    monkeypatch.setattr(ctx_runtime, "_guess_repo_root", lambda _candidate: "/tmp/project")

    binding = ctx_runtime.maybe_bind_ctx_session(
        session_id="sess-1",
        enabled_toolsets=["terminal", "file"],
        platform="cli",
        prompt="Fix the failing tests",
    )

    try:
        assert binding.active
        assert binding.workspace_id == "ws-1"
        assert binding.task_id == "task-1"
        assert binding.worktree_path == str(tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1")
        assert get_task_cwd("sess-1") == binding.worktree_path

        record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))
        assert record["sessions"]["sess-1"]["task_id"] == "task-1"
        assert fake_client.created_tasks == [
            ("ws-1", "Fix the failing tests", "Fix the failing tests")
        ]
    finally:
        clear_task_env_overrides("sess-1")


def test_maybe_bind_ctx_session_reuses_persisted_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(tmp_path)

    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    record_path = tmp_path / "ctx" / "session_bindings.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {
                    "sess-1": {
                        "active": True,
                        "reason": "ctx task bound",
                        "session_id": "sess-1",
                        "platform": "cli",
                        "workspace_id": "ws-1",
                        "task_id": "task-1",
                        "worktree_id": "wt-1",
                        "worktree_path": str(worktree),
                        "source": "ctx-daemon",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def _unexpected_client(*_args, **_kwargs):
        raise AssertionError("ctx daemon should not be called for persisted bindings")

    monkeypatch.setattr(ctx_runtime, "_CtxDaemonClient", _unexpected_client)

    binding = ctx_runtime.maybe_bind_ctx_session(
        session_id="sess-1",
        enabled_toolsets=["terminal"],
        platform="cli",
    )

    try:
        assert binding.active
        assert binding.reason == "reused persisted binding"
        assert binding.worktree_path == str(worktree)
        assert get_task_cwd("sess-1") == str(worktree)
    finally:
        clear_task_env_overrides("sess-1")


def test_maybe_bind_ctx_session_creates_optional_ctx_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(
        tmp_path,
        session_provider_id="codex",
        session_model_id="gpt-5.2-codex",
    )

    fake_client = _FakeCtxClient(
        providers=[
            {
                "provider_id": "codex",
                "installed": True,
                "usability": {"usable": True},
            }
        ]
    )
    monkeypatch.setattr(ctx_runtime, "_CtxDaemonClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(ctx_runtime, "_find_auth_material", lambda _cfg: ("http://ctx.local", "token"))
    monkeypatch.setattr(ctx_runtime, "_guess_repo_root", lambda _candidate: "/tmp/project")

    binding = ctx_runtime.maybe_bind_ctx_session(
        session_id="sess-2",
        enabled_toolsets=["terminal"],
        platform="telegram",
        prompt="Implement the feature",
    )

    try:
        assert binding.active
        assert binding.ctx_session_id == "ctx-session-1"
        assert binding.ctx_session_provider_id == "codex"
        assert fake_client.created_sessions == [
            ("task-1", "codex", "gpt-5.2-codex", "host")
        ]
    finally:
        clear_task_env_overrides("sess-2")


def test_guess_repo_root_prefers_process_cwd_over_terminal_default(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    terminal_default = tmp_path / "terminal-default"
    terminal_default.mkdir()

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("TERMINAL_CWD", str(terminal_default))

    resolved = ctx_runtime._guess_repo_root(None)
    assert resolved == str(repo_root)


def test_ctx_client_retries_after_closed_pool_error(monkeypatch):
    client = ctx_runtime._CtxDaemonClient("http://ctx.local", "token")
    restart_calls = []
    requests = []
    responses = iter(
        [
            ctx_runtime.error.HTTPError(
                "http://ctx.local/api/workspaces/ws-1/tasks",
                500,
                "Internal Server Error",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":"attempted to acquire a connection on a closed pool"}'
                ),
            ),
            _FakeHTTPResponse(200, {"id": "task-1"}),
        ]
    )

    def _fake_urlopen(req, timeout=20):
        requests.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "payload": json.loads(req.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ctx_runtime.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        ctx_runtime,
        "_restart_ctx_daemon_service",
        lambda daemon_url, token: restart_calls.append((daemon_url, token)),
    )

    task = client.create_task("ws-1", "Fix the failing tests", "Fix the failing tests")

    assert task == {"id": "task-1"}
    assert restart_calls == [("http://ctx.local", "token")]
    assert requests == [
        {
            "url": "http://ctx.local/api/workspaces/ws-1/tasks",
            "method": "POST",
            "payload": {
                "title": "Fix the failing tests",
                "prompt": "Fix the failing tests",
            },
            "timeout": 20,
        },
        {
            "url": "http://ctx.local/api/workspaces/ws-1/tasks",
            "method": "POST",
            "payload": {
                "title": "Fix the failing tests",
                "prompt": "Fix the failing tests",
            },
            "timeout": 20,
        },
    ]


def test_ctx_client_does_not_retry_non_recoverable_http_error(monkeypatch):
    client = ctx_runtime._CtxDaemonClient("http://ctx.local", "token")
    restart_calls = []

    def _fake_urlopen(_req, timeout=20):
        raise ctx_runtime.error.HTTPError(
            "http://ctx.local/api/workspaces",
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"database unavailable"}'),
        )

    monkeypatch.setattr(ctx_runtime.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        ctx_runtime,
        "_restart_ctx_daemon_service",
        lambda daemon_url, token: restart_calls.append((daemon_url, token)),
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        client.list_workspaces()

    assert restart_calls == []


def test_restart_ctx_daemon_service_restarts_and_waits(monkeypatch):
    calls = []
    waits = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ctx_runtime.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        ctx_runtime,
        "_wait_for_ctx_daemon_ready",
        lambda daemon_url, token: waits.append((daemon_url, token)),
    )
    monkeypatch.setattr(ctx_runtime, "_last_ctx_recovery_attempt", 0.0)

    ctx_runtime._restart_ctx_daemon_service("http://ctx.local", "token")

    assert calls == [
        (
            ["systemctl", "--user", "restart", "ctx-daemon.service"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 30,
            },
        )
    ]
    assert waits == [("http://ctx.local", "token")]
    assert ctx_runtime._last_ctx_recovery_attempt > 0.0


def test_normalize_ctx_bindings_deactivates_ended_cron_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    session_id = "cron_job-1_20260411_010203"
    _write_binding_record(
        tmp_path,
        session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": session_id,
            "platform": "cron",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-1",
            "worktree_path": str(worktree),
            "source": "ctx-daemon",
        },
    )

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, source="cron")
        db.end_session(session_id, "cron_complete")
    finally:
        db.close()

    retired = ctx_runtime.normalize_ctx_bindings()
    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))

    assert retired == {
        session_id: "ctx binding retired: session ended (cron_complete)"
    }
    assert record["sessions"][session_id]["active"] is False
    assert record["sessions"][session_id]["reason"] == "ctx binding retired: session ended (cron_complete)"


def test_transfer_ctx_binding_moves_record_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    old_session_id = "sess-old"
    new_session_id = "sess-new"
    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    _write_binding_record(
        tmp_path,
        old_session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": old_session_id,
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-1",
            "worktree_path": str(worktree),
            "source": "ctx-daemon",
        },
    )
    from tools.terminal_tool import register_task_env_overrides

    register_task_env_overrides(old_session_id, {"cwd": str(worktree)})

    binding = ctx_runtime.transfer_ctx_binding(
        old_session_id,
        new_session_id,
        reason="ctx binding transferred after compression",
    )
    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))

    assert binding is not None
    assert binding.session_id == new_session_id
    assert old_session_id not in record["sessions"]
    assert record["sessions"][new_session_id]["session_id"] == new_session_id
    assert get_task_cwd(old_session_id, default="fallback") == "fallback"
    assert get_task_cwd(new_session_id) == str(worktree)

    clear_task_env_overrides(new_session_id)


def test_cleanup_ctx_binding_deletes_task_and_clears_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(tmp_path)

    session_id = "sess-cleanup"
    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    _write_binding_record(
        tmp_path,
        session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": session_id,
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-1",
            "worktree_path": str(worktree),
            "source": "ctx-daemon",
        },
    )
    from tools.terminal_tool import register_task_env_overrides

    register_task_env_overrides(session_id, {"cwd": str(worktree)})

    fake_client = _FakeCtxClient()
    monkeypatch.setattr(ctx_runtime, "_CtxDaemonClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(ctx_runtime, "_find_auth_material", lambda _cfg: ("http://ctx.local", "token"))

    assert ctx_runtime.cleanup_ctx_binding(
        session_id,
        reason=ctx_runtime.ctx_cleanup_reason_for_end("cli_close"),
    )

    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))
    assert fake_client.deleted_tasks == ["task-1"]
    assert record["sessions"][session_id]["active"] is False
    assert record["sessions"][session_id]["reason"] == "ctx binding retired: session ended (cli_close)"
    assert get_task_cwd(session_id, default="fallback") == "fallback"


def test_normalize_ctx_bindings_deactivates_missing_worktree_and_clears_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TERMINAL_CWD", raising=False)

    session_id = "sess-missing-worktree"
    missing_worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-missing"
    _write_binding_record(
        tmp_path,
        session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": session_id,
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-missing",
            "worktree_path": str(missing_worktree),
            "source": "ctx-daemon",
        },
    )
    clear_task_env_overrides(session_id)
    from tools.terminal_tool import register_task_env_overrides

    register_task_env_overrides(session_id, {"cwd": str(missing_worktree)})

    retired = ctx_runtime.normalize_ctx_bindings(session_id=session_id)
    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))

    assert retired == {session_id: "ctx binding retired: worktree missing"}
    assert record["sessions"][session_id]["active"] is False
    assert record["sessions"][session_id]["reason"] == "ctx binding retired: worktree missing"
    assert get_task_cwd(session_id, default="fallback") == "fallback"


def test_normalize_ctx_bindings_deactivates_stale_active_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    session_id = "sess-stale-active"
    updated_at = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    _write_binding_record(
        tmp_path,
        session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": session_id,
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-1",
            "worktree_path": str(worktree),
            "updated_at": updated_at,
            "source": "ctx-daemon",
        },
    )

    retired = ctx_runtime.normalize_ctx_bindings()
    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))

    assert retired == {session_id: "ctx binding retired: stale active binding (>12h)"}
    assert record["sessions"][session_id]["active"] is False
    assert record["sessions"][session_id]["reason"] == "ctx binding retired: stale active binding (>12h)"


def test_normalize_ctx_bindings_accepts_explicit_bindings_path(tmp_path):
    now = datetime(2026, 4, 12, 16, 0, 0, tzinfo=timezone.utc)
    session_id = "sess-explicit-path"
    updated_at = (now - timedelta(hours=13)).isoformat()
    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    bindings_path = tmp_path / "session_bindings.json"
    bindings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {
                    session_id: {
                        "active": True,
                        "reason": "ctx task bound",
                        "session_id": session_id,
                        "platform": "cli",
                        "workspace_id": "ws-1",
                        "task_id": "task-1",
                        "worktree_id": "wt-1",
                        "worktree_path": str(worktree),
                        "updated_at": updated_at,
                        "source": "ctx-daemon",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    retired = ctx_runtime.normalize_ctx_bindings(bindings_path=bindings_path, now=now)
    record = json.loads(bindings_path.read_text(encoding="utf-8"))

    assert retired == {session_id: "ctx binding retired: stale active binding (>12h)"}
    assert record["sessions"][session_id]["active"] is False
    assert record["sessions"][session_id]["reason"] == "ctx binding retired: stale active binding (>12h)"
    assert record["sessions"][session_id]["updated_at"] == now.isoformat()


def test_normalize_ctx_bindings_deactivates_stale_active_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-1"
    worktree.mkdir(parents=True, exist_ok=True)
    session_id = "sess-active"
    updated_at = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    _write_binding_record(
        tmp_path,
        session_id,
        {
            "active": True,
            "reason": "ctx task bound",
            "session_id": session_id,
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-1",
            "worktree_id": "wt-1",
            "worktree_path": str(worktree),
            "updated_at": updated_at,
            "source": "ctx-daemon",
        },
    )

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, source="cli")
    finally:
        db.close()

    retired = ctx_runtime.normalize_ctx_bindings()
    record = json.loads((tmp_path / "ctx" / "session_bindings.json").read_text(encoding="utf-8"))

    assert retired == {session_id: "ctx binding retired: stale active binding (>12h)"}
    assert record["sessions"][session_id]["active"] is False


def test_maybe_bind_ctx_session_skips_inactive_persisted_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_ctx_config(tmp_path)

    stale_worktree = tmp_path / "ctx-data" / "worktrees" / "ws-1" / "wt-stale"
    stale_worktree.mkdir(parents=True, exist_ok=True)
    _write_binding_record(
        tmp_path,
        "sess-1",
        {
            "active": False,
            "reason": "ctx binding retired: cron job finished",
            "session_id": "sess-1",
            "platform": "cli",
            "workspace_id": "ws-1",
            "task_id": "task-stale",
            "worktree_id": "wt-stale",
            "worktree_path": str(stale_worktree),
            "source": "ctx-daemon",
        },
    )

    fake_client = _FakeCtxClient()
    monkeypatch.setattr(ctx_runtime, "_CtxDaemonClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(ctx_runtime, "_find_auth_material", lambda _cfg: ("http://ctx.local", "token"))
    monkeypatch.setattr(ctx_runtime, "_guess_repo_root", lambda _candidate: "/tmp/project")

    binding = ctx_runtime.maybe_bind_ctx_session(
        session_id="sess-1",
        enabled_toolsets=["terminal"],
        platform="cli",
        prompt="Fix the failing tests again",
    )

    try:
        assert binding.active
        assert binding.task_id == "task-1"
        assert fake_client.created_tasks == [
            ("ws-1", "Fix the failing tests again", "Fix the failing tests again")
        ]
    finally:
        clear_task_env_overrides("sess-1")
