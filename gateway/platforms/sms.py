# HADTO-PATCH: security
"""Compatibility wrapper for the sealed SMS adapter domain."""

from __future__ import annotations

import sys

from hadto_patches import platform_sms as _platform_sms

sys.modules[__name__] = _platform_sms
