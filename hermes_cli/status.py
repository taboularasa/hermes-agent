# HADTO-PATCH: ctx.rs integration
"""Compatibility wrapper for the sealed status CLI domain."""

from __future__ import annotations

import sys

from hadto_patches import status_cli as _status_cli

sys.modules[__name__] = _status_cli
