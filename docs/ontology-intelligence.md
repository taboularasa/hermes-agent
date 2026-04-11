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
- `/home/david/stacks/smb-ontology-platform/research/agenda/*.yaml`
- `/home/david/stacks/smb-ontology-platform/research/retrospectives/**/*.yaml`
- `/home/david/stacks/smb-ontology-platform/research/source_store/**`
- `/home/david/stacks/smb-ontology-platform/orsd/*.yaml`
- `/home/david/stacks/smb-ontology-platform/docs/plans/*keet-ontology-engineering-progress-tracker.md`
- `/home/david/stacks/smb-ontology-platform/docs/plans/*keet-ontology-engineering-heartbeat.md`
- `/home/david/stacks/smb-ontology-platform/docs/issues/ONT-009-*.md`

## Actions

- `snapshot`
  - Platform and vertical overview, business recommendations, and research asset counts.
- `ontology_engineering`
  - Reads the ontology textbook study control surface, current ontology backlog, and runtime search-provider status to produce concrete Hermes upgrade targets and a business-domain research protocol.
- `self_improvement`
  - Reliability summary for ontology artifacts plus maintenance/growth/capability candidates, textbook-study-driven upgrade targets, and search-provider coverage gaps.
- `consulting_context`
  - Maps a client brief to likely ontology verticals, bounded contexts, discovery questions, proof points, and a multi-provider ontology research protocol.
- `sales_context`
  - Maps a prospect brief to vertical fit, outreach angles, discovery prompts, proof points, and a multi-provider ontology research protocol.
- `source_materials`
  - Summarizes manifests, source-store volume, and recent source material captures.
- `vertical_detail`
  - Returns the profile for a single ontology vertical.

## Design rules

- Do not dump raw ontology artifacts into prompts by default.
- Query `ontology_context` and carry only the compact context pack relevant to the task.
- Treat stale or missing ontology reports as reliability-floor evidence.
- Treat ontology conversion bottlenecks and business recommendations as growth or capability candidates unless they also indicate stale/missing operating knowledge.
- For ontology business-domain research, use `web_search_matrix` first so Hermes compares all available providers before committing to source capture or ontology changes.
- If `ontology_context(action="ontology_engineering")` reports missing provider coverage, treat that as a runtime/configuration gap instead of silently falling back to one backend.

## Verification

- `pytest tests/agent/test_ontology_context.py -q`
- `pytest tests/tools/test_self_improvement_tool.py -q`
- `pytest tests/tools/test_linear_issue_tool.py -q`
- `pytest tests/tools/test_web_tools_config.py -q`
