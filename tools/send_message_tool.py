# HADTO-PATCH: messaging
"""Compatibility wrapper for the sealed messaging transport domain."""

from __future__ import annotations

import sys

from hadto_patches import send_message as _send_message

sys.modules[__name__] = _send_message
