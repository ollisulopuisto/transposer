"""Audiveris backend -- the recommended engine for real-world scores.

Audiveris is a mature Java OMR application. It reads PDFs directly, handles
multi-staff piano systems, and (with the right switches) recovers chord symbols
and lyrics, which is exactly what a lead sheet needs.

This module never bundles Audiveris; it locates an installed copy. Point
``TRANSPOSER_AUDIVERIS`` at the launcher if it is not on ``PATH``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import OmrFailedError
from ..ingest import IngestedInput
from .base import Availability, OmrEngine, OmrResult

#: Names the Audiveris launcher goes by across packagings.
_LAUNCHER_NAMES = ("audiveris", "Audiveris", "audiveris.sh")

#: Processing switches we turn on for lead sheets. Audiveris leaves chord-name
#: recognition off by default because it costs a text-recognition pass.
_DEFAULT_SWITCHES = {
    "chordNames": "true",
    "lyrics": "true",
}

_SWITCH_PREFIX = "org.audiveris.omr.sheet.ProcessingSwitches"


class AudiverisEngine(OmrEngine):
    name = "audiveris"
    description = "Audiveris 5.x (Java) -- best quality, reads PDFs, finds chord symbols and lyrics"
    accepts_pdf = True
    priority = 10

    def __init__(
        self,
        launcher: str | Path | None = None,
        switches: dict[str, str] | None = None,
        timeout: int = 3600,
        java_home: str | None = None,
        tessdata: str | None = None,
    ) -> None:
        self.launcher = str(launcher) if launcher else self._find_launcher()
        self.switches = {**_DEFAULT_SWITCHES, **(switches or {})}
        self.timeout = timeout
        self.java_home = java_home or os.environ.get("TRANSPOSER_JAVA_HOME") or os.environ.get("JAVA_HOME")
        self.tessdata = tessdata or os.environ.get("TESSDATA_PREFIX")

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _find_launcher() -> str | None:
        explicit = os.environ.get("TRANSPOSER_AUDIVERIS")
        if explicit:
            return explicit
        for candidate in _LAUNCHER_NAMES:
            found = shutil.which(candidate)
            if found:
                return found
        home = os.environ.get("AUDIVERIS_HOME")
        if home:
            for candidate in _LAUNCHER_NAMES:
                path = Path(home) / "bin" / candidate
                if path.is_file():
                    return str(path)
        return None

    def availability(self) -> Availability:
        if not self.launcher:
            return Availability(
                False,
                "Audiveris was not found. Install it and put its launcher on PATH, "
                "or set TRANSPOSER_AUDIVERIS to the launcher script.",
            )
        if not Path(self.launcher).is_file() and not shutil.which(self.launcher):
            return Availability(False, f"{self.launcher} is not executable")
        return Availability(True, version="5.x")

    # -- recognition -------------------------------------------------------

    def recognize(self, source: IngestedInput, workdir: Path) -> OmrResult:
        self.require_available()

        out_dir = Path(workdir) / "audiveris"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Audiveris reads PDFs natively, and the preprocessing pass hands back
        # an enhanced PDF when it ran -- so ``source.path`` is already the best
        # available version either way. A bare image still has to be rasterised
        # to normalise its colour depth.
        target = source.path
        if source.kind == "image":
            target = source.rasterize()[0]

        command = [
            self.launcher,
            "-batch",
            "-export",
            "-output",
            str(out_dir),
        ]
        for switch, value in self.switches.items():
            command += ["-option", f"{_SWITCH_PREFIX}.{switch}={value}"]
        command += ["--", str(target)]

        env = os.environ.copy()
        env.setdefault("JAVA_OPTS", "-Djava.awt.headless=true -Xmx4g")
        if self.java_home:
            env["JAVA_HOME"] = self.java_home
        if self.tessdata:
            env["TESSDATA_PREFIX"] = self.tessdata

        completed = self._run(command, env=env, timeout=self.timeout)
        log = (completed.stdout or "") + (completed.stderr or "")

        exported = sorted(out_dir.glob("*.mxl")) + sorted(out_dir.glob("*.xml"))
        exported = [p for p in exported if p.name != "book.xml"]
        best = self._newest(exported)

        if best is None:
            raise OmrFailedError(
                "Audiveris produced no MusicXML export.\n"
                + _tail(log, 40)
            )

        warnings = _collect_warnings(log)
        return OmrResult(
            musicxml=best,
            engine=self.name,
            warnings=warnings,
            artifacts=_artifacts(out_dir),
            log=log,
        )


def _artifacts(out_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for omr in out_dir.glob("*.omr"):
        found["omr_project"] = omr
        break
    for log in out_dir.glob("*.log"):
        found["engine_log"] = log
        break
    return found


def _collect_warnings(log: str) -> list[str]:
    """Pull the lines that tell a user something actionable went wrong."""
    interesting: list[str] = []
    for line in log.splitlines():
        if "no correct rhythm" in line or "too long" in line:
            interesting.append(line.strip())
        elif "No installed OCR languages" in line or "couldn't load any languages" in line:
            interesting.append(
                "Tesseract has no usable language data, so lyrics and chord "
                "names were skipped. Install the full eng.traineddata (the "
                "legacy-capable one) and point TESSDATA_PREFIX at it."
            )
    # Rhythm complaints are common and repetitive; summarise rather than list.
    rhythm = [line for line in interesting if "rhythm" in line or "too long" in line]
    others = [line for line in interesting if line not in rhythm]
    summary = list(others)
    if rhythm:
        summary.append(
            f"Audiveris flagged {len(rhythm)} measure(s) whose rhythm did not add "
            "up; check those bars against the original."
        )
    return summary


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])
