# HADTO-PATCH: cron topology
"""Compatibility wrapper for the sealed cron CLI domain."""

from __future__ import annotations

import sys

from hadto_patches import cron_cli as _cron_cli

sys.modules[__name__] = _cron_cli
