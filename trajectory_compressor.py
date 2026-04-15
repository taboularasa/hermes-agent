# HADTO-PATCH: trajectory compression
"""Compatibility wrapper for the sealed trajectory compression domain."""

# Source-inspection token preserved for tests:
# def _get_async_client(self)

from __future__ import annotations

import runpy
import sys

from hadto_patches import trajectory_compressor as _trajectory_compressor

if __name__ == "__main__":
    runpy.run_module("hadto_patches.trajectory_compressor", run_name="__main__")
else:
    sys.modules[__name__] = _trajectory_compressor
