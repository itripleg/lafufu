"""NATS↔WebSocket bridge with lazy subscription.

Tracks per-WebSocket topic interest. Maintains one NATS subscription per
unique topic pattern; ref-counted. When the last interested WS disconnects
from a pattern, the bridge unsubscribes from NATS.
"""

import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from nats.aio.subscription import Subscription as NatsSubscription
from starlette.routing import Mount, WebSocketRoute

from .auth import ws_authorized

log = logging.getLogger(__name__)

# Patterns the SPA is known to subscribe to.  Any browser-supplied pattern
# not in this set (or not starting with an allowed prefix) is rejected so a
# curious-but-authed client cannot read arbitrary bus traffic.
#
# Prefix rule: all "config.changed.*" subjects are allowed as a family —
# use_servo_config.ts subscribes to individual animator.<servo>.default keys,
# and settings panels may add more in the future.
#
# The ">" firehose is intentionally included: system_pulse.tsx uses it, and
# access is gated by the shared-token auth layer that guards the WS endpoint.
ALLOWED_WS_PATTERNS: frozenset[str] = frozenset(
    {
        # agent status / dialogue (face.tsx, chat_log.tsx)
        "agent.state.*",
        "agent.reply",
        "agent.transcript",
        "agent.tts.rms",
        # animator (pet.tsx)
        "animator.pose",
        # system health (service_status.tsx, admin.tsx)
        "system.heartbeat.*",
        "*.state.*",
        "system.service.*",
        # firehose used by system_pulse.tsx — gated by token auth
        ">",
        # image / expression / frame library invalidation
        "expressions.changed",
        "frames.changed",
        # config change notifications — whole family allowed by prefix check below
        # (individual entries would be config.changed.animator.<servo>.default)
    }
)

# Prefixes that implicitly allow any pattern that starts with them.
_ALLOWED_PREFIXES: tuple[str, ...] = ("config.changed.",)


class WsBridge:
    def __init__(self, nats_client, *, allowed_patterns: frozenset[str] | None = None) -> None:
        self.nats = nats_client
        # Production default: the curated SPA allow-list.  Tests that use
        # synthetic subject names (test.echo, x.y, …) pass their own set.
        self._allowed: frozenset[str] = (
            allowed_patterns if allowed_patterns is not None else ALLOWED_WS_PATTERNS
        )
        # pattern → (NatsSubscription, ref_count)
        self._subs: dict[str, tuple[NatsSubscription, int]] = {}
        # connected websockets → set of patterns they care about
        self._ws_patterns: dict[WebSocket, set[str]] = {}
        # pattern → set of websockets
        self._pattern_listeners: dict[str, set[WebSocket]] = {}
        # Serialises _add_sub / _remove_sub so the check-then-act ref-count
        # update is atomic across the subscribe/unsubscribe await.
        self._ref_lock = asyncio.Lock()

    def mount(self, app: FastAPI) -> None:
        bridge = self

        async def ws_endpoint(ws: WebSocket):
            # Same optional shared-token auth as the HTTP API. The browser sends
            # the lafufu_token cookie on the handshake automatically; loopback
            # (the kiosk) and the no-token-configured case are always allowed.
            if not ws_authorized(ws):
                await ws.close(code=1008)  # 1008 = policy violation
                return
            await ws.accept()
            bridge._ws_patterns[ws] = set()
            try:
                while True:
                    # Per-frame error isolation: a malformed JSON frame, an
                    # unknown op, or an exception in a single sub/unsub call
                    # should drop only that frame, not the whole session.
                    try:
                        frame = await ws.receive_json()
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        log.warning("ws.bad_frame error=%s", e)
                        continue
                    op = frame.get("op")
                    topics = frame.get("topics", []) or []
                    try:
                        if op == "sub":
                            for t in topics:
                                await bridge._add_sub(ws, t)
                        elif op == "unsub":
                            for t in topics:
                                await bridge._remove_sub(ws, t)
                    except Exception as e:
                        log.warning("ws.op_error op=%s error=%s", op, e)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                log.warning("ws.error error=%s", e)
            finally:
                for t in list(bridge._ws_patterns.get(ws, set())):
                    await bridge._remove_sub(ws, t)
                bridge._ws_patterns.pop(ws, None)

        # Insert the WS route before any catch-all Mount (e.g. StaticFiles at "/")
        # so that /ws is matched first regardless of app.mount() order.
        ws_route = WebSocketRoute("/ws", ws_endpoint, name="ws_bridge")
        mount_idx = next(
            (i for i, r in enumerate(app.routes) if isinstance(r, Mount)),
            len(app.routes),
        )
        app.routes.insert(mount_idx, ws_route)

    def _pattern_allowed(self, pattern: str) -> bool:
        """Return True if *pattern* is in the allow-list or matches an allowed prefix."""
        if pattern in self._allowed:
            return True
        return any(pattern.startswith(pfx) for pfx in _ALLOWED_PREFIXES)

    async def _add_sub(self, ws: WebSocket, pattern: str) -> None:
        if not self._pattern_allowed(pattern):
            log.warning("ws.subscribe.rejected pattern=%s", pattern)
            return
        async with self._ref_lock:
            self._ws_patterns[ws].add(pattern)
            self._pattern_listeners.setdefault(pattern, set()).add(ws)
            if pattern in self._subs:
                existing_sub, count = self._subs[pattern]
                self._subs[pattern] = (existing_sub, count + 1)
                return

            # First subscriber for this pattern — open NATS sub
            async def cb(msg):
                await self._fanout(pattern, msg.subject, msg.data)

            sub = await self.nats.subscribe(pattern, cb=cb)
            self._subs[pattern] = (sub, 1)

    async def _remove_sub(self, ws: WebSocket, pattern: str) -> None:
        async with self._ref_lock:
            listeners = self._pattern_listeners.get(pattern)
            if listeners is not None:
                listeners.discard(ws)
            self._ws_patterns.get(ws, set()).discard(pattern)
            if pattern in self._subs:
                sub, count = self._subs[pattern]
                count -= 1
                if count <= 0:
                    # Unsubscribe while holding the lock so no concurrent
                    # _add_sub can re-enter between the del and the network call.
                    with contextlib.suppress(Exception):
                        await sub.unsubscribe()
                    del self._subs[pattern]
                else:
                    self._subs[pattern] = (sub, count)

    async def _fanout(self, pattern: str, subject: str, data: bytes) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as e:
            log.debug("ws.fanout.bad_payload subject=%s error=%s", subject, e)
            return
        frame = {"topic": subject, "payload": payload}
        dead: list[WebSocket] = []
        # Iterate a SNAPSHOT — `ws.send_json` is an await point, and any other
        # coroutine (concurrent _add_sub/_remove_sub or a WS callback that
        # mutates its own subscription set) would otherwise cause
        # `RuntimeError: Set changed size during iteration`.
        for ws in list(self._pattern_listeners.get(pattern, set())):
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            with contextlib.suppress(Exception):
                await ws.close()

    # --- Inspection (used by tests) ---

    def nats_sub_count(self, pattern: str) -> int:
        return self._subs.get(pattern, (None, 0))[1]
