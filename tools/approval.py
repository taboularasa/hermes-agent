# HADTO-PATCH: security
"""Compatibility wrapper for the sealed command-approval policy domain."""

from __future__ import annotations

import sys

from hadto_patches import security as _security

sys.modules[__name__] = _security
