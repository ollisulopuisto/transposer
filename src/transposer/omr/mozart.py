"""Mozart backend -- the classical computer-vision OMR from aashrafh/Mozart.

Mozart binarises the page, segments staves, strips the staff lines, then
classifies each remaining glyph with an HOG + MLP model. It is small, fast,
dependency-light and completely offline, which makes it a genuinely useful
engine for the kind of input it was built for: **clean, printed, single-staff,
treble-clef monophonic music**.

It is not a lead-sheet engine. It has no notion of key signatures, grand
staves, lyrics, chord symbols, repeats or multiple voices, and its output is a
bracket notation rather than MusicXML. This wrapper does three things upstream
Mozart does not:

* normalises the input image to 8-bit RGB (upstream crashes on RGBA pages),
* restores the scikit-image behaviour it was written against, so it runs on a
  current stack (see :mod:`._mozart_runner`),
* runs it as a subprocess so a crash inside it cannot take the server down,
* converts its bracket notation to MusicXML via :mod:`.mozart_notation`.

Point ``TRANSPOSER_MOZART_DIR`` at a checkout of the Mozart repository, or run
``scripts/fetch_mozart.sh`` which puts one in ``third_party/mozart``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..errors import OmrFailedError
from ..ingest import IngestedInput
from .base import Availability, OmrEngine, OmrResult
from .mozart_notation import mozart_text_to_score

#: Where we look for a Mozart checkout when the environment says nothing.
_DEFAULT_LOCATIONS = (
    Path("third_party/mozart"),
    Path.home() / ".local/share/transposer/mozart",
    Path("/opt/mozart"),
)

_REQUIRED_MODULES = ("skimage", "sklearn", "cv2", "imutils")


class MozartEngine(OmrEngine):
    name = "mozart"
    description = (
        "Mozart (aashrafh/Mozart) -- offline CV+MLP engine for clean printed "
        "single-staff treble-clef music"
    )
    accepts_pdf = False
    priority = 50

    def __init__(
        self,
        mozart_dir: str | Path | None = None,
        python: str | None = None,
        timeout: int = 1800,
    ) -> None:
        self.mozart_dir = Path(mozart_dir) if mozart_dir else self._find_mozart()
        self.python = python or os.environ.get("TRANSPOSER_MOZART_PYTHON") or sys.executable
        self.timeout = timeout

    @staticmethod
    def _find_mozart() -> Path | None:
        explicit = os.environ.get("TRANSPOSER_MOZART_DIR")
        if explicit:
            return Path(explicit)
        for candidate in _DEFAULT_LOCATIONS:
            if (candidate / "src" / "main.py").is_file():
                return candidate
        return None

    def availability(self) -> Availability:
        if self.mozart_dir is None:
            return Availability(
                False,
                "no Mozart checkout found. Run scripts/fetch_mozart.sh, or set "
                "TRANSPOSER_MOZART_DIR to a clone of github.com/aashrafh/Mozart.",
            )
        main = self.mozart_dir / "src" / "main.py"
        if not main.is_file():
            return Availability(False, f"{main} does not exist")

        model = self.mozart_dir / "src" / "trained_models" / "nn_trained_model_hog.sav"
        if not model.is_file():
            return Availability(
                False,
                f"Mozart's trained model is missing at {model}. Clone the repo "
                "with its trained_models/ directory intact.",
            )

        missing = [name for name in _REQUIRED_MODULES if not _importable(name)]
        if missing:
            return Availability(
                False,
                "missing Python dependencies: "
                + ", ".join(missing)
                + " (install with: pip install 'transposer[mozart]')",
            )
        return Availability(True)

    def recognize(self, source: IngestedInput, workdir: Path) -> OmrResult:
        self.require_available()
        assert self.mozart_dir is not None

        workdir = Path(workdir)
        in_dir = workdir / "mozart-in"
        out_dir = workdir / "mozart-out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        pages = source.rasterize()
        for page in pages:
            (in_dir / page.name).write_bytes(page.read_bytes())

        env = os.environ.copy()
        # Mozart imports matplotlib at module level; without a headless backend
        # it tries to open a window and dies on a server.
        env["MPLBACKEND"] = "Agg"

        source_dir = (self.mozart_dir / "src").resolve()
        completed = self._run(
            [
                self.python,
                str(Path(__file__).with_name("_mozart_runner.py")),
                str(source_dir),
                str(in_dir.resolve()),
                str(out_dir.resolve()),
            ],
            cwd=source_dir,
            env=env,
            timeout=self.timeout,
        )
        log = (completed.stdout or "") + (completed.stderr or "")

        texts = sorted(out_dir.glob("*.txt"))
        if not texts:
            raise OmrFailedError(
                "Mozart produced no output.\n" + "\n".join(log.splitlines()[-25:])
            )

        combined = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in texts)
        score = mozart_text_to_score(combined, title=source.path.stem)

        musicxml = workdir / "mozart.musicxml"
        score.write("musicxml", fp=str(musicxml))

        warnings = [
            "Mozart reads a single treble-clef staff at a time: it does not "
            "recognise key signatures, chord symbols, lyrics, repeats or "
            "multiple voices. Accidentals are read per note.",
        ]
        if len(pages) > 1:
            warnings.append(
                f"{len(pages)} pages were concatenated into one part; Mozart has "
                "no cross-page structure."
            )

        return OmrResult(
            musicxml=musicxml,
            engine=self.name,
            warnings=warnings,
            artifacts={"mozart_text": texts[0]},
            log=log,
        )


def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False
