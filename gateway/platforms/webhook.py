# HADTO-PATCH: security
"""Compatibility wrapper for the sealed webhook adapter domain."""

from __future__ import annotations

import sys

from hadto_patches import platform_webhook as _platform_webhook

sys.modules[__name__] = _platform_webhook
