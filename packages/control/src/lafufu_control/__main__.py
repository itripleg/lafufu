"""Entry: python -m lafufu_control"""

import asyncio
import os

from .service import ControlService


def main() -> None:
    port = int(os.environ.get("LAFUFU_CONTROL_PORT", "8080"))
    asyncio.run(ControlService(port=port).run())


if __name__ == "__main__":
    main()
