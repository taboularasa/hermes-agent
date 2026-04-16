#!/usr/bin/env python3
# HADTO-PATCH: plugin registry
"""Compatibility wrapper for the sealed toolset resolution domain."""

from __future__ import annotations

import sys

from hadto_patches import toolsets as _toolsets

sys.modules[__name__] = _toolsets
