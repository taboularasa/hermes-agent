# HADTO-PATCH: gateway composition
"""Compatibility wrapper for the sealed gateway CLI domain."""

from __future__ import annotations

import sys

from hadto_patches import gateway_cli as _gateway_cli

sys.modules[__name__] = _gateway_cli
