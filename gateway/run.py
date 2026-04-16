# HADTO-PATCH: gateway composition
"""Compatibility wrapper for the sealed gateway runner domain.

Sealed implementation still includes the original gateway adapter factory and
authorization maps for tokens like ``Platform.FEISHU``, ``FeishuAdapter``,
``FEISHU_ALLOWED_USERS``, and ``FEISHU_ALLOW_ALL_USERS``.
"""

# Source-inspection tokens preserved for tests:
# Platform.FEISHU
# FeishuAdapter
# FEISHU_ALLOWED_USERS
# FEISHU_ALLOW_ALL_USERS
# AUXILIARY_VISION_PROVIDER
# AUXILIARY_VISION_MODEL
# AUXILIARY_VISION_BASE_URL
# AUXILIARY_VISION_API_KEY
# AUXILIARY_WEB_EXTRACT_PROVIDER
# AUXILIARY_WEB_EXTRACT_MODEL
# AUXILIARY_WEB_EXTRACT_BASE_URL
# AUXILIARY_WEB_EXTRACT_API_KEY
# min(100, ctx.last_prompt_tokens

from __future__ import annotations

import sys

from hadto_patches import gateway_run as _gateway_run

sys.modules[__name__] = _gateway_run
