# HADTO-PATCH: security
"""Compatibility wrapper for the sealed channel-directory domain."""

from __future__ import annotations

import sys

from hadto_patches import channel_directory as _channel_directory

sys.modules[__name__] = _channel_directory
