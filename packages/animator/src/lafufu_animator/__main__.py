"""Entry point: python -m lafufu_animator"""

import asyncio

from .dxl_bus import DxlBus
from .service import AnimatorService


def main() -> None:
    bus = DxlBus()  # auto-detects on real Pi
    svc = AnimatorService(bus=bus)
    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
