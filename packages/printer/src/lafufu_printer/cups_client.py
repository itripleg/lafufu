"""CUPS print client. Shells out to `lp` for simplicity / no native deps."""

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


class CupsUnavailable(Exception):
    pass


class CupsClient:
    def __init__(self, printer_name: str | None = None) -> None:
        self._printer_name = printer_name
        self._lp = shutil.which("lp")
        self._lpstat = shutil.which("lpstat")

    @property
    def available(self) -> bool:
        return self._lp is not None and self._lpstat is not None

    def list_printers(self) -> list[str]:
        if not self._lpstat:
            return []
        try:
            out = subprocess.check_output([self._lpstat, "-p"], text=True, timeout=5)
        except subprocess.SubprocessError as e:
            log.warning("lpstat.failed error=%s", e)
            return []
        names: list[str] = []
        for line in out.splitlines():
            if line.startswith("printer "):
                # "printer NAME is idle. ..."
                parts = line.split()
                if len(parts) >= 2:
                    names.append(parts[1])
        return names

    def default_printer(self) -> str | None:
        if self._printer_name:
            return self._printer_name
        printers = self.list_printers()
        return printers[0] if printers else None

    def print_text(self, text: str, *, title: str | None = None) -> str:
        """Print text. Returns job id (best effort)."""
        if not self._lp:
            raise CupsUnavailable("`lp` not on PATH")
        printer = self.default_printer()
        if not printer:
            raise CupsUnavailable("no CUPS printers configured")
        cmd = [self._lp, "-d", printer]
        if title:
            cmd += ["-t", title]
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise CupsUnavailable(f"lp exited {result.returncode}: {result.stderr.decode()}")
        out = result.stdout.decode().strip()
        return out.split()[3] if "request id is" in out else "?"

    def print_file(self, path, *, title: str | None = None) -> str:
        """Print an existing file (image / pdf / etc.) by path."""
        if not self._lp:
            raise CupsUnavailable("`lp` not on PATH")
        printer = self.default_printer()
        if not printer:
            raise CupsUnavailable("no CUPS printers configured")
        cmd = [self._lp, "-d", printer]
        if title:
            cmd += ["-t", title]
        cmd.append(str(path))
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise CupsUnavailable(f"lp exited {result.returncode}: {result.stderr.decode()}")
        out = result.stdout.decode().strip()
        return out.split()[3] if "request id is" in out else "?"
