from unittest.mock import MagicMock, patch

from run_agent import AIAgent

from tests.run_agent.test_run_agent import _make_tool_defs, _mock_response


def _make_agent() -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("terminal")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        return agent


def _prepare_agent(agent: AIAgent) -> None:
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False


def test_run_conversation_injects_query_ready_cq_governance_for_ontology_cq_requests() -> None:
    agent = _make_agent()
    _prepare_agent(agent)
    captured = {}

    def _fake_api_call(api_kwargs):
        captured.update(api_kwargs)
        return _mock_response(content="done", finish_reason="stop")

    with (
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
    ):
        result = agent.run_conversation(
            "Rewrite these ontology competency questions into query-ready CQ YAML."
        )

    assert result["completed"] is True
    system_prompt = captured["messages"][0]["content"]
    assert "docs/operations/query-ready-cq-governance.yaml" in system_prompt
    assert "field_value_lookup" in system_prompt
    assert "free-form competency questions are incomplete" in system_prompt


def test_run_conversation_skips_query_ready_cq_governance_for_unrelated_requests() -> None:
    agent = _make_agent()
    _prepare_agent(agent)
    captured = {}

    def _fake_api_call(api_kwargs):
        captured.update(api_kwargs)
        return _mock_response(content="done", finish_reason="stop")

    with (
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
    ):
        result = agent.run_conversation("Summarize the recent CLI changes.")

    assert result["completed"] is True
    system_prompt = captured["messages"][0]["content"]
    assert "docs/operations/query-ready-cq-governance.yaml" not in system_prompt
