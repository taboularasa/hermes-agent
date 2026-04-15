# HADTO-PATCH: ctx.rs integration
"""Compatibility wrapper for the sealed ctx.rs integration domain."""

from __future__ import annotations

import sys

from hadto_patches import ctx as _ctx

sys.modules[__name__] = _ctx
