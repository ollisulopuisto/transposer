"""The contract every OMR engine implements."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import EngineUnavailableError
from ..ingest import IngestedInput


@dataclass
class Availability:
    """Whether an engine can run here, and why not if it cannot."""

    ok: bool
    reason: str = ""
    version: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.ok


@dataclass
class OmrResult:
    """What an engine produced."""

    musicxml: Path
    engine: str
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, Path] = field(default_factory=dict)
    log: str = ""


class OmrEngine(ABC):
    """Base class for recognition backends.

    Subclasses declare what they can eat (``accepts_pdf``), whether they can
    run in this environment (:meth:`availability`), and how to turn an input
    into MusicXML (:meth:`recognize`).
    """

    #: Short identifier used on the command line and in the web UI.
    name: str = "engine"
    #: One-line description shown in ``transposer engines``.
    description: str = ""
    #: True when the engine reads PDFs itself and should not be handed images.
    accepts_pdf: bool = False
    #: Rough ordering hint; lower numbers are tried first by ``--engine auto``.
    priority: int = 100

    @abstractmethod
    def availability(self) -> Availability:
        """Report whether this engine can run right now."""

    @abstractmethod
    def recognize(self, source: IngestedInput, workdir: Path) -> OmrResult:
        """Recognise ``source`` and return a path to MusicXML."""

    def require_available(self) -> None:
        status = self.availability()
        if not status.ok:
            raise EngineUnavailableError(f"OMR engine {self.name!r}: {status.reason}")

    # -- helpers shared by the subprocess-based engines --------------------

    @staticmethod
    def _run(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess, capturing output, without raising on failure."""
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _newest(paths: list[Path]) -> Path | None:
        existing = [p for p in paths if p.is_file()]
        if not existing:
            return None
        return max(existing, key=lambda p: p.stat().st_mtime)
