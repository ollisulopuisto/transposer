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
import subprocess
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
        probe_timeout: int = 60,
    ) -> None:
        self.launcher = str(launcher) if launcher else self._find_launcher()
        self.switches = {**_DEFAULT_SWITCHES, **(switches or {})}
        self.timeout = timeout
        self.java_home = (
            java_home
            or os.environ.get("TRANSPOSER_JAVA_HOME")
            or os.environ.get("JAVA_HOME")
        )
        self.tessdata = tessdata or os.environ.get("TESSDATA_PREFIX")
        self.probe_timeout = probe_timeout
        self._probed: Availability | None = None

    # -- the environment the JVM runs in -----------------------------------

    def build_env(self) -> dict[str, str]:
        """The environment Audiveris is launched with.

        Two of these are load-bearing on a headless Linux box.

        ``sun.java2d.uiScale`` stops Audiveris probing the monitor's scaling
        factor, which it does on Linux from a static initialiser, through GTK,
        via JNA -- and which it guards with ``catch (Exception)``. A missing
        libgtk-3 raises ``UnsatisfiedLinkError``, an Error rather than an
        Exception, so the guard does not catch it and the process dies before
        reading a note. Setting the scale makes it return before it looks.
        ``GDK_SCALE`` is the same escape hatch one branch earlier.

        Anything the user already put in ``JAVA_OPTS`` is kept: theirs come
        first, and a heap size they chose wins, since a later ``-Xmx`` would
        override an earlier one.
        """
        env = os.environ.copy()

        existing = env.get("JAVA_OPTS", "").strip()
        options = [existing] if existing else []
        options.append("-Djava.awt.headless=true")
        options.append("-Dsun.java2d.uiScale=1")
        if "-Xmx" not in existing:
            options.append("-Xmx4g")
        env["JAVA_OPTS"] = " ".join(options)

        env.setdefault("GDK_SCALE", "1")
        if self.java_home:
            env["JAVA_HOME"] = self.java_home
        if self.tessdata:
            env["TESSDATA_PREFIX"] = self.tessdata
        return env

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
        """Whether Audiveris is here *and* can start.

        Asking only whether the launcher exists is not enough, and the gap is
        not theoretical: an image shipped reporting Audiveris ``[ok]`` while
        every recognition failed, because the JVM died in a static initialiser
        the moment it ran. So the launcher is actually started once, and what it
        says on the way down is what the user is told.
        """
        if not self.launcher:
            return Availability(
                False,
                "Audiveris was not found. Install it and put its launcher on PATH, "
                "or set TRANSPOSER_AUDIVERIS to the launcher script.",
            )
        if not Path(self.launcher).is_file() and not shutil.which(self.launcher):
            return Availability(False, f"{self.launcher} is not executable")

        if self._probed is None:
            self._probed = self._probe()
        return self._probed

    def _probe(self) -> Availability:
        """Start the launcher once and see whether it comes up.

        ``-help`` is enough: the initialisers that fail run before the CLI is
        parsed, so a JVM that cannot start cannot print a usage message either.
        The result is cached per engine instance, because ``transposer engines``
        and the web UI's health check both ask and neither wants a JVM each
        time.
        """
        try:
            completed = subprocess.run(
                [self.launcher, "-help"],
                capture_output=True,
                text=True,
                timeout=self.probe_timeout,
                env=self.build_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Availability(
                False,
                f"{self.launcher} did not respond within {self.probe_timeout}s; "
                "it may be waiting for a display or a lock.",
            )
        except OSError as exc:
            return Availability(False, f"{self.launcher} could not be run: {exc}")

        if completed.returncode != 0:
            detail = _tail((completed.stderr or completed.stdout or "").strip(), 6)
            return Availability(
                False,
                "Audiveris is installed but fails to start:\n"
                + (detail or f"exit status {completed.returncode}"),
            )
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

        completed = self._run(command, env=self.build_env(), timeout=self.timeout)
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
