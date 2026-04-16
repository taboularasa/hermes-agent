# HADTO-PATCH: plugin registry
"""Compatibility wrapper for the sealed tool registry domain."""

from __future__ import annotations

import sys

from hadto_patches import registry as _registry

sys.modules[__name__] = _registry
