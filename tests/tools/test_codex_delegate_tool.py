import json
from pathlib import Path

from tools import codex_delegate_tool


class _FakeSession:
    def __init__(self, session_id: str, pid: int, output_buffer: str = "", exited: bool = False, exit_code: int | None = None):
        self.id = session_id
        self.pid = pid
        self.output_buffer = output_buffer
        self.exited = exited
        self.exit_code = exit_code
        self.started_at = 100.0


class _FakeProcessRegistry:
    def __init__(self):
        self.sessions = {}
        self.spawn_calls = []
        self.wait_calls = []
        self.kill_calls = []

    def spawn_local(self, command, cwd=None, task_id="", session_key="", env_vars=None, use_pty=False):
        self.spawn_calls.append({
            "command": command,
            "cwd": cwd,
            "task_id": task_id,
            "session_key": session_key,
            "env_vars": env_vars,
            "use_pty": use_pty,
        })
        session = _FakeSession("proc_test123", 4242)
        self.sessions[session.id] = session
        return session

    def get(self, session_id):
        return self.sessions.get(session_id)

    def wait(self, session_id, timeout=None):
        self.wait_calls.append((session_id, timeout))
        session = self.sessions[session_id]
        session.exited = True
        session.exit_code = 0
        return {"status": "exited", "exit_code": 0}

    def kill_process(self, session_id):
        self.kill_calls.append(session_id)
        session = self.sessions[session_id]
        session.exited = True
        session.exit_code = -15
        return {"status": "killed", "session_id": session_id}


def _write_git_repo(path: Path) -> None:
    (path / ".git").mkdir(parents=True)


def test_codex_delegate_start_persists_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_git_repo(repo)

    fake_registry = _FakeProcessRegistry()
    monkeypatch.setattr(codex_delegate_tool, "process_registry", fake_registry)
    monkeypatch.setattr(codex_delegate_tool.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(codex_delegate_tool, "_resolve_git_dir", lambda _workdir: repo / ".git")
    monkeypatch.setattr(codex_delegate_tool, "describe_existing_ctx_binding", lambda _task_id: None)

    result = json.loads(codex_delegate_tool.codex_delegate(
        action="start",
        prompt="Implement the feature",
        phase="implement",
        task_id="sess-1",
        workdir=str(repo),
    ))

    assert result["status"] == "running"
    assert result["process_session_id"] == "proc_test123"
    assert fake_registry.spawn_calls
    assert "codex exec --json" in fake_registry.spawn_calls[0]["command"]
    assert "--dangerously-bypass-approvals-and-sandbox" in fake_registry.spawn_calls[0]["command"]

    runs = json.loads((tmp_path / "home" / "codex" / "runs.json").read_text(encoding="utf-8"))
    assert result["run_id"] in runs["runs"]


def test_codex_delegate_status_parses_codex_events(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_dir = repo / ".git"
    git_dir.mkdir()

    fake_registry = _FakeProcessRegistry()
    fake_registry.sessions["proc_test123"] = _FakeSession(
        "proc_test123",
        4242,
        output_buffer="\n".join([
            '{"type":"thread.started","thread_id":"thread-123"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ready"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
        ]),
        exited=True,
        exit_code=0,
    )
    monkeypatch.setattr(codex_delegate_tool, "process_registry", fake_registry)
    monkeypatch.setattr(codex_delegate_tool.shutil, "which", lambda _name: "/usr/bin/codex")

    record = {
        "run_id": "codex_test123",
        "status": "running",
        "phase": "implement",
        "workdir": str(repo),
        "task_id": "sess-1",
        "process_session_id": "proc_test123",
        "last_message_path": str(git_dir / "hermes-codex" / "codex_test123.last-message.txt"),
        "record_path": str(git_dir / "hermes-codex" / "codex_test123.json"),
        "latest_path": str(git_dir / "hermes-codex" / "latest.json"),
    }
    last_message = Path(record["last_message_path"])
    last_message.parent.mkdir(parents=True, exist_ok=True)
    last_message.write_text("final answer", encoding="utf-8")
    codex_delegate_tool._persist_record(record)

    result = json.loads(codex_delegate_tool.codex_delegate(action="status", run_id="codex_test123"))
    assert result["status"] == "completed"
    assert result["codex_session_id"] == "thread-123"
    assert result["last_agent_message"] == "ready"
    assert result["final_message"] == "final answer"


def test_codex_delegate_resume_uses_existing_codex_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_git_repo(repo)

    fake_registry = _FakeProcessRegistry()
    monkeypatch.setattr(codex_delegate_tool, "process_registry", fake_registry)
    monkeypatch.setattr(codex_delegate_tool.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(codex_delegate_tool, "_resolve_git_dir", lambda _workdir: repo / ".git")
    monkeypatch.setattr(codex_delegate_tool, "describe_existing_ctx_binding", lambda _task_id: None)

    record = {
        "run_id": "codex_parent",
        "status": "completed",
        "phase": "implement",
        "workdir": str(repo),
        "task_id": "sess-1",
        "process_session_id": "proc_parent",
        "codex_session_id": "thread-abc",
        "record_path": str(repo / ".git" / "hermes-codex" / "codex_parent.json"),
        "latest_path": str(repo / ".git" / "hermes-codex" / "latest.json"),
        "last_message_path": str(repo / ".git" / "hermes-codex" / "codex_parent.last-message.txt"),
    }
    fake_registry.sessions["proc_parent"] = _FakeSession("proc_parent", 1111, exited=True, exit_code=0)
    codex_delegate_tool._persist_record(record)

    result = json.loads(codex_delegate_tool.codex_delegate(
        action="resume",
        run_id="codex_parent",
        prompt="Address the failing test and rerun it.",
        phase="follow-up",
    ))

    assert result["status"] == "running"
    assert result["run_id"] != "codex_parent"
    assert fake_registry.spawn_calls
    assert "codex exec resume" in fake_registry.spawn_calls[0]["command"]
    assert "thread-abc" in fake_registry.spawn_calls[0]["command"]


def test_codex_delegate_start_reuses_active_external_key(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_git_repo(repo)

    fake_registry = _FakeProcessRegistry()
    fake_registry.sessions["proc_active"] = _FakeSession("proc_active", 5252, output_buffer="", exited=False)
    monkeypatch.setattr(codex_delegate_tool, "process_registry", fake_registry)
    monkeypatch.setattr(codex_delegate_tool.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(codex_delegate_tool, "_resolve_git_dir", lambda _workdir: repo / ".git")
    monkeypatch.setattr(codex_delegate_tool, "describe_existing_ctx_binding", lambda _task_id: None)

    record = {
        "run_id": "codex_active",
        "status": "running",
        "phase": "implement",
        "workdir": str(repo),
        "task_id": "sess-1",
        "process_session_id": "proc_active",
        "external_key": "linear:HAD-300",
        "record_path": str(repo / ".git" / "hermes-codex" / "codex_active.json"),
        "latest_path": str(repo / ".git" / "hermes-codex" / "latest.json"),
        "last_message_path": str(repo / ".git" / "hermes-codex" / "codex_active.last-message.txt"),
    }
    codex_delegate_tool._persist_record(record)

    result = json.loads(
        codex_delegate_tool.codex_delegate(
            action="start",
            prompt="Implement the next slice",
            phase="implement",
            task_id="sess-1",
            workdir=str(repo),
            external_key="linear:HAD-300",
        )
    )

    assert result["status"] == "running"
    assert result["run_id"] == "codex_active"
    assert result["skipped_existing"] is True
    assert fake_registry.spawn_calls == []
