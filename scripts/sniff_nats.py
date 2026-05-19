"""Temporary debug script — subscribe to NATS '>' for N seconds, print events."""

import asyncio
import json
import sys

from nats.aio.client import Client as NATS


async def main(secs: int = 25) -> None:
    nc = NATS()
    await nc.connect("nats://localhost:4222")

    async def cb(msg):
        try:
            p = json.loads(msg.data)
        except Exception:
            p = msg.data.decode("utf-8", errors="replace")
        print(msg.subject, "->", json.dumps(p)[:240], flush=True)

    await nc.subscribe(">", cb=cb)
    print(f"subscribed, listening {secs}s", flush=True)
    await asyncio.sleep(secs)
    await nc.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 25))
