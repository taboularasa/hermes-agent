# HADTO-PATCH: env
"""Compatibility wrapper for the sealed Doppler env-loading domain."""

from __future__ import annotations

import sys

from hadto_patches import env as _env

sys.modules[__name__] = _env
