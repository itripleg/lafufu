"""Entry: python -m lafufu_printer"""

import asyncio
import os

from .cups_client import CupsClient
from .service import PrinterService


def main() -> None:
    cups = CupsClient(printer_name=os.environ.get("LAFUFU_PRINTER_NAME"))
    auto_print = os.environ.get("LAFUFU_PRINTER_AUTO", "true").lower() not in ("0", "false", "no")
    svc = PrinterService(cups=cups, auto_print=auto_print)
    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
