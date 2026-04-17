"""Compatibility helpers for legacy Honcho call sites."""

from __future__ import annotations


def set_session_context(_honcho_manager, _session_key: str) -> None:
    """Retained for backward compatibility with legacy agent runner code."""
    return None
