# HADTO-PATCH: security
"""Compatibility wrapper for the sealed cron tool domain."""

from __future__ import annotations

import sys

from hadto_patches import cron_tools as _cron_tools

sys.modules[__name__] = _cron_tools
