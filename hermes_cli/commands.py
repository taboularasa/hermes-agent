# HADTO-PATCH: gateway composition
"""Compatibility wrapper for the sealed command registry domain."""

from __future__ import annotations

import sys

from hadto_patches import commands as _commands

sys.modules[__name__] = _commands
