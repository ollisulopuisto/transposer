"""MuseScore renderer -- best-looking output when MuseScore is installed.

MuseScore's engraver handles collisions, lyric spacing and system breaks better
than anything else that runs headlessly, so it wins when it is present. It
needs a writable ``$HOME`` and, on a bare container, an X server stand-in; the
``-o`` batch mode below works under ``xvfb-run`` or with ``QT_QPA_PLATFORM``
set to ``offscreen``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import RenderFailedError
from ..omr.base import Availability
from .base import PageSize, Renderer, RenderResult

_BINARIES = ("mscore", "musescore", "mscore4portable", "musescore4", "musescore3", "mscore3")


class MuseScoreRenderer(Renderer):
    name = "musescore"
    description = "MuseScore CLI -- best engraving, needs MuseScore installed"

    def __init__(self, executable: str | None = None, timeout: int = 600) -> None:
        self.executable = executable or os.environ.get("TRANSPOSER_MUSESCORE") or _find()
        self.timeout = timeout

    def availability(self) -> Availability:
        if not self.executable:
            return Availability(
                False,
                "MuseScore was not found on PATH (set TRANSPOSER_MUSESCORE to its binary)",
            )
        return Availability(True)

    def render(
        self,
        musicxml: Path,
        target: Path,
        page_size: PageSize,
        scale: int = 40,
        workdir: Path | None = None,
    ) -> RenderResult:
        self.require_available()
        assert self.executable is not None

        import subprocess

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("XDG_RUNTIME_DIR", str(Path(workdir or target.parent)))

        completed = subprocess.run(
            [self.executable, "-o", str(target), str(musicxml)],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            check=False,
        )

        if not target.is_file() or target.stat().st_size == 0:
            raise RenderFailedError(
                "MuseScore produced no PDF.\n"
                + "\n".join((completed.stdout + completed.stderr).splitlines()[-20:])
            )

        warnings = []
        if page_size.name != "a4":
            warnings.append(
                f"MuseScore uses the page size stored in the score, so {page_size.name} "
                "was ignored; use the verovio renderer to force a paper size."
            )

        return RenderResult(
            pdf=target,
            renderer=self.name,
            page_count=_page_count(target),
            warnings=warnings,
        )


def _find() -> str | None:
    for name in _BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _page_count(pdf: Path) -> int:
    try:
        import pymupdf

        with pymupdf.open(pdf) as document:
            return document.page_count
    except Exception:  # pragma: no cover - cosmetic only
        return 0
