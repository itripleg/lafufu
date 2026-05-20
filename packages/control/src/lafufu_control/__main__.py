"""Entry: python -m lafufu_control"""

import asyncio
import os

from .service import ControlService


def main() -> None:
    port = int(os.environ.get("LAFUFU_CONTROL_PORT", "8080"))
    # Optional shared-token auth. Unset / empty → auth disabled (loopback-only
    # deployments). Set it to require a token from non-loopback clients.
    api_token = os.environ.get("LAFUFU_API_TOKEN", "").strip()
    asyncio.run(ControlService(port=port, api_token=api_token).run())


if __name__ == "__main__":
    main()
