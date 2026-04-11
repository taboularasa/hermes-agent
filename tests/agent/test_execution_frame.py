from agent.execution_frame import build_execution_frame


def test_build_execution_frame_minimal():
    frame = build_execution_frame(goal="Ship the fix", source="test")
    assert frame.goals == ["Ship the fix"]
    assert frame.constraints == []
    assert len(frame.actors) == 2
    assert frame.actors[0].name == "Hermes"


def test_build_execution_frame_parses_sectioned_context():
    context = """
Constraints:
- must keep API stable
Actors:
- Hermes (parent)
Artifacts:
- docs/plan.md
Evidence:
- journal entry 2026-04-10
Commitments:
- deliver by Friday
Verification:
- run pytest tests/tools/test_delegate.py
"""
    frame = build_execution_frame(goal="Implement", context=context, source="test")
    assert "must keep API stable" in frame.constraints
    assert frame.actors[0].name
    assert frame.artifacts[0].name == "docs/plan.md"
    assert frame.evidence[0].description == "journal entry 2026-04-10"
    assert frame.commitments[0].commitment == "deliver by Friday"
    assert frame.verification_targets[0].target == "run pytest tests/tools/test_delegate.py"


def test_build_execution_frame_does_not_override_explicit_goals():
    frame = build_execution_frame(
        goal="Ignored",
        frame={"goals": ["Use explicit goals"], "constraints": ["No drift"]},
        source="test",
    )
    assert frame.goals == ["Use explicit goals"]
    assert frame.constraints == ["No drift"]
