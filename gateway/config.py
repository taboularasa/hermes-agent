# HADTO-PATCH: security
"""Compatibility wrapper for the sealed gateway config domain."""

from __future__ import annotations

import sys

from hadto_patches import gateway_config as _gateway_config

sys.modules[__name__] = _gateway_config
