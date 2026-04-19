"""Zep-backed memory provider for Hermes.

Uses the Zep Cloud SDK's current user/thread/context API shape:

- one stable Zep user per Hermes user/profile scope
- one Zep thread per Hermes session
- one dedicated notes thread for mirrored built-in memory writes

The provider is context-first. Hermes keeps its built-in memory tools and
mirrors those writes into Zep, while turn-by-turn recall comes from
``thread.get_user_context()`` and ``thread.add_messages(..., return_context=True)``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://api.getzep.com"
_DEFAULT_TIMEOUT_SECS = 10.0
_MAX_MESSAGE_CHARS = 12000
_MEMORY_CONTEXT_RE = re.compile(
    r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>",
    re.IGNORECASE,
)
_SYSTEM_NOTE_RE = re.compile(
    r"\[System note:\s*The following is recalled memory context,\s*NOT new user input\.\s*Treat as informational background data\.\]\s*",
    re.IGNORECASE,
)
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _normalize_api_url(raw_url: str) -> str:
    url = (raw_url or _DEFAULT_API_URL).strip() or _DEFAULT_API_URL
    return url.rstrip("/")


def _sdk_base_url(raw_api_url: str) -> str:
    api_url = _normalize_api_url(raw_api_url)
    if api_url.endswith("/api/v2"):
        return api_url
    return f"{api_url}/api/v2"


def _safe_component(value: str, default: str) -> str:
    cleaned = _SAFE_ID_RE.sub("-", (value or "").strip())
    cleaned = cleaned.strip("-.")
    return cleaned or default


def _clean_message_content(text: str) -> str:
    cleaned = _MEMORY_CONTEXT_RE.sub("", text or "")
    cleaned = _SYSTEM_NOTE_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _MAX_MESSAGE_CHARS:
        cleaned = cleaned[:_MAX_MESSAGE_CHARS].rstrip()
    return cleaned


def _format_context_block(raw_context: str) -> str:
    context = (raw_context or "").strip()
    if not context:
        return ""
    return f"## Zep Context\n{context}"


def _load_config(hermes_home: Optional[str] = None) -> dict:
    from hermes_constants import get_hermes_home

    base = Path(hermes_home) if hermes_home else get_hermes_home()
    config = {
        "api_key": os.environ.get("ZEP_API_KEY", ""),
        "api_url": os.environ.get("ZEP_API_URL", _DEFAULT_API_URL),
    }

    config_path = base / "zep.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(file_cfg, dict):
                config.update({k: v for k, v in file_cfg.items() if v not in (None, "")})
        except Exception:
            logger.debug("Failed to parse %s", config_path, exc_info=True)

    config["api_url"] = _normalize_api_url(str(config.get("api_url") or _DEFAULT_API_URL))
    return config


def _get_zep_sdk() -> Tuple[type, type, Type[Exception], Type[Exception]]:
    try:
        from zep_cloud import Message, NotFoundError, Zep
        from zep_cloud.core.api_error import ApiError
    except ImportError as exc:
        raise RuntimeError(
            "zep-cloud package not installed. Run: pip install zep-cloud"
        ) from exc
    return Zep, Message, NotFoundError, ApiError


def _sdk_is_available() -> bool:
    try:
        _get_zep_sdk()
        return True
    except RuntimeError:
        return False


class ZepMemoryProvider(MemoryProvider):
    """Zep Cloud-backed long-term memory."""

    def __init__(self) -> None:
        self._config: dict = {}
        self._client = None
        self._message_cls = None
        self._api_error_cls: Type[Exception] = Exception
        self._not_found_cls: Type[Exception] = Exception
        self._client_lock = threading.RLock()

        self._active = False
        self._read_enabled = True
        self._write_enabled = True

        self._api_key = ""
        self._api_url = _DEFAULT_API_URL
        self._zep_user_id = ""
        self._session_id = ""
        self._session_thread_id = ""
        self._notes_thread_id = ""

        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None

        self._user_ready = False
        self._session_thread_ready = False
        self._notes_thread_ready = False

    @property
    def name(self) -> str:
        return "zep"

    def is_available(self) -> bool:
        cfg = _load_config()
        return bool(cfg.get("api_key")) and _sdk_is_available()

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "api_key",
                "description": "Zep API key",
                "secret": True,
                "required": True,
                "env_var": "ZEP_API_KEY",
                "url": "https://app.getzep.com",
            },
            {
                "key": "api_url",
                "description": "Zep API URL",
                "default": _DEFAULT_API_URL,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "zep.json"
        existing = {}
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
            except Exception:
                existing = {}
        existing.update(values)
        config_path.write_text(
            json.dumps(existing, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._config = _load_config(kwargs.get("hermes_home"))
        self._api_key = str(self._config.get("api_key") or "")
        self._api_url = _normalize_api_url(str(self._config.get("api_url") or _DEFAULT_API_URL))

        agent_context = kwargs.get("agent_context", "") or "primary"
        self._read_enabled = agent_context == "primary"
        self._write_enabled = agent_context == "primary"

        identity = _safe_component(str(kwargs.get("agent_identity") or "default"), "default")
        workspace = _safe_component(str(kwargs.get("agent_workspace") or "hermes"), "hermes")
        platform = _safe_component(str(kwargs.get("platform") or "cli"), "cli")
        actor = _safe_component(str(kwargs.get("user_id") or "local-user"), "local-user")
        session_key = _safe_component(str(kwargs.get("parent_session_id") or session_id), "session")

        self._zep_user_id = f"hermes.{workspace}.{identity}.{platform}.{actor}"
        self._session_thread_id = f"{self._zep_user_id}.session.{session_key}"
        self._notes_thread_id = f"{self._zep_user_id}.notes"

        self._user_ready = False
        self._session_thread_ready = False
        self._notes_thread_ready = False

        if not self._api_key:
            self._active = False
            self._client = None
            return

        try:
            zep_cls, message_cls, not_found_cls, api_error_cls = _get_zep_sdk()
            self._message_cls = message_cls
            self._not_found_cls = not_found_cls
            self._api_error_cls = api_error_cls
            self._client = zep_cls(
                api_key=self._api_key,
                base_url=_sdk_base_url(self._api_url),
                timeout=_DEFAULT_TIMEOUT_SECS,
            )
            self._active = True
            self._ensure_user_exists()
            self._ensure_thread_exists(self._session_thread_id)
            self._ensure_thread_exists(self._notes_thread_id, is_notes=True)
            self._warm_user_graph()
        except Exception:
            logger.warning("Zep initialization failed", exc_info=True)
            self._active = False
            self._client = None

    def system_prompt_block(self) -> str:
        if not self._active or not (self._read_enabled or self._write_enabled):
            return ""
        lines = [
            "# Zep Memory",
            "Active. Relevant long-term context may be injected automatically from prior conversations.",
        ]
        if self._write_enabled:
            lines.append("Built-in memory writes are mirrored into Zep.")
        return "\n".join(lines)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._active or not self._read_enabled or not self._client:
            return ""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)

        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""

        if result:
            return result
        return self._fetch_context()

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._active or not self._read_enabled or not self._client:
            return

        def _run() -> None:
            context = self._fetch_context()
            if context:
                with self._prefetch_lock:
                    self._prefetch_result = context

        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=2.0)
        self._prefetch_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="zep-prefetch",
        )
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._active or not self._write_enabled or not self._client or not self._message_cls:
            return

        clean_user = _clean_message_content(user_content)
        clean_assistant = _clean_message_content(assistant_content)
        if not clean_user and not clean_assistant:
            return

        def _run() -> None:
            try:
                self._ensure_thread_exists(self._session_thread_id)
                messages = []
                if clean_user:
                    messages.append(
                        self._message_cls(
                            role="user",
                            content=clean_user,
                            metadata={"source": "hermes", "type": "conversation_turn"},
                        )
                    )
                if clean_assistant:
                    messages.append(
                        self._message_cls(
                            role="assistant",
                            content=clean_assistant,
                            metadata={"source": "hermes", "type": "conversation_turn"},
                        )
                    )
                if not messages:
                    return
                response = self._client.thread.add_messages(
                    self._session_thread_id,
                    messages=messages,
                    return_context=True,
                )
                context = _format_context_block(getattr(response, "context", "") or "")
                if context:
                    with self._prefetch_lock:
                        self._prefetch_result = context
            except Exception:
                logger.debug("Zep sync_turn failed", exc_info=True)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        self._sync_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="zep-sync",
        )
        self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if (
            not self._active
            or not self._write_enabled
            or not self._client
            or not self._message_cls
            or action != "add"
        ):
            return

        note = (content or "").strip()
        if not note:
            return

        def _run() -> None:
            try:
                self._ensure_thread_exists(self._notes_thread_id, is_notes=True)
                message = self._message_cls(
                    role="user",
                    content=f"Persistent {target} memory from Hermes: {note}",
                    metadata={
                        "source": "hermes_memory",
                        "target": target,
                        "type": "explicit_memory",
                    },
                )
                self._client.thread.add_messages(
                    self._notes_thread_id,
                    messages=[message],
                )
            except Exception:
                logger.debug("Zep memory mirror failed", exc_info=True)

        if self._write_thread and self._write_thread.is_alive():
            self._write_thread.join(timeout=2.0)
        self._write_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="zep-memory-write",
        )
        self._write_thread.start()

    def shutdown(self) -> None:
        for attr_name in ("_prefetch_thread", "_sync_thread", "_write_thread"):
            thread = getattr(self, attr_name, None)
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
            setattr(self, attr_name, None)
        self._close_client()

    def _fetch_context(self) -> str:
        if not self._client or not self._read_enabled:
            return ""
        try:
            self._ensure_thread_exists(self._session_thread_id)
            response = self._client.thread.get_user_context(self._session_thread_id)
            return _format_context_block(getattr(response, "context", "") or "")
        except Exception:
            logger.debug("Zep prefetch failed", exc_info=True)
            return ""

    def _warm_user_graph(self) -> None:
        if not self._client or not self._zep_user_id:
            return
        try:
            self._client.user.warm(self._zep_user_id)
        except Exception:
            logger.debug("Zep warm failed", exc_info=True)

    def _ensure_user_exists(self) -> None:
        if self._user_ready or not self._client:
            return
        with self._client_lock:
            if self._user_ready:
                return
            try:
                self._client.user.get(self._zep_user_id)
            except Exception as exc:
                if not self._is_not_found(exc):
                    raise
                try:
                    self._client.user.add(
                        user_id=self._zep_user_id,
                        metadata={
                            "source": "hermes",
                            "provider": "zep",
                        },
                    )
                except Exception:
                    try:
                        self._client.user.get(self._zep_user_id)
                    except Exception:
                        raise
            self._user_ready = True

    def _ensure_thread_exists(self, thread_id: str, *, is_notes: bool = False) -> None:
        if not self._client:
            return
        ready_flag = "_notes_thread_ready" if is_notes else "_session_thread_ready"
        if getattr(self, ready_flag):
            return
        with self._client_lock:
            if getattr(self, ready_flag):
                return
            self._ensure_user_exists()
            try:
                self._client.thread.get(thread_id, lastn=1)
            except Exception as exc:
                if not self._is_not_found(exc):
                    raise
                try:
                    self._client.thread.create(
                        thread_id=thread_id,
                        user_id=self._zep_user_id,
                    )
                except Exception:
                    try:
                        self._client.thread.get(thread_id, lastn=1)
                    except Exception:
                        raise
            setattr(self, ready_flag, True)

    def _is_not_found(self, exc: Exception) -> bool:
        if isinstance(exc, self._not_found_cls):
            return True
        return getattr(exc, "status_code", None) == 404

    def _close_client(self) -> None:
        client = self._client
        self._client = None
        if not client:
            return
        try:
            wrapper = getattr(client, "_client_wrapper", None)
            http_client = getattr(wrapper, "httpx_client", None)
            raw_client = getattr(http_client, "httpx_client", None)
            if raw_client:
                raw_client.close()
        except Exception:
            logger.debug("Failed to close Zep client", exc_info=True)


def register(ctx) -> None:
    """Register Zep as a bundled memory provider."""
    ctx.register_memory_provider(ZepMemoryProvider())
