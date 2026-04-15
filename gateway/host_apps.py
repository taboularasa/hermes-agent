# HADTO-PATCH: misc
"""Compatibility wrapper for the sealed host-app discovery domain."""

from __future__ import annotations

import sys

from hadto_patches import host_apps as _host_apps

sys.modules[__name__] = _host_apps
