"""The "no recognition needed" backend.

When the user already has MusicXML, MIDI, ABC or kern, there is nothing to
recognise; the file only needs normalising to MusicXML so the rest of the
pipeline sees one format. Keeping this behind the same interface as the real
engines means the transposition and rendering code has exactly one input shape.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import OmrFailedError
from ..ingest import IngestedInput
from .base import Availability, OmrEngine, OmrResult


class PassthroughEngine(OmrEngine):
    name = "passthrough"
    description = "No OMR -- read MusicXML/MXL/MIDI/ABC/kern input directly"
    accepts_pdf = False
    priority = 1

    def availability(self) -> Availability:
        return Availability(True)

    def recognize(self, source: IngestedInput, workdir: Path) -> OmrResult:
        if not source.is_score:
            raise OmrFailedError(
                f"{source.path.name} is a {source.kind}, which needs an OMR "
                "engine; the passthrough engine only reads score files."
            )

        suffix = source.path.suffix.lower()
        if suffix in {".xml", ".musicxml", ".mxl"}:
            return OmrResult(musicxml=source.path, engine=self.name)

        # MIDI, ABC and kern go through music21 to become MusicXML.
        from music21 import converter

        score = converter.parse(str(source.path))
        target = Path(workdir) / f"{source.path.stem}.musicxml"
        score.write("musicxml", fp=str(target))

        warnings = []
        if suffix in {".mid", ".midi"}:
            warnings.append(
                "MIDI carries no spelling information, so accidentals and the "
                "key signature were inferred."
            )
        return OmrResult(musicxml=target, engine=self.name, warnings=warnings)
