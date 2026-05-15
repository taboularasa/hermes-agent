import json

from agent.trajectory import save_trajectory


def test_save_trajectory_redacts_without_mutating_input(tmp_path):
    raw_secret = "sk-testsecret1234567890"
    trajectory = [
        {"from": "human", "value": f"token={raw_secret}"},
        {"role": "tool", "content": f"Authorization: Bearer {raw_secret}"},
    ]

    output = tmp_path / "trajectory.jsonl"
    save_trajectory(trajectory, "test-model", True, filename=str(output))

    record = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(record)

    assert raw_secret not in serialized
    assert raw_secret in trajectory[0]["value"]
    assert raw_secret in trajectory[1]["content"]
