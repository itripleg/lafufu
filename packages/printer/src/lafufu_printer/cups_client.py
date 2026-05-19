"""CUPS print client. Shells out to `lp` for simplicity / no native deps."""

import contextlib
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

    def print_file(
        self,
        path,
        *,
        title: str | None = None,
        extra_lp_options: list[str] | None = None,
        target_size_px: tuple[int, int] | None = None,
        dead_zone_top_px: int = 0,
        dead_zone_bottom_px: int = 0,
    ) -> str:
        """Print an image file by path.

        If `target_size_px` is given, the image is pre-resized to exactly
        that pixel size before being sent to lp — bypassing CUPS' fit-to-page
        logic, which is unreliable across drivers (especially raster/label
        printers). This is the recommended path: compute target pixels from
        the page size at the printer's DPI and let lp print 1:1.
        """
        if not self._lp:
            raise CupsUnavailable("`lp` not on PATH")
        printer = self.default_printer()
        if not printer:
            raise CupsUnavailable("no CUPS printers configured")

        send_path = str(path)
        resized_temp: str | None = None
        if target_size_px:
            send_path = _prep_resized_copy(
                path, target_size_px, dead_zone_top_px, dead_zone_bottom_px
            )
            resized_temp = send_path  # remember so we can clean up after lp

        cmd = [self._lp, "-d", printer]
        if title:
            cmd += ["-t", title]
        if not target_size_px:
            # Without an explicit target, fall back to CUPS scaling. Send both
            # the old (fit-to-page) and new (print-scaling=fit) flags for
            # cross-version compat.
            cmd += ["-o", "fit-to-page", "-o", "print-scaling=fit"]
        if extra_lp_options:
            cmd += extra_lp_options
        cmd.append(send_path)
        log.info("lp.exec cmd=%s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
        finally:
            # Don't accumulate per-print temp files in /tmp.
            if resized_temp:
                import os as _os

                with contextlib.suppress(OSError):
                    _os.unlink(resized_temp)
        if result.returncode != 0:
            raise CupsUnavailable(f"lp exited {result.returncode}: {result.stderr.decode()}")
        out = result.stdout.decode().strip()
        return out.split()[3] if "request id is" in out else "?"


def _prep_resized_copy(
    src_path,
    target_size_px: tuple[int, int],
    dead_zone_top_px: int = 0,
    dead_zone_bottom_px: int = 0,
) -> str:
    """Resize src to fit inside the printable area (target minus dead zones),
    placed in the printable region of a target-sized canvas. Dead zones at
    top/bottom are left as white — the printer can't reach those pixels
    anyway, so anything we'd put there would be lost. Padding them
    explicitly means we don't waste image content on unreachable area."""
    import tempfile
    from pathlib import Path

    from PIL import Image

    src = Path(src_path)
    tw, th = target_size_px
    printable_h = max(1, th - dead_zone_top_px - dead_zone_bottom_px)
    im = Image.open(src).convert("RGB")
    iw, ih = im.size
    # Fit-inside the printable area (full width, reduced height).
    scale = min(tw / iw, printable_h / ih)
    new_w, new_h = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = im.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), "white")
    # Center horizontally; anchor to the TOP of the printable band so the
    # image starts immediately after the dead zone, not floating in the
    # middle. (Any aspect-mismatch slack becomes white space at the BOTTOM.)
    x = (tw - new_w) // 2
    y = dead_zone_top_px
    canvas.paste(resized, (x, y))
    # Unique temp filename so concurrent print jobs don't overwrite each
    # other's intermediate file mid-read.
    fd, out_path = tempfile.mkstemp(prefix=f"lafufu_print_{src.stem}_", suffix=".png")
    import os

    os.close(fd)
    out = Path(out_path)
    canvas.save(out, "PNG")
    log.info(
        "lp.resize src=%s -> %s @ %dx%d (printable %dx%d, top_dz=%d, bot_dz=%d)",
        src.name,
        out,
        tw,
        th,
        tw,
        printable_h,
        dead_zone_top_px,
        dead_zone_bottom_px,
    )
    return str(out)
