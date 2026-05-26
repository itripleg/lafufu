"""Sniff a few NATS topics for the E2E test. Runs until SIGINT or N events
received on either topic. Prints (topic, payload) per message and exits 0
when the requested counts are seen.

Usage:
    uv run python scripts/sniff_nats_e2e.py <topic> [<topic>...] --count N
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import nats


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("topics", nargs="+")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args()

    nc = await nats.connect("nats://localhost:4222")
    received: list[tuple[str, dict]] = []

    async def cb(msg):
        try:
            payload = json.loads(msg.data.decode())
        except Exception:
            payload = msg.data.decode()
        received.append((msg.subject, payload))
        print(
            f"<- {msg.subject}  {json.dumps(payload) if isinstance(payload, dict) else payload}",
            flush=True,
        )

    for topic in args.topics:
        await nc.subscribe(topic, cb=cb)

    deadline = asyncio.get_event_loop().time() + args.timeout
    while len(received) < args.count and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)

    await nc.drain()
    return 0 if len(received) >= args.count else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
