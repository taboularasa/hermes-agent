"""Typed execution frame for Hermes planning and delegation workflows."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Actor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    role: str
    responsibility: Optional[str] = None


class Artifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    kind: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    source: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    collected_at: Optional[str] = None


class Commitment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commitment: str
    owner: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None


class VerificationTarget(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: str
    method: Optional[str] = None
    command: Optional[str] = None
    success_criteria: Optional[str] = None


class ExecutionFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    frame_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=_utc_now_iso)
    source: Optional[str] = None

    goals: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    actors: List[Actor] = Field(default_factory=list)
    artifacts: List[Artifact] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    commitments: List[Commitment] = Field(default_factory=list)
    verification_targets: List[VerificationTarget] = Field(default_factory=list)

    assumptions: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    def to_prompt(self) -> str:
        """Render the execution frame for inclusion in prompts."""
        def render_list(items: Iterable[str]) -> List[str]:
            rendered = [f"- {item}" for item in items if item]
            return rendered or ["- (none specified)"]

        lines: List[str] = ["EXECUTION FRAME (typed)"]
        lines.append("Goals:")
        lines.extend(render_list(self.goals))
        lines.append("Constraints:")
        lines.extend(render_list(self.constraints))
        lines.append("Actors:")
        if self.actors:
            for actor in self.actors:
                suffix = f" — {actor.responsibility}" if actor.responsibility else ""
                lines.append(f"- {actor.name} ({actor.role}){suffix}")
        else:
            lines.append("- (none specified)")
        lines.append("Artifacts:")
        if self.artifacts:
            for artifact in self.artifacts:
                details = []
                if artifact.kind:
                    details.append(artifact.kind)
                if artifact.location:
                    details.append(artifact.location)
                meta = f" [{', '.join(details)}]" if details else ""
                lines.append(f"- {artifact.name}{meta}")
        else:
            lines.append("- (none specified)")
        lines.append("Evidence:")
        if self.evidence:
            for evidence in self.evidence:
                details = []
                if evidence.source:
                    details.append(evidence.source)
                if evidence.location:
                    details.append(evidence.location)
                meta = f" [{', '.join(details)}]" if details else ""
                lines.append(f"- {evidence.description}{meta}")
        else:
            lines.append("- (none specified)")
        lines.append("Commitments:")
        if self.commitments:
            for commitment in self.commitments:
                owner = f" ({commitment.owner})" if commitment.owner else ""
                lines.append(f"- {commitment.commitment}{owner}")
        else:
            lines.append("- (none specified)")
        lines.append("Verification targets:")
        if self.verification_targets:
            for target in self.verification_targets:
                detail = f" — {target.method}" if target.method else ""
                lines.append(f"- {target.target}{detail}")
        else:
            lines.append("- (none specified)")
        return "\n".join(lines)


_SECTION_MAP = {
    "goals": {"goal", "goals", "objective", "objectives"},
    "constraints": {"constraint", "constraints", "requirements", "rules"},
    "actors": {"actors", "people", "owners", "stakeholders"},
    "artifacts": {"artifacts", "deliverables", "outputs", "files"},
    "evidence": {"evidence", "sources", "references"},
    "commitments": {"commitments", "promises", "agreements"},
    "verification_targets": {"verification", "validation", "tests", "checks"},
}


def _normalize_section(label: str) -> Optional[str]:
    cleaned = re.sub(r"[^a-zA-Z ]", " ", label).strip().lower()
    for key, aliases in _SECTION_MAP.items():
        if cleaned in aliases:
            return key
    return None


def _parse_sectioned_context(context: str) -> Dict[str, List[str]]:
    lines = [line.rstrip() for line in context.splitlines()]
    sections: Dict[str, List[str]] = {key: [] for key in _SECTION_MAP.keys()}
    current: Optional[str] = None
    header_re = re.compile(r"^\s*([A-Za-z0-9 _-]+):\s*(.*)$")

    for line in lines:
        if not line.strip():
            continue
        match = header_re.match(line)
        if match:
            label, remainder = match.groups()
            normalized = _normalize_section(label)
            if normalized:
                current = normalized
                if remainder.strip():
                    sections[current].append(remainder.strip())
                continue
        if line.lstrip().startswith(('-', '*')):
            item = line.lstrip()[1:].strip()
            if current and item:
                sections[current].append(item)
                continue
        if current and line.strip():
            sections[current].append(line.strip())

    return {k: v for k, v in sections.items() if v}


def _coerce_frame_input(frame: Any) -> Optional[Dict[str, Any]]:
    if frame is None:
        return None
    if isinstance(frame, ExecutionFrame):
        return frame.model_dump()
    if isinstance(frame, dict):
        return frame
    if isinstance(frame, str):
        try:
            parsed = json.loads(frame)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def build_execution_frame(
    *,
    goal: Optional[str] = None,
    context: Optional[str] = None,
    frame: Any = None,
    source: Optional[str] = None,
    include_default_actors: bool = True,
) -> ExecutionFrame:
    """Create a typed ExecutionFrame from explicit input and optional context hints."""
    raw = _coerce_frame_input(frame) or {}
    try:
        model = ExecutionFrame.model_validate(raw)
    except ValidationError:
        model = ExecutionFrame()

    if source and not model.source:
        model.source = source
    if goal and not model.goals:
        model.goals = [goal.strip()]

    if context:
        parsed = _parse_sectioned_context(context)
        if parsed.get("goals") and not model.goals:
            model.goals = parsed["goals"]
        if parsed.get("constraints") and not model.constraints:
            model.constraints = parsed["constraints"]
        if parsed.get("actors") and not model.actors:
            model.actors = [Actor(name=text, role="unspecified") for text in parsed["actors"]]
        if parsed.get("artifacts") and not model.artifacts:
            model.artifacts = [Artifact(name=text) for text in parsed["artifacts"]]
        if parsed.get("evidence") and not model.evidence:
            model.evidence = [Evidence(description=text) for text in parsed["evidence"]]
        if parsed.get("commitments") and not model.commitments:
            model.commitments = [Commitment(commitment=text) for text in parsed["commitments"]]
        if parsed.get("verification_targets") and not model.verification_targets:
            model.verification_targets = [VerificationTarget(target=text) for text in parsed["verification_targets"]]

    if include_default_actors and not model.actors:
        model.actors = [
            Actor(name="Hermes", role="parent_agent", responsibility="coordination and delegation"),
            Actor(name="Subagent", role="executor", responsibility="execute the delegated task"),
        ]

    return model

