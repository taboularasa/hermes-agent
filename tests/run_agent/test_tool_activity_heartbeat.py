import time

from run_agent import AIAgent


def _agent_stub() -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent._current_tool = "slow_tool"
    agent._last_activity_ts = time.time() - 100
    agent._last_activity_desc = "executing tool: slow_tool"
    return agent


def test_single_tool_heartbeat_touches_activity_while_tool_is_current():
    agent = _agent_stub()

    stop = agent._start_tool_activity_heartbeat("slow_tool", interval=0.02)
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if "(0s elapsed)" in agent._last_activity_desc:
                break
            time.sleep(0.01)
    finally:
        stop.set()

    assert agent._last_activity_desc == "executing tool: slow_tool (0s elapsed)"
    assert time.time() - agent._last_activity_ts < 1.0


def test_single_tool_heartbeat_stops_when_current_tool_changes():
    agent = _agent_stub()

    stop = agent._start_tool_activity_heartbeat("slow_tool", interval=0.02)
    agent._current_tool = "other_tool"
    try:
        time.sleep(0.08)
    finally:
        stop.set()

    assert agent._last_activity_desc == "executing tool: slow_tool"
