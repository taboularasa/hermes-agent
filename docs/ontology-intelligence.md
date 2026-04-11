# Ontology Intelligence

Hermes now treats Hadto ontology artifacts as a first-class reasoning surface instead of a side-channel research system.

## Surfaces

- Tool: `ontology_context`
- Core parser: `agent/ontology_context.py`
- Reliability integration: `tools/self_improvement_tool.py`

## What the tool reads

- `/home/david/stacks/smb-ontology-platform/evolution/metrics.json`
- `/home/david/stacks/smb-ontology-platform/evolution/delta_report.json`
- `/home/david/stacks/smb-ontology-platform/evolution/daily_report.md`
- `/home/david/stacks/smb-ontology-platform/evolution/logs/*.json`
- `/home/david/stacks/smb-ontology-platform/research/manifests/**/*.yaml`
- `/home/david/stacks/smb-ontology-platform/research/prompt_proposals/**/*.yaml`
- `/home/david/stacks/smb-ontology-platform/research/source_store/**`
- `/home/david/stacks/smb-ontology-platform/orsd/*.yaml`

## Actions

- `snapshot`
  - Platform and vertical overview, business recommendations, and research asset counts.
- `self_improvement`
  - Reliability summary for ontology artifacts plus maintenance/growth/capability candidates.
- `consulting_context`
  - Maps a client brief to likely ontology verticals, bounded contexts, discovery questions, and proof points.
- `sales_context`
  - Maps a prospect brief to vertical fit, outreach angles, discovery prompts, and proof points.
- `source_materials`
  - Summarizes manifests, source-store volume, and recent source material captures.
- `vertical_detail`
  - Returns the profile for a single ontology vertical.

## Design rules

- Do not dump raw ontology artifacts into prompts by default.
- Query `ontology_context` and carry only the compact context pack relevant to the task.
- Treat stale or missing ontology reports as reliability-floor evidence.
- Treat ontology conversion bottlenecks and business recommendations as growth or capability candidates unless they also indicate stale/missing operating knowledge.

## Verification

- `pytest tests/agent/test_ontology_context.py -q`
- `pytest tests/tools/test_self_improvement_tool.py -q`
- `pytest tests/tools/test_linear_issue_tool.py -q`
