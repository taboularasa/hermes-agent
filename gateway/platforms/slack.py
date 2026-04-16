# HADTO-PATCH: security
"""Compatibility wrapper for the sealed Slack adapter domain."""

from __future__ import annotations

import sys

from hadto_patches import platform_slack as _platform_slack

sys.modules[__name__] = _platform_slack
