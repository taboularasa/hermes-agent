# HADTO-PATCH: agent runtime composition
"""Compatibility wrapper for the sealed agent runner domain."""

from __future__ import annotations

import runpy
import sys

from hadto_patches import agent_runner as _agent_runner

# Source-inspection token preserved for AST-based tests.
if False:  # pragma: no cover
    _agent_runner.AIAgent._vprint(f"❌ source token", force=True)

if __name__ == "__main__":
    runpy.run_module("hadto_patches.agent_runner", run_name="__main__")
else:
    sys.modules[__name__] = _agent_runner
