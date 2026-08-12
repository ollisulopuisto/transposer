"""The end-to-end job: a scan goes in, a transposed PDF comes out.

    ingest → OMR → MusicXML → transpose → engrave → PDF

Each stage is replaceable, and every intermediate artifact is kept on disk.
That matters more than it sounds: OMR is never perfect, so the MusicXML the
pipeline produced is the thing a user opens in MuseScore to fix a wrong note
before re-rendering.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from music21 import converter, stream

from .cleanup import CleanupReport, clean_score, strip_credits
from .ingest import DEFAULT_DPI, ingest
from .keys import Direction
from .omr import OmrResult, select_engine
from .render import select_renderer
from .render.base import RenderResult, resolve_page_size
from .transpose import TranspositionReport, transpose_score


@dataclass
class PipelineOptions:
    """Everything the pipeline can be told to do."""

    target: str = "C"
    engine: str = "auto"
    renderer: str = "auto"
    direction: Direction = "auto"
    octave_shift: int = 0
    instrument: str | None = None
    simplify_enharmonics: bool = True
    transpose_text_chords: bool = True
    plain_clefs: bool = True
    key_changes: str = "auto"
    drop_text: bool = False
    strip_credits: bool = True
    paper: str = "a4"
    landscape: bool = False
    scale: int = 40
    dpi: int = DEFAULT_DPI
    keep_workdir: bool = False
    title_suffix: str | None = None


@dataclass
class PipelineResult:
    """What came out, and everything worth showing the user about how."""

    pdf: Path
    musicxml: Path
    source_musicxml: Path
    engine: str
    renderer: str
    transposition: TranspositionReport
    render: RenderResult
    workdir: Path
    cleanup: CleanupReport = field(default_factory=CleanupReport)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"engine     : {self.engine}",
            f"renderer   : {self.renderer} ({self.render.page_count} page(s))",
            f"transposed : {self.transposition.summary()}",
            f"pdf        : {self.pdf}",
            f"musicxml   : {self.musicxml}",
        ]
        if self.warnings:
            lines.append("warnings   :")
            lines += [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


def run(
    source: Path | str,
    output: Path | str,
    options: PipelineOptions | None = None,
    workdir: Path | str | None = None,
    progress=None,
) -> PipelineResult:
    """Run the whole pipeline.

    ``progress`` is an optional ``callable(stage: str, message: str)`` used by
    the web UI to stream status while a multi-minute OMR pass runs.
    """
    options = options or PipelineOptions()
    source = Path(source)
    output = Path(output)

    # Intermediates are deliberately kept: the recognised MusicXML is what a
    # user opens to fix an OMR mistake. Callers that do not want them around
    # can pass their own workdir, or call cleanup() when finished.
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="transposer-"))
    workdir.mkdir(parents=True, exist_ok=True)

    def report(stage: str, message: str) -> None:
        if progress is not None:
            progress(stage, message)

    report("ingest", f"reading {source.name}")
    ingested = ingest(source, workdir / "input")

    engine = select_engine(ingested, options.engine)
    report("omr", f"recognising with {engine.name}")
    if not engine.accepts_pdf and ingested.kind == "pdf":
        ingested.rasterize(dpi=options.dpi)
    omr: OmrResult = engine.recognize(ingested, workdir)

    report("parse", "parsing MusicXML")
    score = converter.parse(str(omr.musicxml))
    if not isinstance(score, stream.Score):
        score = _as_score(score)

    source_copy = workdir / "recognised.musicxml"
    _preserve(omr.musicxml, source_copy)

    report("cleanup", "repairing common recognition mistakes")
    cleanup = clean_score(
        score,
        plain_clefs=options.plain_clefs,
        key_changes=options.key_changes,
        drop_text=options.drop_text,
    )

    report("transpose", f"transposing to {options.target}")
    transposed, transposition = transpose_score(
        score,
        options.target,
        direction=options.direction,
        octave_shift=options.octave_shift,
        instrument=options.instrument,
        simplify_enharmonics=options.simplify_enharmonics,
        transpose_text_chords=options.transpose_text_chords,
    )

    if options.title_suffix:
        _append_title(transposed, options.title_suffix)

    transposed_xml = workdir / "transposed.musicxml"
    transposed.write("musicxml", fp=str(transposed_xml))

    # Credits live outside music21's object model, so they are pruned on the
    # exported file rather than on the score. OMR fills them with whatever text
    # it found near the page edge, and the renderer draws a proper header from
    # the metadata anyway, so for recognised input they all go.
    if options.strip_credits and engine.name != "passthrough":
        cleanup.credits_removed = strip_credits(transposed_xml)
        if cleanup.credits_removed:
            cleanup.notes.append(
                f"removed {cleanup.credits_removed} page credit(s) left over from "
                "recognition; the title block is drawn from the score metadata"
            )

    report("render", "engraving PDF")
    renderer = select_renderer(options.renderer)
    page_size = resolve_page_size(options.paper, options.landscape)
    rendered = renderer.render(
        transposed_xml,
        output,
        page_size=page_size,
        scale=options.scale,
        workdir=workdir / "render",
    )

    warnings = (
        list(omr.warnings)
        + list(cleanup.notes)
        + list(transposition.warnings)
        + list(rendered.warnings)
    )

    result = PipelineResult(
        pdf=rendered.pdf,
        musicxml=transposed_xml,
        source_musicxml=source_copy,
        engine=engine.name,
        renderer=renderer.name,
        transposition=transposition,
        render=rendered,
        workdir=workdir,
        cleanup=cleanup,
        warnings=warnings,
    )
    report("done", "finished")
    return result


def cleanup(result: PipelineResult) -> None:
    """Delete a run's working directory once its artifacts are no longer needed."""
    shutil.rmtree(result.workdir, ignore_errors=True)


def _as_score(parsed) -> stream.Score:
    """Wrap a bare Part/Opus in a Score so downstream code has one shape."""
    if isinstance(parsed, stream.Opus):
        scores = list(parsed.scores)
        if scores:
            return scores[0]
    wrapper = stream.Score()
    wrapper.append(parsed)
    return wrapper


def _preserve(source: Path, target: Path) -> None:
    """Copy the engine's raw output next to the transposed version.

    Compressed ``.mxl`` files are copied verbatim rather than renamed, so the
    file the user downloads still opens in a notation editor.
    """
    if source.suffix.lower() == ".mxl":
        target = target.with_suffix(".mxl")
    shutil.copy2(source, target)


def _append_title(score: stream.Score, suffix: str) -> None:
    from music21 import metadata as m21_metadata

    if score.metadata is None:
        score.insert(0, m21_metadata.Metadata())
    current = score.metadata.title or ""
    score.metadata.title = f"{current} {suffix}".strip()
