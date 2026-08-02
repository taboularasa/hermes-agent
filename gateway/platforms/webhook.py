"""Generic webhook platform adapter.

Runs an aiohttp HTTP server that receives webhook POSTs from external
services (GitHub, GitLab, JIRA, Stripe, etc.), validates HMAC signatures,
transforms payloads into agent prompts, and routes responses back to the
source or to another configured platform.

Configuration lives in config.yaml under platforms.webhook.extra.routes.
Each route defines:
  - events: which event types to accept (header-based filtering)
  - secret: HMAC secret for signature validation (REQUIRED)
  - prompt: template string formatted with the webhook payload
  - skills: optional list of skills to load for the agent
  - deliver: where to send the response (linear_comment, github_comment,
    telegram, etc.)
  - deliver_extra: additional delivery config (repo, pr_number, chat_id)
  - deliver_only: if true, skip the agent — the rendered prompt IS the
    message that gets delivered.  Use for external push notifications
    (Supabase, monitoring alerts, inter-agent pings) where zero LLM cost
    and sub-second delivery matter more than agent reasoning.

Security:
  - HMAC secret is required per route (validated at startup)
  - Rate limiting per route (fixed-window, configurable)
  - Idempotency cache prevents duplicate agent runs on webhook retries
  - Body size limits checked before reading payload
  - Set secret to "INSECURE_NO_AUTH" to skip validation (testing only)
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Literal, Optional
import urllib.error
import urllib.request

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
)
from gateway.platforms.hermes_linear_ingress import (
    HERMES_LINEAR_REQUEST_CONTRACT,
    HERMES_LINEAR_SIGNED_ROUTE_NAME,
    HermesLinearDeliveryInbox,
    HermesLinearInboxDelivery,
    default_hermes_linear_inbox_path,
    hermes_linear_acceptance_receipt,
    hermes_linear_session_chat_id,
    verify_hermes_linear_request,
)
from gateway.session import build_session_key

logger = logging.getLogger(__name__)

# Sentinel returned by _resolve_request_profile when a /p/<profile>/ prefix
# names a profile this gateway does not serve (→ 404). Distinct from None
# (no prefix / multiplexing off → handle as the default profile).
_PROFILE_REJECTED = object()

_BUILTIN_DELIVER_PLATFORMS = {
    "telegram",
    "discord",
    "slack",
    "signal",
    "sms",
    "whatsapp",
    "matrix",
    "mattermost",
    "homeassistant",
    "email",
    "dingtalk",
    "feishu",
    "wecom",
    "wecom_callback",
    "weixin",
    "bluebubbles",
    "qqbot",
    "yuanbao",
}

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8644
_INSECURE_NO_AUTH = "INSECURE_NO_AUTH"
_DYNAMIC_ROUTES_FILENAME = "webhook_subscriptions.json"
_RATE_WINDOW_SECONDS = 60.0

# Hostnames/IP literals that only serve connections originating on the same
# machine. Anything else is treated as a public bind for safety-rail purposes.
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "::1",
    "ip6-localhost",
    "ip6-loopback",
})


def _is_loopback_host(host: str) -> bool:
    """True when `host` binds only to the local machine.

    Covers IPv4 loopback, the standard `localhost` alias, IPv6 loopback in
    both bracketed and bare form, and the common Debian-style aliases. Any
    falsy value (empty string, None) is conservatively treated as non-loopback
    because an unset host usually means the platform-default public bind.
    """
    if not host:
        return False
    return host.strip().lower() in _LOOPBACK_HOSTS


def check_webhook_requirements() -> bool:
    """Check if webhook adapter dependencies are available."""
    return AIOHTTP_AVAILABLE


class WebhookAdapter(BasePlatformAdapter):
    """Generic webhook receiver that triggers agent runs from HTTP POSTs."""

    SUPPORTS_MESSAGE_EDITING = False

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WEBHOOK)
        self._host: str = config.extra.get("host", DEFAULT_HOST)
        self._port: int = int(config.extra.get("port", DEFAULT_PORT))
        self._global_secret: str = config.extra.get("secret", "")
        self._static_routes: Dict[str, dict] = config.extra.get("routes", {})
        self._dynamic_routes: Dict[str, dict] = {}
        self._dynamic_routes_mtime: float = 0.0
        self._routes: Dict[str, dict] = dict(self._static_routes)
        self._runner = None

        # Delivery info keyed by session chat_id.
        #
        # Read by every send() invocation for the chat_id (status messages
        # AND the final response).  Cleaned up via TTL on each POST so the
        # dict stays bounded — see _prune_delivery_info().  Do NOT pop on
        # send(), or interim status messages (e.g. fallback notifications,
        # context-pressure warnings) will consume the entry before the
        # final response arrives, causing the response to silently fall
        # back to the "log" deliver type.
        self._delivery_info: Dict[str, dict] = {}
        self._delivery_info_created: Dict[str, float] = {}
        self._delivery_info_order: Deque[tuple[float, str]] = deque()

        # Reference to gateway runner for cross-platform delivery (set externally)
        self.gateway_runner = None

        # Idempotency: TTL cache of recently processed delivery IDs.
        # Prevents duplicate agent runs when webhook providers retry.
        self._seen_deliveries: Dict[str, float] = {}
        self._idempotency_ttl: int = 3600  # 1 hour
        self._seen_deliveries_next_prune_at: float = 0.0
        self._hermes_linear_delivery_inboxes: Dict[str, HermesLinearDeliveryInbox] = {}
        self._hermes_linear_worker_task: Optional[asyncio.Task] = None
        self._hermes_linear_activity_worker_task: Optional[asyncio.Task] = None
        self._hermes_linear_delivery_tasks: set[asyncio.Task] = set()
        self._hermes_linear_worker_wake = asyncio.Event()
        self._hermes_linear_activity_worker_wake = asyncio.Event()
        self._hermes_linear_worker_stop = asyncio.Event()
        self._hermes_linear_lease_seconds = 60
        # Keep gateway pressure bounded without adding a new user-facing config
        # surface for an opt-in route that is not deployed yet.
        self._hermes_linear_max_concurrent_deliveries = 4
        self._hermes_linear_worker_instance = (
            f"hermes-linear-{os.getpid()}-{uuid.uuid4().hex}"
        )
        self._hermes_linear_failpoint = None  # test-only callable; never configured

        # Rate limiting: per-route timestamps in a fixed window.
        self._rate_counts: Dict[str, Deque[float]] = {}
        self._rate_limit: int = int(config.extra.get("rate_limit", 30))  # per minute

        # Body size limit (auth-before-body pattern)
        self._max_body_bytes: int = int(
            config.extra.get("max_body_bytes", 1_048_576)
        )  # 1MB

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        # Load agent-created subscriptions before validating
        self._reload_dynamic_routes()

        # Validate routes at startup — secret is required per route
        for name, route in self._routes.items():
            secret = route.get("secret", self._global_secret)
            if not secret:
                raise ValueError(
                    f"[webhook] Route '{name}' has no HMAC secret. "
                    f"Set 'secret' on the route or globally. "
                    f"For testing without auth, set secret to '{_INSECURE_NO_AUTH}'."
                )

            request_contract = route.get("request_contract")
            if request_contract not in {None, HERMES_LINEAR_REQUEST_CONTRACT}:
                raise ValueError(
                    f"[webhook] Route '{name}' has unsupported request_contract "
                    f"'{request_contract}'."
                )
            if (
                request_contract == HERMES_LINEAR_REQUEST_CONTRACT
                and name != HERMES_LINEAR_SIGNED_ROUTE_NAME
            ):
                raise ValueError(
                    f"[webhook] Hermes Linear request contract is restricted to "
                    f"the static '{HERMES_LINEAR_SIGNED_ROUTE_NAME}' route, not "
                    f"'{name}'."
                )
            if (
                request_contract == HERMES_LINEAR_REQUEST_CONTRACT
                and secret == _INSECURE_NO_AUTH
            ):
                raise ValueError(
                    f"[webhook] Route '{name}' requires authenticated Hermes Linear "
                    "requests and cannot use INSECURE_NO_AUTH."
                )

            if request_contract == HERMES_LINEAR_REQUEST_CONTRACT:
                configured_path = route.get("delivery_inbox_path") or route.get(
                    "delivery_ledger_path"
                )
                inbox_path = (
                    Path(configured_path).expanduser()
                    if configured_path
                    else default_hermes_linear_inbox_path()
                )
                self._hermes_linear_delivery_inboxes[name] = HermesLinearDeliveryInbox(
                    inbox_path
                )

            # Safety rail: refuse to start if INSECURE_NO_AUTH is combined with a
            # non-loopback bind. The escape hatch is for local testing only;
            # serving an unauthenticated route on a public interface is a
            # deployment-grade footgun we'd rather crash early than ship.
            if secret == _INSECURE_NO_AUTH and not _is_loopback_host(self._host):
                raise ValueError(
                    f"[webhook] Route '{name}' uses INSECURE_NO_AUTH secret "
                    f"but is bound to non-loopback host '{self._host}'. "
                    f"INSECURE_NO_AUTH is for local testing only. "
                    f"Refusing to start to prevent accidental exposure."
                )
            # deliver_only routes bypass the agent — the POST body becomes a
            # direct push notification via the configured delivery target.
            # Validate up-front so misconfiguration surfaces at startup rather
            # than on the first webhook POST.
            if route.get("deliver_only"):
                deliver = route.get("deliver", "log")
                if not deliver or deliver == "log":
                    raise ValueError(
                        f"[webhook] Route '{name}' has deliver_only=true but "
                        f"deliver is '{deliver}'. Direct delivery requires a "
                        f"real target (telegram, discord, slack, github_comment, etc.)."
                    )

        app = web.Application(client_max_size=self._max_body_bytes)
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/webhooks/{route_name}", self._handle_webhook)
        # Multi-profile multiplexing: a /p/<profile>/webhooks/<route> prefix
        # routes the inbound event to that profile. Same handler; the profile is
        # captured from the path and stamped onto the SessionSource so the agent
        # turn resolves that profile's config/skills/credentials. Only honored
        # when gateway.multiplex_profiles is on (the handler validates).
        app.router.add_post("/p/{profile}/webhooks/{route_name}", self._handle_webhook)

        # Port conflict detection — fail fast if port is already in use
        import socket as _socket

        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                _s.settimeout(1)
                _s.connect(("127.0.0.1", self._port))
            logger.error(
                "[webhook] Port %d already in use. Set a different port in config.yaml: platforms.webhook.port",
                self._port,
            )
            return False
        except (ConnectionRefusedError, OSError):
            pass  # port is free

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._mark_connected()
        if self._hermes_linear_delivery_inboxes:
            self._hermes_linear_worker_stop.clear()
            self._hermes_linear_worker_task = asyncio.create_task(
                self._run_hermes_linear_inbox_worker()
            )
            self._hermes_linear_activity_worker_task = asyncio.create_task(
                self._run_hermes_linear_activity_worker()
            )
            self._background_tasks.add(self._hermes_linear_worker_task)
            self._background_tasks.add(self._hermes_linear_activity_worker_task)
            self._hermes_linear_worker_task.add_done_callback(
                self._background_tasks.discard
            )
            self._hermes_linear_activity_worker_task.add_done_callback(
                self._background_tasks.discard
            )

        route_names = ", ".join(self._routes.keys()) or "(none configured)"
        logger.info(
            "[webhook] Listening on %s:%d — routes: %s",
            self._host,
            self._port,
            route_names,
        )
        return True

    async def disconnect(self) -> None:
        self._hermes_linear_worker_stop.set()
        self._hermes_linear_worker_wake.set()
        self._hermes_linear_activity_worker_wake.set()
        workers = [
            task
            for task in (
                self._hermes_linear_worker_task,
                self._hermes_linear_activity_worker_task,
            )
            if task is not None
        ]
        self._hermes_linear_worker_task = None
        self._hermes_linear_activity_worker_task = None
        delivery_tasks = list(self._hermes_linear_delivery_tasks)
        for task in [*workers, *delivery_tasks]:
            if not task.done():
                task.cancel()
        for task in [*workers, *delivery_tasks]:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "[webhook] Hermes Linear task exited during shutdown",
                    exc_info=True,
                )
        self._hermes_linear_delivery_tasks.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._mark_disconnected()
        logger.info("[webhook] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Deliver the agent's response to the configured destination.

        chat_id is ``webhook:{route}:{delivery_id}``.  The delivery info
        stored during webhook receipt is read with ``.get()`` (not popped)
        so that interim status messages emitted before the final response
        — fallback-model notifications, context-pressure warnings, etc. —
        do not consume the entry and silently downgrade the final response
        to the ``log`` deliver type.  TTL cleanup happens on POST.
        """
        delivery = self._delivery_info.get(chat_id, {})
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            logger.info("[webhook] Response for %s: %s", chat_id, content[:200])
            return SendResult(success=True)

        if deliver_type == "linear_comment":
            return await self._deliver_linear_comment(content, delivery)

        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)

        # Cross-platform delivery — any platform with a gateway adapter.
        # Check both built-in names and plugin-registered platforms.
        _is_known_platform = deliver_type in _BUILTIN_DELIVER_PLATFORMS
        if not _is_known_platform:
            try:
                from gateway.platform_registry import platform_registry

                _is_known_platform = platform_registry.is_registered(deliver_type)
            except Exception:
                pass
        if self.gateway_runner and _is_known_platform:
            return await self._deliver_cross_platform(deliver_type, content, delivery)

        logger.warning("[webhook] Unknown deliver type: %s", deliver_type)
        return SendResult(success=False, error=f"Unknown deliver type: {deliver_type}")

    def _prune_delivery_info(self, now: float) -> None:
        """Drop delivery_info entries older than the idempotency TTL.

        Mirrors the cleanup pattern used for ``_seen_deliveries``.  Called
        on each POST so the dict size is bounded by ``rate_limit * TTL``
        even if many webhooks fire and never receive a final response.
        """
        if len(self._delivery_info_order) < len(self._delivery_info_created):
            self._delivery_info_order = deque(
                (created_at, key)
                for key, created_at in sorted(
                    self._delivery_info_created.items(), key=lambda item: item[1]
                )
            )
        cutoff = now - self._idempotency_ttl
        while self._delivery_info_order and self._delivery_info_order[0][0] < cutoff:
            created_at, key = self._delivery_info_order.popleft()
            if self._delivery_info_created.get(key) != created_at:
                continue
            self._delivery_info.pop(key, None)
            self._delivery_info_created.pop(key, None)

    def _prune_seen_deliveries(self, now: float) -> None:
        """Occasionally prune expired delivery IDs without scanning every POST."""
        if now < self._seen_deliveries_next_prune_at:
            return
        cutoff = now - self._idempotency_ttl
        stale = [k for k, t in self._seen_deliveries.items() if t < cutoff]
        for k in stale:
            self._seen_deliveries.pop(k, None)
        self._seen_deliveries_next_prune_at = now + min(
            60.0, max(1.0, self._idempotency_ttl / 10)
        )

    def _record_rate_limit_hit(self, route_name: str, now: float) -> bool:
        """Return True if route is still within limit after recording this hit."""
        window = self._rate_counts.get(route_name)
        if not isinstance(window, deque):
            new_window: Deque[float] = deque(window or ())
            self._rate_counts[route_name] = new_window
            window = new_window
        cutoff = now - _RATE_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._rate_limit:
            return False
        window.append(now)
        return True

    def _record_delivery_id(self, delivery_id: str, now: float) -> bool:
        """Return True when this delivery should be processed."""
        seen_at = self._seen_deliveries.get(delivery_id)
        if seen_at is not None and now - seen_at < self._idempotency_ttl:
            return False
        if seen_at is not None:
            self._seen_deliveries.pop(delivery_id, None)
        self._seen_deliveries[delivery_id] = now
        if len(self._seen_deliveries) > max(self._rate_limit * 2, 128):
            self._prune_seen_deliveries(now)
        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "webhook"}

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health — simple health check."""
        inbox_states: dict[str, dict[str, int]] = {}
        activity_states: dict[str, dict[str, int]] = {}
        for route_name, inbox in self._hermes_linear_delivery_inboxes.items():
            try:
                inbox_states[route_name] = await asyncio.to_thread(inbox.state_counts)
                route_activities = await asyncio.to_thread(inbox.activity_state_counts)
                if route_activities:
                    activity_states[route_name] = route_activities
            except Exception:
                inbox_states[route_name] = {"diagnostics_unavailable": 1}
        payload: dict[str, Any] = {"status": "ok", "platform": "webhook"}
        if inbox_states:
            payload["hermes_linear_inbox"] = inbox_states
        if activity_states:
            payload["hermes_linear_activity"] = activity_states
        return web.json_response(payload)

    def _reload_dynamic_routes(self) -> None:
        """Reload agent-created subscriptions from disk if the file changed."""
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
        subs_path = hermes_home / _DYNAMIC_ROUTES_FILENAME
        if not subs_path.exists():
            if self._dynamic_routes:
                self._dynamic_routes = {}
                self._routes = dict(self._static_routes)
                logger.debug(
                    "[webhook] Dynamic subscriptions file removed, cleared dynamic routes"
                )
            return
        try:
            mtime = subs_path.stat().st_mtime
            if mtime <= self._dynamic_routes_mtime:
                return  # No change
            data = json.loads(subs_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            # Merge: static routes take precedence over dynamic ones.
            # Reject any dynamic route whose effective secret is empty —
            # an empty secret would cause _handle_webhook to skip HMAC
            # validation entirely, letting unauthenticated callers in.
            new_dynamic: Dict[str, dict] = {}
            for k, v in data.items():
                if k in self._static_routes:
                    continue
                effective_secret = v.get("secret", self._global_secret)
                if not effective_secret:
                    logger.warning(
                        "[webhook] Dynamic route '%s' skipped: 'secret' is "
                        "missing or empty. Set a valid HMAC secret, or use "
                        "'%s' to explicitly disable auth (testing only).",
                        k,
                        _INSECURE_NO_AUTH,
                    )
                    continue
                if effective_secret == _INSECURE_NO_AUTH and not _is_loopback_host(
                    self._host
                ):
                    logger.warning(
                        "[webhook] Dynamic route '%s' skipped: INSECURE_NO_AUTH "
                        "is only allowed on loopback hosts. Current host: '%s'.",
                        k,
                        self._host,
                    )
                    continue
                new_dynamic[k] = v
            self._dynamic_routes = new_dynamic
            self._routes = {**self._dynamic_routes, **self._static_routes}
            self._dynamic_routes_mtime = mtime
            logger.info(
                "[webhook] Reloaded %d dynamic route(s): %s",
                len(self._dynamic_routes),
                ", ".join(self._dynamic_routes.keys()) or "(none)",
            )
        except Exception as e:
            logger.error("[webhook] Failed to reload dynamic routes: %s", e)

    def _resolve_request_profile(self, request: "web.Request"):
        """Resolve + validate the /p/<profile>/ URL prefix on a webhook request.

        Returns:
          - ``None`` when no profile prefix is present, or multiplexing is off
            (the prefix is ignored, request handled as the default profile).
          - the profile name (str) when present, multiplexing is on, and the
            profile is one this gateway serves.
          - ``_PROFILE_REJECTED`` when a prefix is present but the profile is
            unknown/unconfigured (handler returns 404).
        """
        profile = (request.match_info.get("profile") or "").strip()
        if not profile:
            return None
        runner = self.gateway_runner
        cfg = getattr(runner, "config", None)
        if not getattr(cfg, "multiplex_profiles", False):
            # Prefix supplied but multiplexing is off — ignore it, behave as
            # the single-profile gateway (don't 404 a would-be valid route).
            return None
        try:
            from hermes_cli.profiles import profiles_to_serve

            served = {name for name, _ in profiles_to_serve(multiplex=True)}
        except Exception:
            return _PROFILE_REJECTED
        if profile not in served:
            return _PROFILE_REJECTED
        return profile

    def _hermes_linear_inbox_for_route(
        self, route_name: str, route_config: dict
    ) -> HermesLinearDeliveryInbox:
        inbox = self._hermes_linear_delivery_inboxes.get(route_name)
        if inbox is not None:
            return inbox
        configured_path = route_config.get("delivery_inbox_path") or route_config.get(
            "delivery_ledger_path"
        )
        inbox_path = (
            Path(configured_path).expanduser()
            if configured_path
            else default_hermes_linear_inbox_path()
        )
        inbox = HermesLinearDeliveryInbox(inbox_path)
        self._hermes_linear_delivery_inboxes[route_name] = inbox
        return inbox

    def _run_hermes_linear_failpoint(self, name: str, delivery_key: str) -> None:
        hook = self._hermes_linear_failpoint
        if callable(hook):
            hook(name, delivery_key)

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        """POST /webhooks/{route_name} — receive and process a webhook event."""
        # Hot-reload dynamic subscriptions on each request (mtime-gated, cheap)
        self._reload_dynamic_routes()

        route_name = request.match_info.get("route_name", "")
        route_config = self._routes.get(route_name)

        # Multi-profile: resolve + validate the /p/<profile>/ prefix if present.
        profile = self._resolve_request_profile(request)
        if profile is _PROFILE_REJECTED:
            return web.json_response(
                {"error": "Unknown or unconfigured profile"}, status=404
            )

        if not route_config:
            return web.json_response(
                {"error": f"Unknown route: {route_name}"}, status=404
            )

        # Disabled routes are kept in the subscriptions file (so the dashboard
        # can re-enable them) but reject incoming events.  Default-enabled:
        # only an explicit ``enabled: false`` turns a route off, matching the
        # mcp_servers ``enabled`` semantics.
        if route_config.get("enabled", True) is False:
            return web.json_response(
                {"error": f"Route disabled: {route_name}"}, status=403
            )

        # ── Auth-before-body ─────────────────────────────────────
        # Check Content-Length before reading the full payload.
        content_length = request.content_length or 0
        if content_length > self._max_body_bytes:
            return web.json_response({"error": "Payload too large"}, status=413)

        # Read body (must be done before any validation)
        try:
            raw_body = await request.read()
        except Exception as e:
            logger.error("[webhook] Failed to read body: %s", e)
            return web.json_response({"error": "Bad request"}, status=400)

        # Validate HMAC signature FIRST (skip only for the explicit local-test
        # INSECURE_NO_AUTH mode). Missing/empty secrets must fail closed here,
        # not only during connect(), so direct handler reuse cannot turn a
        # network webhook route into an unauthenticated agent-dispatch surface.
        secret = route_config.get("secret", self._global_secret)
        if not secret:
            logger.error(
                "[webhook] Route %s has no HMAC secret; refusing request",
                route_name,
            )
            return web.json_response(
                {"error": "Webhook route is missing an HMAC secret"},
                status=403,
            )
        request_contract = route_config.get("request_contract")
        hermes_linear_verification = None
        if request_contract == HERMES_LINEAR_REQUEST_CONTRACT:
            if secret == _INSECURE_NO_AUTH:
                logger.error(
                    "[webhook] Hermes Linear route %s is misconfigured without auth",
                    route_name,
                )
                return web.json_response(
                    {"error": "Webhook route is misconfigured"}, status=403
                )
            hermes_linear_verification = verify_hermes_linear_request(
                raw_body=raw_body,
                headers=request.headers,
                secret=secret,
                now=time.time(),
            )
            if not hermes_linear_verification.ok:
                logger.warning(
                    "[webhook] Hermes Linear request rejected route=%s reason=%s",
                    route_name,
                    hermes_linear_verification.reason,
                )
                return web.json_response(
                    {
                        "error": "Hermes Linear request rejected",
                        "reason": hermes_linear_verification.reason,
                    },
                    status=hermes_linear_verification.status,
                )
        elif secret != _INSECURE_NO_AUTH:
            if not self._validate_signature(request, raw_body, secret):
                logger.warning("[webhook] Invalid signature for route %s", route_name)
                return web.json_response({"error": "Invalid signature"}, status=401)

        # ── Rate limiting (after auth) ───────────────────────────
        now = time.time()
        if not self._record_rate_limit_hit(route_name, now):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        # Parse payload. The Hermes contract verifier has already established
        # that its payload is an object; legacy routes retain form fallback.
        payload: dict[str, Any]
        if hermes_linear_verification is not None:
            if hermes_linear_verification.payload is None:
                return web.json_response({"error": "Cannot parse body"}, status=400)
            payload = hermes_linear_verification.payload
        else:
            try:
                parsed_payload = json.loads(raw_body)
                if not isinstance(parsed_payload, dict):
                    return web.json_response({"error": "Cannot parse body"}, status=400)
                payload = parsed_payload
            except json.JSONDecodeError:
                # Try form-encoded as fallback
                try:
                    import urllib.parse

                    payload = dict(urllib.parse.parse_qsl(raw_body.decode("utf-8")))
                except Exception:
                    return web.json_response({"error": "Cannot parse body"}, status=400)

        # Check event type filter
        event_type = (
            "linear_agent_session"
            if hermes_linear_verification is not None
            else (
                request.headers.get("X-GitHub-Event", "")
                or request.headers.get("X-GitLab-Event", "")
                or payload.get("event_type", "")
                or payload.get("type", "")
                or "unknown"
            )
        )
        allowed_events = route_config.get("events", [])
        if allowed_events and event_type not in allowed_events:
            logger.debug(
                "[webhook] Ignoring event %s for route %s (allowed: %s)",
                event_type,
                route_name,
                allowed_events,
            )
            return web.json_response({"status": "ignored", "event": event_type})

        # Format prompt from template
        prompt_template = route_config.get("prompt", "")
        prompt = self._render_prompt(prompt_template, payload, event_type, route_name)

        # Inject skill content if configured.
        # We call build_skill_invocation_message() directly rather than
        # using /skill-name slash commands — the gateway's command parser
        # would intercept those and break the flow.
        skills = route_config.get("skills", [])
        if skills:
            try:
                from agent.skill_commands import (
                    build_skill_invocation_message,
                    get_skill_commands,
                )

                skill_cmds = get_skill_commands()
                for skill_name in skills:
                    cmd_key = f"/{skill_name}"
                    if cmd_key in skill_cmds:
                        skill_content = build_skill_invocation_message(
                            cmd_key, user_instruction=prompt
                        )
                        if skill_content:
                            prompt = skill_content
                            break  # Load the first matching skill
                    else:
                        logger.warning("[webhook] Skill '%s' not found", skill_name)
            except Exception as e:
                logger.warning("[webhook] Skill loading failed: %s", e)

        # Build a unique delivery ID
        if hermes_linear_verification is not None:
            delivery_id = hermes_linear_verification.delivery_key or ""
        else:
            delivery_id = request.headers.get(
                "X-GitHub-Delivery",
                request.headers.get(
                    "svix-id",
                    request.headers.get("X-Request-ID", str(int(time.time() * 1000))),
                ),
            )

        if hermes_linear_verification is not None:
            if not delivery_id or not hermes_linear_verification.body_sha256:
                logger.error(
                    "[webhook] Verified Hermes Linear request lacked claim fields"
                )
                return web.json_response(
                    {"error": "Invalid verified delivery"}, status=400
                )
            try:
                inbox = self._hermes_linear_inbox_for_route(route_name, route_config)
                claim = inbox.accept(
                    delivery_key=delivery_id,
                    body_sha256=hermes_linear_verification.body_sha256,
                    raw_body=raw_body,
                    received_at=int(time.time()),
                    route_name=route_name,
                    profile=profile if isinstance(profile, str) else None,
                )
            except Exception as exc:
                logger.error(
                    "[webhook] Hermes Linear inbox commit failed route=%s error_type=%s",
                    route_name,
                    type(exc).__name__,
                )
                return web.json_response(
                    {"error": "Delivery acceptance failed"}, status=503
                )
            if claim == "duplicate":
                logger.info(
                    "[webhook] Skipping duplicate Hermes Linear delivery %s",
                    delivery_id,
                )
                return web.json_response(
                    hermes_linear_acceptance_receipt(
                        status="duplicate",
                        delivery_key=delivery_id,
                        body_sha256=hermes_linear_verification.body_sha256,
                    ),
                    status=200,
                )
            if claim == "conflict":
                logger.warning(
                    "[webhook] Hermes Linear delivery key conflict %s",
                    delivery_id,
                )
                return web.json_response(
                    {"error": "Delivery key conflict", "delivery_id": delivery_id},
                    status=409,
                )

            # The exact verified body is now durable. Wake the long-lived
            # worker, then acknowledge without depending on an unawaited
            # request-scoped task. Direct handler tests may not call connect();
            # in that case the committed row remains available for the worker
            # at the next normal adapter start.
            self._run_hermes_linear_failpoint("after_inbox_commit", delivery_id)
            self._hermes_linear_worker_wake.set()
            return web.json_response(
                hermes_linear_acceptance_receipt(
                    status="accepted",
                    delivery_key=delivery_id,
                    body_sha256=hermes_linear_verification.body_sha256,
                ),
                status=202,
            )

        # ── Idempotency ─────────────────────────────────────────
        # Skip duplicate deliveries (webhook retries).
        now = time.time()
        if not self._record_delivery_id(delivery_id, now):
            logger.info("[webhook] Skipping duplicate delivery %s", delivery_id)
            return web.json_response(
                {"status": "duplicate", "delivery_id": delivery_id},
                status=200,
            )

        # ── Direct delivery mode (deliver_only) ─────────────────
        # Skip the agent entirely — the rendered prompt IS the message we
        # deliver.  Use case: external services (Supabase, monitoring,
        # cron jobs, other agents) that need to push a plain notification
        # to a user's chat with zero LLM cost.  Reuses the same HMAC auth,
        # rate limiting, idempotency, and template rendering as agent mode.
        if route_config.get("deliver_only"):
            delivery = {
                "deliver": route_config.get("deliver", "log"),
                "deliver_extra": self._render_delivery_extra(
                    route_config.get("deliver_extra", {}), payload
                ),
                "payload": payload,
            }
            logger.info(
                "[webhook] direct-deliver event=%s route=%s target=%s msg_len=%d delivery=%s",
                event_type,
                route_name,
                delivery["deliver"],
                len(prompt),
                delivery_id,
            )
            try:
                result = await self._direct_deliver(prompt, delivery)
            except Exception:
                logger.exception(
                    "[webhook] direct-deliver failed route=%s delivery=%s",
                    route_name,
                    delivery_id,
                )
                return web.json_response(
                    {
                        "status": "error",
                        "error": "Delivery failed",
                        "delivery_id": delivery_id,
                    },
                    status=502,
                )

            if result.success:
                return web.json_response(
                    {
                        "status": "delivered",
                        "route": route_name,
                        "target": delivery["deliver"],
                        "delivery_id": delivery_id,
                    },
                    status=200,
                )
            # Delivery attempted but target rejected it — surface as 502
            # with a generic error (don't leak adapter-level detail).
            logger.warning(
                "[webhook] direct-deliver target rejected route=%s target=%s error=%s",
                route_name,
                delivery["deliver"],
                result.error,
            )
            return web.json_response(
                {
                    "status": "error",
                    "error": "Delivery failed",
                    "delivery_id": delivery_id,
                },
                status=502,
            )

        # Use delivery_id in session key so concurrent webhooks on the
        # same route get independent agent runs (not queued/interrupted).
        session_chat_id = f"webhook:{route_name}:{delivery_id}"

        # Store delivery info for send().  Read by every send() invocation
        # for this chat_id (interim status messages and the final response),
        # so we do NOT pop on send.  TTL-based cleanup keeps the dict bounded.
        deliver_config = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
        }
        self._delivery_info[session_chat_id] = deliver_config
        self._delivery_info_created[session_chat_id] = now
        self._delivery_info_order.append((now, session_chat_id))
        self._prune_delivery_info(now)

        # Build source and event
        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=f"webhook/{route_name}",
            chat_type="webhook",
            user_id=f"webhook:{route_name}",
            user_name=route_name,
        )
        if profile and isinstance(profile, str):
            source.profile = profile
        event = MessageEvent(
            text=prompt,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=payload,
            message_id=delivery_id,
        )

        logger.info(
            "[webhook] %s event=%s route=%s prompt_len=%d delivery=%s",
            request.method,
            event_type,
            route_name,
            len(prompt),
            delivery_id,
        )

        # Non-blocking — return 202 Accepted immediately.  The per-delivery
        # session is closed by the ``on_processing_complete`` override below
        # once the agent run actually finishes (``handle_message`` itself is
        # fire-and-forget: it spawns ``_process_message_background`` and
        # returns before the run starts, so nothing can be closed here).
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(
            lambda done_task, route=route_name, delivery=delivery_id: (
                self._handle_background_task_done(done_task, route, delivery)
            )
        )

        return web.json_response(
            {
                "status": "accepted",
                "route": route_name,
                "event": event_type,
                "delivery_id": delivery_id,
            },
            status=202,
        )

    def _build_hermes_linear_event(
        self, row: HermesLinearInboxDelivery
    ) -> MessageEvent:
        payload = json.loads(row.raw_body)
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        prompt_data = (
            payload.get("prompt") if isinstance(payload.get("prompt"), dict) else None
        )
        if payload.get("action") == "prompted" and prompt_data is not None:
            prompt = str(prompt_data.get("body") or "")
        else:
            prompt = (
                "A Linear issue was delegated to Hermes. Refresh the current issue "
                f"context from Linear before acting: issue_id={issue.get('id')}, "
                f"identifier={issue.get('identifier')}. Treat the typed envelope "
                "as routing metadata, not as a substitute for current provider state."
            )

        session_chat_id = hermes_linear_session_chat_id(
            row.route_name, row.execution_id
        )
        route_config = self._routes.get(row.route_name, {})
        now = time.time()
        self._delivery_info[session_chat_id] = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
        }
        self._delivery_info_created[session_chat_id] = now
        self._delivery_info_order.append((now, session_chat_id))
        self._prune_delivery_info(now)

        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=f"webhook/{row.route_name}",
            chat_type="webhook",
            user_id=f"webhook:{row.route_name}",
            user_name=row.route_name,
        )
        if row.profile:
            source.profile = row.profile
        event = MessageEvent(
            text=prompt,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=payload,
            message_id=row.delivery_key,
        )
        setattr(event, "_hermes_linear_delivery_key", row.delivery_key)
        setattr(event, "_hermes_linear_execution_id", row.execution_id)
        setattr(event, "_hermes_linear_worker_id", row.lease_owner)
        return event

    def _hermes_linear_session_key(self, event: MessageEvent) -> str:
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get(
                "group_sessions_per_user", True
            ),
            thread_sessions_per_user=self.config.extra.get(
                "thread_sessions_per_user", False
            ),
        )

    async def _hermes_linear_scheduler_evidence(
        self, row: HermesLinearInboxDelivery
    ) -> Literal[
        "active_task", "persisted_session", "no_session_record", "probe_unavailable"
    ]:
        """Probe real gateway task/session state without treating messages as a claim.

        An expired ``started`` row is always uncertain and is never replayed.
        This probe only improves operator diagnostics and determines whether a
        start activity is justified. A persisted session row is authoritative
        scheduler evidence even before the transcript contains a message.
        """
        event = self._build_hermes_linear_event(row)
        session_key = self._hermes_linear_session_key(event)
        task = self._session_tasks.get(session_key)
        if task is not None and not task.done():
            return "active_task"

        runner = self.gateway_runner
        store = getattr(runner, "session_store", None) if runner is not None else None
        session_db = (
            getattr(runner, "_session_db", None) if runner is not None else None
        )
        key_fn = getattr(runner, "_session_key_for_source", None)
        peek = getattr(store, "peek_session_id", None)
        get_session = getattr(session_db, "get_session", None)
        if not callable(key_fn) or not callable(peek) or not callable(get_session):
            return "probe_unavailable"
        try:
            persisted_key = key_fn(event.source)
            session_id = peek(persisted_key)
            if not session_id:
                return "no_session_record"
            result = get_session(session_id)
            session = await result if asyncio.iscoroutine(result) else result
            return "persisted_session" if session is not None else "no_session_record"
        except Exception:
            logger.warning(
                "[webhook] Hermes Linear scheduler probe failed delivery=%s",
                row.delivery_key,
            )
            return "probe_unavailable"

    async def _heartbeat_hermes_linear_delivery(
        self, inbox: HermesLinearDeliveryInbox, row: HermesLinearInboxDelivery
    ) -> None:
        worker_id = row.lease_owner
        if not worker_id:
            return
        interval = max(1, self._hermes_linear_lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            ok = await asyncio.to_thread(
                inbox.heartbeat,
                row.delivery_key,
                worker_id,
                int(time.time()),
                self._hermes_linear_lease_seconds,
            )
            if not ok:
                return

    async def _dispatch_hermes_linear_inbox_row(
        self, inbox: HermesLinearDeliveryInbox, row: HermesLinearInboxDelivery
    ) -> None:
        worker_id = row.lease_owner
        if not worker_id:
            return
        now = int(time.time())
        if row.state == "started":
            evidence = await self._hermes_linear_scheduler_evidence(row)
            event = self._build_hermes_linear_event(row)
            scheduler_accepted = evidence in {"active_task", "persisted_session"}
            stalled = await asyncio.to_thread(
                inbox.mark_stalled_started,
                delivery_key=row.delivery_key,
                worker_id=worker_id,
                now=now,
                scheduler_evidence=evidence,
                activity_body=(
                    self._hermes_linear_activity_body(event, "action")
                    if scheduler_accepted
                    else None
                ),
            )
            if not stalled:
                # The live task may have completed between the probe and the
                # failed-closed transition. Preserve lifecycle parity for that
                # accepted fast run without changing its terminal state.
                activity_body = (
                    self._hermes_linear_activity_body(event, "action")
                    if scheduler_accepted
                    else None
                )
                if activity_body is not None:
                    await asyncio.to_thread(
                        inbox.enqueue_start_activity,
                        delivery_key=row.delivery_key,
                        worker_id=worker_id,
                        now=now,
                        activity_body=activity_body,
                    )
                    self._hermes_linear_activity_worker_wake.set()
                return
            if scheduler_accepted:
                self._hermes_linear_activity_worker_wake.set()
            logger.warning(
                "[webhook] Hermes Linear uncertain started delivery failed closed "
                "delivery=%s reason=stalled_started_%s",
                row.delivery_key,
                evidence,
            )
            return

        event = self._build_hermes_linear_event(row)
        scheduled = await asyncio.to_thread(
            inbox.mark_scheduled, row.delivery_key, worker_id, now
        )
        if not scheduled:
            return
        self._run_hermes_linear_failpoint("after_worker_lease", row.delivery_key)
        self._run_hermes_linear_failpoint("before_start_transition", row.delivery_key)
        started = await asyncio.to_thread(
            inbox.mark_started,
            delivery_key=row.delivery_key,
            worker_id=worker_id,
            now=int(time.time()),
        )
        if not started:
            return
        self._run_hermes_linear_failpoint("after_start_transition", row.delivery_key)
        self._run_hermes_linear_failpoint("before_agent_schedule", row.delivery_key)

        heartbeat = asyncio.create_task(
            self._heartbeat_hermes_linear_delivery(inbox, row)
        )
        try:
            await self.handle_message(event)
            session_key = self._hermes_linear_session_key(event)
            task = self._session_tasks.get(session_key)
            if task is None:
                await asyncio.to_thread(
                    inbox.finish,
                    delivery_key=row.delivery_key,
                    outcome="failed",
                    now=int(time.time()),
                    reason_code="scheduling_rejected",
                    error_activity_body=self._hermes_linear_activity_body(
                        event, "error"
                    ),
                )
                self._hermes_linear_activity_worker_wake.set()
                return
            self._run_hermes_linear_failpoint("after_agent_schedule", row.delivery_key)
            activity_body = self._hermes_linear_activity_body(event, "action")
            if activity_body is not None:
                await asyncio.to_thread(
                    inbox.enqueue_start_activity,
                    delivery_key=row.delivery_key,
                    worker_id=worker_id,
                    now=int(time.time()),
                    activity_body=activity_body,
                )
                self._hermes_linear_activity_worker_wake.set()
            await asyncio.shield(task)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _run_hermes_linear_delivery_task(
        self, inbox: HermesLinearDeliveryInbox, row: HermesLinearInboxDelivery
    ) -> None:
        try:
            await self._dispatch_hermes_linear_inbox_row(inbox, row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[webhook] Hermes Linear delivery task failed delivery=%s error_type=%s",
                row.delivery_key,
                type(exc).__name__,
            )

    async def _run_hermes_linear_inbox_worker(self) -> None:
        worker_id = f"{self._hermes_linear_worker_instance}-delivery"
        while not self._hermes_linear_worker_stop.is_set():
            self._hermes_linear_delivery_tasks = {
                task for task in self._hermes_linear_delivery_tasks if not task.done()
            }
            available = (
                self._hermes_linear_max_concurrent_deliveries
                - len(self._hermes_linear_delivery_tasks)
            )
            leased_any = False
            for inbox in list(self._hermes_linear_delivery_inboxes.values()):
                if available <= 0:
                    break
                try:
                    row = await asyncio.to_thread(
                        inbox.lease_next,
                        worker_id=worker_id,
                        now=int(time.time()),
                        lease_seconds=self._hermes_linear_lease_seconds,
                    )
                    if row is None:
                        continue
                    leased_any = True
                    task = asyncio.create_task(
                        self._run_hermes_linear_delivery_task(inbox, row)
                    )
                    self._hermes_linear_delivery_tasks.add(task)
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                    task.add_done_callback(self._hermes_linear_delivery_tasks.discard)
                    available -= 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "[webhook] Hermes Linear inbox worker error error_type=%s",
                        type(exc).__name__,
                    )
            if leased_any:
                continue
            self._hermes_linear_worker_wake.clear()
            try:
                await asyncio.wait_for(
                    self._hermes_linear_worker_wake.wait(), timeout=0.5
                )
            except asyncio.TimeoutError:
                pass

    async def _run_hermes_linear_activity_worker(self) -> None:
        worker_id = f"{self._hermes_linear_worker_instance}-activity"
        while not self._hermes_linear_worker_stop.is_set():
            processed = False
            for inbox in list(self._hermes_linear_delivery_inboxes.values()):
                try:
                    processed = (
                        await self._dispatch_hermes_linear_activity(inbox, worker_id)
                    ) or processed
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "[webhook] Hermes Linear activity worker error error_type=%s",
                        type(exc).__name__,
                    )
            if processed:
                continue
            self._hermes_linear_activity_worker_wake.clear()
            try:
                await asyncio.wait_for(
                    self._hermes_linear_activity_worker_wake.wait(), timeout=0.5
                )
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _post_hermes_linear_activity(
        url: str, secret: str, request_body: bytes
    ) -> tuple[Literal["delivered", "retry", "terminal"], str]:
        request = urllib.request.Request(
            url,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            return "retry", "network_or_timeout"
        if 200 <= status < 300:
            return "delivered", "activity_accepted"
        if status in {408, 425, 429} or status >= 500:
            return "retry", f"http_{status}_retryable"
        return "terminal", f"http_{status}_terminal"

    async def _dispatch_hermes_linear_activity(
        self, inbox: HermesLinearDeliveryInbox, worker_id: str
    ) -> bool:
        activity = await asyncio.to_thread(
            inbox.lease_activity,
            worker_id=worker_id,
            now=int(time.time()),
            lease_seconds=self._hermes_linear_lease_seconds,
        )
        if activity is None:
            return False
        delivery = await asyncio.to_thread(inbox.get, activity.delivery_key)
        route_config = self._routes.get(delivery.route_name, {}) if delivery else {}
        url = str(route_config.get("linear_activity_url") or "").strip()
        secret = str(route_config.get("linear_activity_secret") or "")
        if not url or not secret:
            await asyncio.to_thread(
                inbox.finish_activity,
                activity=activity,
                worker_id=worker_id,
                result="retry",
                now=int(time.time()),
                reason_code="activity_transport_unconfigured",
            )
            return True
        result, reason = await asyncio.to_thread(
            self._post_hermes_linear_activity, url, secret, activity.request_body
        )
        await asyncio.to_thread(
            inbox.finish_activity,
            activity=activity,
            worker_id=worker_id,
            result=result,
            now=int(time.time()),
            reason_code=reason,
        )
        logger.info(
            "[webhook] Hermes Linear activity result delivery=%s kind=%s state=%s reason=%s",
            activity.delivery_key,
            activity.kind,
            result,
            reason,
        )
        return True

    @staticmethod
    def _hermes_linear_activity_body(
        event: MessageEvent, kind: Literal["action", "error"]
    ) -> bytes | None:
        payload = event.raw_message if isinstance(event.raw_message, dict) else None
        delivery_key = getattr(event, "_hermes_linear_delivery_key", None)
        if payload is None or not isinstance(delivery_key, str):
            return None
        session = payload.get("agentSession")
        destination = payload.get("destination")
        issue = payload.get("issue")
        if not isinstance(session, dict) or not isinstance(destination, dict):
            return None
        issue_identifier = issue.get("identifier") if isinstance(issue, dict) else None
        activity: dict[str, Any]
        if kind == "action":
            activity = {
                "type": "action",
                "action": "Hermes execution started",
                "parameter": str(issue_identifier or "Linear agent session"),
                "dedupeKey": f"hermes-execution-start:{delivery_key}",
                "ephemeral": True,
                "contextualMetadata": {
                    "source": "hermes-linear-ingress",
                    "deliveryKey": delivery_key,
                },
            }
        else:
            activity = {
                "type": "error",
                "body": "Hermes could not complete this delegated execution.",
                "dedupeKey": f"hermes-execution-error:{delivery_key}",
                "ephemeral": False,
                "contextualMetadata": {
                    "source": "hermes-linear-ingress",
                    "deliveryKey": delivery_key,
                },
            }
        body = {
            "agentSessionId": session.get("id"),
            "organizationId": payload.get("organizationId"),
            "appUserId": destination.get("appUserId"),
            "activity": activity,
        }
        return json.dumps(body, separators=(",", ":")).encode()

    async def _hermes_linear_inbox_context(
        self, event: MessageEvent
    ) -> tuple[HermesLinearDeliveryInbox, HermesLinearInboxDelivery] | None:
        route_name = event.source.user_name if event.source is not None else None
        if not isinstance(route_name, str):
            return None
        inbox = self._hermes_linear_delivery_inboxes.get(route_name)
        if inbox is None:
            return None
        delivery_key = getattr(event, "_hermes_linear_delivery_key", None)
        if isinstance(delivery_key, str):
            row = await asyncio.to_thread(inbox.get, delivery_key)
            return (inbox, row) if row is not None else None
        chat_id = str(getattr(event.source, "chat_id", ""))
        match = re.search(r":hermes-linear-([0-9a-f]{32})$", chat_id)
        if match is None:
            return None
        row = await asyncio.to_thread(inbox.get_by_execution_prefix, match.group(1))
        return (inbox, row) if row is not None else None

    def _hermes_linear_error_activity_from_delivery(
        self, delivery: HermesLinearInboxDelivery
    ) -> bytes | None:
        try:
            event = self._build_hermes_linear_event(delivery)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None
        return self._hermes_linear_activity_body(event, "error")

    async def on_processing_start(self, event: MessageEvent) -> None:
        # The inbox worker queues the durable, idempotent start activity only
        # after handle_message() exposes the accepted session task. The generic
        # base hook intentionally swallows hook errors, so it cannot be the
        # acceptance/scheduling transaction.
        return

    async def on_processing_complete(self, event: "MessageEvent", outcome: Any) -> None:
        """Close the per-delivery webhook session once its run finishes.

        A webhook delivery is one-shot: the ``delivery_id`` is baked into the
        session key, so the session will never receive a second turn.  Mirror
        the cron completion path (``cron/scheduler.py`` →
        ``end_session(..., "cron_complete")``) by marking the session ended
        when the run completes.  Without this, webhook sessions keep
        ``ended_at`` NULL forever; ``SessionDB.prune_sessions`` only reaps
        rows with ``ended_at`` set, so unclosed webhook sessions accumulate
        unbounded and drive state.db bloat (the ghost-session leak).

        This hook is the one seam that runs at the TRUE end of the run:
        ``BasePlatformAdapter._process_message_background`` fires it after the
        message handler returns, on the success, failure, and cancellation
        paths alike — so error runs are reaped too.  (``handle_message`` is
        fire-and-forget; wrapping IT closes before the run even starts.)
        ``end_session()`` is first-reason-wins and no-ops on an already-ended
        row, so this never clobbers a ``compression``/``agent_close`` reason.
        """
        context = await self._hermes_linear_inbox_context(event)
        if context is not None:
            inbox, delivery = context
            delivery_key = delivery.delivery_key
            if delivery.state in {"scheduled", "started", "failed"}:
                succeeded = outcome == ProcessingOutcome.SUCCESS
                self._run_hermes_linear_failpoint(
                    "before_finish_transition", delivery_key
                )
                await asyncio.to_thread(
                    inbox.finish,
                    delivery_key=delivery_key,
                    outcome="completed" if succeeded else "failed",
                    now=int(time.time()),
                    reason_code=(
                        "execution_completed" if succeeded else "execution_failed"
                    ),
                    error_activity_body=(
                        None
                        if succeeded
                        else (
                            self._hermes_linear_activity_body(event, "error")
                            or self._hermes_linear_error_activity_from_delivery(
                                delivery
                            )
                        )
                    ),
                )
                if not succeeded:
                    self._hermes_linear_activity_worker_wake.set()
                self._run_hermes_linear_failpoint(
                    "after_finish_transition", delivery_key
                )
        await self._end_webhook_session(event, event.source.chat_id)

    async def _end_webhook_session(
        self, event: "MessageEvent", session_chat_id: str
    ) -> None:
        """Mark the per-delivery webhook session ended in state.db.

        Resolves the persisted ``session_id`` from the gateway session store
        using the SAME source the run was keyed on (so profile multiplexing
        and key construction match exactly), then closes it via the existing
        ``SessionDB.end_session`` API — never a hand-written UPDATE.
        """
        runner = self.gateway_runner
        if runner is None:
            return
        session_db = getattr(runner, "_session_db", None)
        store = getattr(runner, "session_store", None)
        if session_db is None or store is None:
            return
        try:
            key_fn = getattr(runner, "_session_key_for_source", None)
            if key_fn is None:
                return
            session_key = key_fn(event.source)
            # Resolve the persisted session_id via the store's public,
            # lock-held accessor (peek_session_id) rather than reaching into
            # the private _entries dict without the store lock. Fall back to
            # the private path only for older stores / test doubles that
            # predate the accessor.
            peek = getattr(store, "peek_session_id", None)
            if callable(peek):
                session_id = peek(session_key)
            else:
                if hasattr(store, "_ensure_loaded"):
                    try:
                        store._ensure_loaded()
                    except Exception:
                        pass
                entries = getattr(store, "_entries", {}) or {}
                entry = entries.get(session_key)
                session_id = getattr(entry, "session_id", None) if entry else None
            if not session_id:
                logger.debug(
                    "[webhook] No session_id to close for %s (key=%s)",
                    session_chat_id,
                    session_key,
                )
                return
            # AsyncSessionDB forwards end_session via asyncio.to_thread; a
            # plain SessionDB exposes it synchronously.  Handle both.
            _end = session_db.end_session
            result = _end(session_id, "webhook_complete")
            if asyncio.iscoroutine(result):
                await result
            logger.debug(
                "[webhook] Closed session %s for delivery %s",
                session_id,
                session_chat_id,
            )
        except Exception as e:
            logger.debug(
                "[webhook] Failed to close session for %s: %s",
                session_chat_id,
                e,
            )

    # ------------------------------------------------------------------
    # Signature validation
    # ------------------------------------------------------------------

    def _validate_signature(
        self, request: "web.Request", body: bytes, secret: str
    ) -> bool:
        """Validate webhook signature (GitHub, GitLab, Svix, generic HMAC-SHA256)."""

        def _header(name: str) -> str:
            return (
                request.headers.get(name, "")
                or request.headers.get(name.lower(), "")
                or request.headers.get(name.upper(), "")
            )

        # Svix / AgentMail:
        #   svix-id: msg_...
        #   svix-timestamp: unix seconds
        #   svix-signature: v1,<base64-hmac> [v1,<base64-hmac> ...]
        # Signed content is: "{id}.{timestamp}.{raw_body}".  Svix secrets
        # usually start with "whsec_" and the remainder is base64-encoded.
        svix_id = _header("svix-id")
        svix_timestamp = _header("svix-timestamp")
        svix_signature = _header("svix-signature")
        if svix_id or svix_timestamp or svix_signature:
            return self._validate_svix_signature(
                body=body,
                secret=secret,
                msg_id=svix_id,
                timestamp=svix_timestamp,
                signature_header=svix_signature,
            )

        # GitHub: X-Hub-Signature-256 = sha256=<hex>
        gh_sig = request.headers.get("X-Hub-Signature-256", "")
        if gh_sig:
            expected = (
                "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            )
            return hmac.compare_digest(gh_sig, expected)

        # GitLab: X-Gitlab-Token = <plain secret>
        gl_token = request.headers.get("X-Gitlab-Token", "")
        if gl_token:
            return hmac.compare_digest(gl_token, secret)

        # Generic: X-Webhook-Signature = <hex HMAC-SHA256>
        generic_sig = request.headers.get("X-Webhook-Signature", "")
        if generic_sig:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(generic_sig, expected)

        # No recognised signature header but secret is configured → reject
        logger.debug("[webhook] Secret configured but no signature header found")
        return False

    def _validate_svix_signature(
        self,
        body: bytes,
        secret: str,
        msg_id: str,
        timestamp: str,
        signature_header: str,
        tolerance_seconds: int = 300,
    ) -> bool:
        """Validate Svix-compatible signatures used by AgentMail webhooks."""
        if not (msg_id and timestamp and signature_header and secret):
            return False

        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(int(time.time()) - ts) > tolerance_seconds:
            logger.warning("[webhook] Svix signature timestamp outside replay window")
            return False

        if secret.startswith("whsec_"):
            encoded_secret = secret.removeprefix("whsec_")
            try:
                key = base64.b64decode(encoded_secret, validate=True)
            except (binascii.Error, ValueError):
                logger.debug("[webhook] Invalid whsec_ Svix signing secret")
                return False
        else:
            # Be permissive for providers that document Svix-style headers but
            # hand out raw shared secrets rather than whsec_ base64 secrets.
            logger.debug("[webhook] Validating Svix-style signature with raw secret")
            key = secret.encode()

        signed_content = msg_id.encode() + b"." + timestamp.encode() + b"." + body
        expected = base64.b64encode(
            hmac.new(key, signed_content, hashlib.sha256).digest()
        ).decode()

        # Svix can send multiple signatures separated by spaces during secret
        # rotation. Each entry is formatted as "vN,<base64>".
        for part in signature_header.split():
            try:
                version, signature = part.split(",", 1)
            except ValueError:
                continue
            if version == "v1" and hmac.compare_digest(signature, expected):
                return True
        return False

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        template: str,
        payload: dict,
        event_type: str,
        route_name: str,
    ) -> str:
        """Render a prompt template with the webhook payload.

        Supports dot-notation access into nested dicts:
        ``{pull_request.title}`` → ``payload["pull_request"]["title"]``

        Special token ``{__raw__}`` dumps the entire payload as indented
        JSON (truncated to 4000 chars).  Useful for monitoring alerts or
        any webhook where the agent needs to see the full payload.
        """
        if not template:
            truncated = json.dumps(payload, indent=2)[:4000]
            return (
                f"Webhook event '{event_type}' on route "
                f"'{route_name}':\n\n```json\n{truncated}\n```"
            )

        def _resolve(match: re.Match) -> str:
            key = match.group(1)
            # Special token: dump the entire payload as JSON
            if key == "__raw__":
                return json.dumps(payload, indent=2)[:4000]
            value: Any = payload
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part, f"{{{key}}}")
                else:
                    return f"{{{key}}}"
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2)[:2000]
            return str(value)

        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _resolve, template)

    def _render_delivery_extra(self, extra: dict, payload: dict) -> dict:
        """Render delivery_extra template values with payload data."""
        rendered: Dict[str, Any] = {}
        for key, value in extra.items():
            if isinstance(value, str):
                rendered[key] = self._render_prompt(value, payload, "", "")
            else:
                rendered[key] = value
        return rendered

    # ------------------------------------------------------------------
    # Response delivery
    # ------------------------------------------------------------------

    async def _direct_deliver(self, content: str, delivery: dict) -> SendResult:
        """Deliver *content* directly without invoking the agent.

        Used by ``deliver_only`` routes: the rendered template becomes the
        literal message body, and we dispatch to the same delivery helpers
        that the agent-mode ``send()`` flow uses.  All target types that
        work in agent mode work here — Telegram, Discord, Slack, GitHub
        PR comments, etc.
        """
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            # Shouldn't reach here — startup validation rejects deliver_only
            # with deliver=log — but guard defensively.
            logger.info("[webhook] direct-deliver log-only: %s", content[:200])
            return SendResult(success=True)

        if deliver_type == "linear_comment":
            return await self._deliver_linear_comment(content, delivery)

        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)

        # Fall through to the cross-platform dispatcher, which validates the
        # target name and routes via the gateway runner.
        return await self._deliver_cross_platform(deliver_type, content, delivery)

    def _handle_background_task_done(
        self,
        task: "asyncio.Task[Any]",
        route_name: str,
        delivery_id: str,
    ) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        logger.exception(
            "[webhook] Background handler failed route=%s delivery=%s",
            route_name,
            delivery_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    async def _deliver_linear_comment(self, content: str, delivery: dict) -> SendResult:
        """Post agent response as a Linear issue comment."""
        extra = delivery.get("deliver_extra", {})
        payload = delivery.get("payload", {})
        session = payload.get("agentSession") if isinstance(payload, dict) else {}
        session = session if isinstance(session, dict) else {}
        issue = session.get("issue") if isinstance(session, dict) else {}
        if not isinstance(issue, dict) and isinstance(payload, dict):
            issue = payload.get("issue", {})
        issue = issue if isinstance(issue, dict) else {}

        issue_id = str(extra.get("issue_id") or issue.get("id") or "").strip()
        issue_identifier = str(
            extra.get("issue_identifier") or issue.get("identifier") or ""
        ).strip()
        if not issue_id and issue_identifier:
            issue_id = issue_identifier
        if not issue_id:
            logger.error(
                "[webhook] linear_comment delivery missing issue_id/issue_identifier"
            )
            return SendResult(
                success=False, error="Missing issue_id or issue_identifier"
            )

        token = os.getenv("LINEAR_API_KEY") or os.getenv("LINEAR_API_TOKEN")
        if not token:
            logger.error(
                "[webhook] LINEAR_API_KEY is required for linear_comment delivery"
            )
            return SendResult(success=False, error="Missing LINEAR_API_KEY")

        body = str(content or "").strip()
        if not body:
            return SendResult(success=True)
        marker = str(extra.get("marker") or "").strip()
        if marker and marker not in body:
            body = f"{marker}\n\n{body}"

        mutation = """
        mutation WebhookLinearComment($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment { id url }
          }
        }
        """
        request_body = json.dumps({
            "query": mutation,
            "variables": {"input": {"issueId": issue_id, "body": body}},
        }).encode("utf-8")

        def _post() -> dict:
            req = urllib.request.Request(
                "https://api.linear.app/graphql",
                data=request_body,
                headers={
                    "Authorization": token,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)

        try:
            result = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            logger.error("[webhook] Linear comment HTTP error: %s", detail)
            return SendResult(success=False, error=detail or str(e))
        except Exception as e:
            logger.error("[webhook] Linear comment delivery error: %s", e)
            return SendResult(success=False, error=str(e))

        errors = result.get("errors")
        if errors:
            logger.error("[webhook] Linear comment GraphQL errors: %s", errors)
            return SendResult(success=False, error=json.dumps(errors)[:500])

        comment_create = (result.get("data") or {}).get("commentCreate") or {}
        if not comment_create.get("success"):
            logger.error("[webhook] Linear commentCreate did not succeed")
            return SendResult(success=False, error="commentCreate failed")

        comment = comment_create.get("comment") or {}
        logger.info(
            "[webhook] Posted Linear comment issue=%s comment=%s",
            issue_identifier or issue_id,
            comment.get("id") or "?",
        )
        return SendResult(success=True, message_id=comment.get("id"))

    async def _deliver_github_comment(self, content: str, delivery: dict) -> SendResult:
        """Post agent response as a GitHub PR/issue comment via ``gh`` CLI."""
        extra = delivery.get("deliver_extra", {})
        repo = extra.get("repo", "")
        pr_number = extra.get("pr_number", "")

        if not repo or not pr_number:
            logger.error("[webhook] github_comment delivery missing repo or pr_number")
            return SendResult(success=False, error="Missing repo or pr_number")

        # --- Input validation (prevent CLI argument injection) ---
        # pr_number must be a positive integer.
        try:
            pr_int = int(pr_number)
            if pr_int <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            logger.error("[webhook] invalid pr_number: %r", pr_number)
            return SendResult(success=False, error="Invalid pr_number")

        # repo must match owner/name (alphanumeric, hyphens, underscores, dots).
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo):
            logger.error("[webhook] invalid repo format: %r", repo)
            return SendResult(success=False, error="Invalid repo format")

        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(pr_int),
                    "--repo",
                    repo,
                    "--body",
                    content,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info("[webhook] Posted comment on %s#%s", repo, pr_number)
                return SendResult(success=True)
            else:
                logger.error("[webhook] gh pr comment failed: %s", result.stderr)
                return SendResult(success=False, error=result.stderr)
        except FileNotFoundError:
            logger.error(
                "[webhook] 'gh' CLI not found — install GitHub CLI for "
                "github_comment delivery"
            )
            return SendResult(success=False, error="gh CLI not installed")
        except Exception as e:
            logger.error("[webhook] github_comment delivery error: %s", e)
            return SendResult(success=False, error=str(e))

    async def _deliver_cross_platform(
        self, platform_name: str, content: str, delivery: dict
    ) -> SendResult:
        """Route response to another platform (telegram, discord, etc.)."""
        if not self.gateway_runner:
            return SendResult(
                success=False,
                error="No gateway runner for cross-platform delivery",
            )

        try:
            target_platform = Platform(platform_name)
        except ValueError:
            return SendResult(success=False, error=f"Unknown platform: {platform_name}")

        adapter = self.gateway_runner.adapters.get(target_platform)
        if not adapter:
            return SendResult(
                success=False,
                error=f"Platform {platform_name} not connected",
            )

        # Use home channel if no specific chat_id in deliver_extra
        extra = delivery.get("deliver_extra", {})
        chat_id = extra.get("chat_id", "")
        if not chat_id:
            home = self.gateway_runner.config.get_home_channel(target_platform)
            if home:
                chat_id = home.chat_id
            else:
                return SendResult(
                    success=False,
                    error=f"No chat_id or home channel for {platform_name}",
                )

        # Pass thread_id from deliver_extra so Telegram forum topics work
        metadata = None
        thread_id = extra.get("message_thread_id") or extra.get("thread_id")
        if thread_id:
            metadata = {"thread_id": thread_id}

        return await adapter.send(chat_id, content, metadata=metadata)
