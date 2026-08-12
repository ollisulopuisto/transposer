"""oemer backend -- an end-to-end neural OMR that outputs MusicXML directly.

oemer (https://github.com/BreezeWhite/oemer) segments the page with a U-Net,
then reconstructs notes, beams and voices. It copes with piano grand staves and
needs no Java, which makes it the easiest engine to install. It downloads
roughly 200 MB of ONNX weights from GitHub on first use, so it needs one
network round trip before it works offline.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from ..errors import OmrFailedError
from ..ingest import IngestedInput
from .base import Availability, OmrEngine, OmrResult


def _find() -> str | None:
    """Locate the oemer console script.

    ``shutil.which`` only sees ``PATH``, which misses the common case of a
    virtualenv whose ``bin`` directory has not been activated -- so look next
    to the running interpreter too.
    """
    found = shutil.which("oemer")
    if found:
        return found
    sibling = Path(sys.executable).with_name("oemer")
    return str(sibling) if sibling.is_file() else None


class OemerEngine(OmrEngine):
    name = "oemer"
    description = "oemer (Python/ONNX) -- neural end-to-end OMR, no Java needed"
    accepts_pdf = False
    priority = 30

    def __init__(self, executable: str | None = None, timeout: int = 3600) -> None:
        self.executable = executable or os.environ.get("TRANSPOSER_OEMER") or _find()
        self.timeout = timeout

    def availability(self) -> Availability:
        if not self.executable:
            return Availability(
                False,
                "oemer is not installed (pip install 'transposer[oemer]')",
            )
        return Availability(True)

    def recognize(self, source: IngestedInput, workdir: Path) -> OmrResult:
        self.require_available()
        assert self.executable is not None

        workdir = Path(workdir)
        out_dir = workdir / "oemer"
        out_dir.mkdir(parents=True, exist_ok=True)

        pages = source.rasterize()
        if len(pages) > 1:
            # oemer takes one image at a time and has no multi-page mode.
            raise OmrFailedError(
                f"oemer handles one page at a time; this input has {len(pages)}. "
                "Use the audiveris engine for multi-page documents."
            )

        completed = self._run(
            [self.executable, str(pages[0]), "--output-path", str(out_dir)],
            timeout=self.timeout,
        )
        log = (completed.stdout or "") + (completed.stderr or "")

        produced = self._newest(
            sorted(out_dir.glob("*.musicxml")) + sorted(out_dir.glob("*.xml"))
        )
        if produced is None:
            raise OmrFailedError(
                "oemer produced no MusicXML.\n" + "\n".join(log.splitlines()[-25:])
            )

        return OmrResult(
            musicxml=produced,
            engine=self.name,
            warnings=[
                "oemer reconstructs notes and voices but does not read chord "
                "symbols or lyrics.",
            ],
            log=log,
        )
