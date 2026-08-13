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

from .chordband import MIN_CONFIDENCE, read_and_apply
from .chordocr import merge_chord_symbols
from .cleanup import CleanupReport, clean_score, strip_credits
from .errors import TransposerError
from .ingest import DEFAULT_DPI, images_to_pdf, ingest, native_resolution
from .keys import Direction
from .preprocess import TARGET_INTERLINE, PreprocessReport, enhance_pages
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
    repair_chords: bool = True
    chord_pass: bool = False
    chord_ocr: bool = True
    chord_ocr_confidence: float = MIN_CONFIDENCE
    paper: str = "a4"
    landscape: bool = False
    scale: int = 40
    dpi: int = DEFAULT_DPI
    keep_workdir: bool = False
    title_suffix: str | None = None
    # Image enhancement, applied before recognition.
    preprocess: bool = True
    target_interline: int = TARGET_INTERLINE
    deskew: bool = True
    sharpen: bool = True
    binarize: bool = False


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
    preprocessing: list[PreprocessReport] = field(default_factory=list)
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
    engine, omr, score, preprocessing, preprocess_notes, ingested = _recognise(
        source, options, workdir, binarize=options.binarize, report=report
    )

    source_copy = workdir / "recognised.musicxml"
    _preserve(omr.musicxml, source_copy)

    report("cleanup", "repairing common recognition mistakes")
    cleanup = clean_score(
        score,
        plain_clefs=options.plain_clefs,
        key_changes=options.key_changes,
        drop_text=options.drop_text,
        repair_chords=options.repair_chords,
    )

    # A second recognition pass tuned for text. Binarising sharpens chord
    # symbols and lyrics at the cost of noteheads, so the notes come from the
    # first pass and only the chord symbols are taken from this one.
    if options.chord_pass and engine.name != "passthrough" and not options.binarize:
        report("chord-pass", "second recognition pass for chord symbols")
        try:
            _, _, text_score, _, _, _ = _recognise(
                source,
                options,
                workdir / "chord-pass",
                binarize=True,
                report=lambda stage, message: None,
            )
            clean_score(
                text_score,
                plain_clefs=False,
                key_changes="keep",
                drop_text=False,
                fix_metadata=False,
                repair_chords=options.repair_chords,
            )
            merged, merge_notes = merge_chord_symbols(score, text_score)
            cleanup.chords_promoted += merged
            cleanup.notes.extend(merge_notes)
        except TransposerError as exc:
            cleanup.notes.append(f"the chord-symbol pass did not run: {exc}")

    # The OMR engines drive Tesseract's legacy classifier, which on a chord
    # chart does not so much misread the symbols as never propose them. Reading
    # the band above each staff directly, with the LSTM engine, finds the ones
    # that never reached the MusicXML at all -- and because it works off the
    # page rather than the recognised score, it is independent of what the
    # engine got wrong.
    if options.chord_ocr and engine.name != "passthrough":
        report("chord-ocr", "reading the chord band with Tesseract's LSTM engine")
        try:
            pages = list(ingested.pages) or ingested.rasterize(dpi=options.dpi)
        except TransposerError as exc:
            pages = []
            cleanup.notes.append(f"the chord-band pass did not run: {exc}")
        if pages:
            added, band_notes = read_and_apply(
                score,
                pages,
                workdir=workdir / "chord-band",
                min_confidence=options.chord_ocr_confidence,
            )
            cleanup.chords_promoted += added
            cleanup.notes.extend(band_notes)

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
        list(preprocess_notes)
        + list(omr.warnings)
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
        preprocessing=preprocessing,
        warnings=warnings,
    )
    report("done", "finished")
    return result


def _recognise(source: Path, options: PipelineOptions, workdir: Path, binarize: bool, report):
    """Ingest, enhance and recognise one copy of the input."""
    workdir.mkdir(parents=True, exist_ok=True)
    ingested = ingest(source, workdir / "input")
    engine = select_engine(ingested, options.engine)

    preprocessing: list[PreprocessReport] = []
    notes: list[str] = []
    if options.preprocess and not ingested.is_score:
        report("preprocess", "measuring and enhancing the page")
        preprocessing, notes = _preprocess(ingested, options, workdir, binarize=binarize)

    report("omr", f"recognising with {engine.name}")
    if not engine.accepts_pdf and ingested.kind == "pdf":
        ingested.rasterize(dpi=options.dpi)
    omr: OmrResult = engine.recognize(ingested, workdir)

    report("parse", "parsing MusicXML")
    score = converter.parse(str(omr.musicxml))
    if not isinstance(score, stream.Score):
        score = _as_score(score)

    return engine, omr, score, preprocessing, notes, ingested


def _preprocess(
    ingested, options: PipelineOptions, workdir: Path, binarize: bool | None = None
) -> tuple[list[PreprocessReport], list[str]]:
    """Rasterise and enhance the input before recognition.

    Two decisions happen here. First the rasterising resolution: a PDF that is
    a wrapper around a scan holds a fixed number of pixels, and rendering it at
    a higher dpi than that only interpolates -- badly, and then again when the
    enhancement pass scales. So a scan is rendered at its own resolution and
    resampled exactly once, by the step that knows what it is aiming for.

    Second, the enhanced pages are bundled back into a PDF for engines that
    read PDFs, so a multi-page score stays one document.
    """
    notes: list[str] = []

    dpi = options.dpi
    if ingested.kind == "pdf":
        native = native_resolution(ingested.path)
        if native and native < options.dpi:
            dpi = max(72, round(native))
            notes.append(
                f"the PDF holds a scan at about {dpi} dpi, so it was read at that "
                f"resolution rather than {options.dpi}; asking for more would only "
                "interpolate"
            )

    pages = ingested.rasterize(dpi=dpi)
    enhanced, reports = enhance_pages(
        pages,
        workdir / "enhanced",
        target_interline=options.target_interline,
        deskew=options.deskew,
        sharpen=options.sharpen,
        binarize=options.binarize if binarize is None else binarize,
    )
    ingested.replace_pages(enhanced)

    for report in reports:
        notes.extend(report.notes)
    if reports:
        notes.append("enhanced page 1: " + reports[0].summary())

    if ingested.kind == "pdf":
        bundled = images_to_pdf(enhanced, workdir / "enhanced" / "enhanced.pdf")
        ingested.path = bundled

    return reports, notes


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
