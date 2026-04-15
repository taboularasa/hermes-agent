# HADTO-PATCH: cron topology
"""Compatibility wrapper for the sealed cron scheduler domain."""

from __future__ import annotations

import sys

from hadto_patches import cron_scheduler as _cron_scheduler

sys.modules[__name__] = _cron_scheduler
