# HADTO-PATCH: web provider routing
"""Compatibility wrapper for the sealed web tooling domain."""

from __future__ import annotations

import runpy
import sys

from hadto_patches import web_tools as _web_tools

if __name__ == "__main__":
    runpy.run_module("hadto_patches.web_tools", run_name="__main__")
else:
    sys.modules[__name__] = _web_tools
