from hermes_cli.ctx_runtime import CtxBinding


def test_aiagent_includes_ctx_note_in_system_prompt(monkeypatch):
    import run_agent

    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **_kwargs: [])
    monkeypatch.setattr(
        run_agent,
        "maybe_bind_ctx_session",
        lambda **kwargs: CtxBinding(
            active=True,
            reason="ctx task bound",
            session_id=kwargs["session_id"],
            platform=kwargs.get("platform") or "cli",
            workspace_id="ws-1",
            task_id="task-1",
            worktree_id="wt-1",
            worktree_path="/tmp/ctx-worktree",
        ),
    )

    agent = run_agent.AIAgent(
        model="test/model",
        api_key="test-key",
        base_url="https://example.com/v1",
        enabled_toolsets=["terminal"],
        session_id="sess-1",
        platform="cli",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )

    prompt = agent._build_system_prompt()
    assert "ctx task `task-1`" in prompt
    assert "/tmp/ctx-worktree" in prompt


def test_aiagent_compression_transfers_ctx_binding(monkeypatch, tmp_path):
    import run_agent
    from hermes_state import SessionDB

    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **_kwargs: [])
    monkeypatch.setattr(
        run_agent,
        "maybe_bind_ctx_session",
        lambda **kwargs: CtxBinding(
            active=True,
            reason="ctx task bound",
            session_id=kwargs["session_id"],
            platform=kwargs.get("platform") or "cli",
            workspace_id="ws-1",
            task_id="task-1",
            worktree_id="wt-1",
            worktree_path="/tmp/ctx-worktree",
        ),
    )

    transfers = []

    def _fake_transfer(old_session_id, new_session_id, *, reason):
        transfers.append((old_session_id, new_session_id, reason))
        return CtxBinding(
            active=True,
            reason=reason,
            session_id=new_session_id,
            platform="cli",
            workspace_id="ws-1",
            task_id="task-1",
            worktree_id="wt-1",
            worktree_path="/tmp/ctx-worktree",
        )

    monkeypatch.setattr(run_agent, "transfer_ctx_binding", _fake_transfer)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        agent = run_agent.AIAgent(
            model="test/model",
            api_key="test-key",
            base_url="https://example.com/v1",
            enabled_toolsets=["terminal"],
            session_id="sess-1",
            platform="cli",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=db,
        )

        monkeypatch.setattr(agent, "flush_memories", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            agent.context_compressor,
            "compress",
            lambda messages, current_tokens=None: list(messages),
        )
        monkeypatch.setattr(agent, "_build_system_prompt", lambda *_args, **_kwargs: "system")

        old_session_id = agent.session_id
        agent._compress_context(
            [{"role": "user", "content": "hello"}],
            "system",
        )

        assert transfers
        assert transfers[0][0] == old_session_id
        assert transfers[0][1] == agent.session_id
        assert agent._ctx_binding.session_id == agent.session_id
        assert agent._ctx_binding.reason == "ctx binding transferred after compression"
    finally:
        db.close()
