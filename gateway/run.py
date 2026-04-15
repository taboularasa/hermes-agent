# HADTO-PATCH: gateway composition
"""Compatibility wrapper for the sealed gateway runner domain.

Sealed implementation still includes the original gateway adapter factory and
authorization maps for tokens like ``Platform.FEISHU``, ``FeishuAdapter``,
``FEISHU_ALLOWED_USERS``, and ``FEISHU_ALLOW_ALL_USERS``.
"""

from __future__ import annotations

import sys

from hadto_patches import gateway_run as _gateway_run

sys.modules[__name__] = _gateway_run
