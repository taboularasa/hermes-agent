# HADTO-PATCH: cron topology
"""Compatibility wrapper for the sealed cron job storage domain."""

from __future__ import annotations

import sys

from hadto_patches import cron_jobs as _cron_jobs

sys.modules[__name__] = _cron_jobs
