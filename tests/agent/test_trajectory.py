import json

from agent.trajectory import save_trajectory


def test_save_trajectory_redacts_message_content(tmp_path):
    output = tmp_path / "trajectory.jsonl"
    trajectory = [
        {"from": "human", "value": "Use key sk-proj-abc123def456ghi789jkl012"},
        {"role": "assistant", "content": "Authorization: Bearer sk-proj-abc123def456ghi789jkl012"},
    ]

    save_trajectory(trajectory, model="test-model", completed=True, filename=str(output))

    payload = json.loads(output.read_text(encoding="utf-8").strip())
    serialized = json.dumps(payload)
    assert "abc123def456" not in serialized
    assert "sk-pro" in serialized
