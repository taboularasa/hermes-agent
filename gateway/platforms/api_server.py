# HADTO-PATCH: security
"""Compatibility wrapper for the sealed API server adapter domain."""

from __future__ import annotations

import sys

from hadto_patches import platform_api_server as _platform_api_server

sys.modules[__name__] = _platform_api_server
