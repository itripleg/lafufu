"""NATS connection + typed publish/subscribe helpers."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import nats
from nats.aio.client import Client as NATS
from nats.aio.subscription import Subscription
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_RETRY_DELAYS_S = (1, 2, 5, 10, 30)


async def _on_disconnected() -> None:
    log.warning("nats.disconnected")


async def _on_reconnected() -> None:
    log.info("nats.reconnected")


async def _on_closed() -> None:
    log.warning("nats.closed")


async def _on_error(e: Exception) -> None:
    log.warning("nats.error error=%s", e)


async def connect_with_retry(
    url: str = "nats://localhost:4222", *, name: str = "lafufu-svc"
) -> NATS:
    """Connect to NATS with exponential backoff. Never gives up."""
    attempt = 0
    while True:
        try:
            client = await nats.connect(
                url,
                name=name,
                reconnect_time_wait=2,
                max_reconnect_attempts=-1,
                ping_interval=10,
                max_outstanding_pings=3,
                error_cb=_on_error,
                disconnected_cb=_on_disconnected,
                reconnected_cb=_on_reconnected,
                closed_cb=_on_closed,
            )
            log.info("nats.connected name=%s url=%s", name, url)
            return client
        except Exception as e:
            wait = _RETRY_DELAYS_S[min(attempt, len(_RETRY_DELAYS_S) - 1)]
            log.warning("nats.connect.failed attempt=%d wait=%ds error=%s", attempt, wait, e)
            await asyncio.sleep(wait)
            attempt += 1


async def publish_model(nc: NATS, subject: str, model: BaseModel) -> None:
    """Publish a pydantic model as JSON-encoded bytes."""
    await nc.publish(subject, model.model_dump_json().encode("utf-8"))


async def subscribe_model[T: BaseModel](
    nc: NATS,
    subject: str,
    schema: type[T],
    handler: Callable[[str, T], Awaitable[None]],
    *,
    queue: str | None = None,
) -> Subscription:
    """Subscribe with pydantic validation. Invalid payloads logged + dropped."""

    async def cb(msg):
        try:
            obj = schema.model_validate_json(msg.data)
        except ValidationError as e:
            log.warning(
                "payload.invalid subject=%s schema=%s error=%s", msg.subject, schema.__name__, e
            )
            return
        except Exception as e:
            log.warning("payload.decode_failed subject=%s error=%s", msg.subject, e)
            return
        try:
            await handler(msg.subject, obj)
        except Exception as e:
            log.exception("handler.raised subject=%s error=%s", msg.subject, e)

    return await nc.subscribe(subject, queue=queue, cb=cb)
