# HADTO-PATCH: agent runtime composition
"""Compatibility wrapper for the sealed agent runner domain."""

from __future__ import annotations

import runpy
import sys

from hadto_patches import agent_runner as _agent_runner

if __name__ == "__main__":
    runpy.run_module("hadto_patches.agent_runner", run_name="__main__")
else:
    sys.modules[__name__] = _agent_runner
