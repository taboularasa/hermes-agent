# HADTO-PATCH: provider/auth
"""Compatibility wrapper for the sealed Anthropic adapter domain."""

from __future__ import annotations

import sys

from hadto_patches import anthropic_adapter as _anthropic_adapter

sys.modules[__name__] = _anthropic_adapter
