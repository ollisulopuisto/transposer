"""End-to-end tests that do not need an OMR engine installed.

A MusicXML input takes the passthrough engine, which exercises everything after
recognition: parsing, cleanup, transposition, engraving and PDF assembly.
"""

import music21 as m21
import pytest
from music21.musicxml.m21ToXml import GeneralObjectExporter

from transposer.errors import UnsupportedInputError
from transposer.ingest import classify, ingest, merge_pdfs
from transposer.omr import get_engine, select_engine
from transposer.pipeline import PipelineOptions, run


@pytest.fixture
def lead_sheet(tmp_path):
    part = m21.stream.Part()
    part.append(m21.key.Key("E-", "major"))
    part.append(m21.meter.TimeSignature("4/4"))
    for figure, pitch_name in [("E-", "e-4"), ("Cm", "c5"), ("A-", "a-4"), ("B-7", "b-4")]:
        part.append(m21.harmony.ChordSymbol(figure))
        note = m21.note.Note(pitch_name)
        note.quarterLength = 4.0
        part.append(note)
    score = m21.stream.Score()
    score.insert(0, m21.metadata.Metadata())
    score.metadata.title = "Test Sheet"
    score.append(part)

    path = tmp_path / "lead-sheet.musicxml"
    path.write_bytes(GeneralObjectExporter().parse(score))
    return path


def test_classify_recognises_the_three_input_families(tmp_path):
    assert classify(tmp_path / "a.pdf") == "pdf"
    assert classify(tmp_path / "a.PNG") == "image"
    assert classify(tmp_path / "a.musicxml") == "score"
    assert classify(tmp_path / "a.mid") == "score"
    with pytest.raises(UnsupportedInputError):
        classify(tmp_path / "a.docx")


def test_ingest_copies_the_input(tmp_path, lead_sheet):
    ingested = ingest(lead_sheet, tmp_path / "work")
    assert ingested.kind == "score"
    assert ingested.path.parent == tmp_path / "work"
    assert lead_sheet.exists(), "the original must not be moved"


def test_score_input_always_uses_passthrough(tmp_path, lead_sheet):
    ingested = ingest(lead_sheet, tmp_path / "work")
    assert select_engine(ingested, "auto").name == "passthrough"
    # Even an explicit OMR engine request is overridden: there is nothing to see.
    assert select_engine(ingested, "audiveris").name == "passthrough"


def test_pipeline_produces_a_pdf_and_musicxml(tmp_path, lead_sheet):
    output = tmp_path / "out.pdf"
    result = run(
        lead_sheet,
        output,
        PipelineOptions(target="C", renderer="verovio"),
        workdir=tmp_path / "work",
    )

    assert output.is_file()
    assert output.stat().st_size > 1000
    assert output.read_bytes()[:5] == b"%PDF-"
    assert result.musicxml.is_file()
    assert result.engine == "passthrough"
    assert result.renderer == "verovio"
    assert result.render.page_count >= 1


def test_pipeline_transposes_the_music(tmp_path, lead_sheet):
    result = run(
        lead_sheet,
        tmp_path / "out.pdf",
        PipelineOptions(target="C", renderer="verovio"),
        workdir=tmp_path / "work",
    )
    assert result.transposition.source_key.name == "E- major"
    assert result.transposition.written_key.name == "C major"
    assert result.transposition.interval.directedName == "m-3"

    moved = m21.converter.parse(str(result.musicxml))
    roots = [c.root().name for c in moved.recurse().getElementsByClass(m21.harmony.ChordSymbol)]
    assert roots == ["C", "A", "F", "G"]


def test_a4_pages_come_out_a4(tmp_path, lead_sheet):
    pymupdf = pytest.importorskip("pymupdf")
    output = tmp_path / "out.pdf"
    run(
        lead_sheet,
        output,
        PipelineOptions(target="C", renderer="verovio", paper="a4"),
        workdir=tmp_path / "work",
    )
    with pymupdf.open(output) as document:
        rect = document[0].rect
    assert rect.width == pytest.approx(595.28, abs=1.0)
    assert rect.height == pytest.approx(841.89, abs=1.0)


def test_landscape_swaps_the_page(tmp_path, lead_sheet):
    pymupdf = pytest.importorskip("pymupdf")
    output = tmp_path / "out.pdf"
    run(
        lead_sheet,
        output,
        PipelineOptions(target="C", renderer="verovio", paper="a4", landscape=True),
        workdir=tmp_path / "work",
    )
    with pymupdf.open(output) as document:
        rect = document[0].rect
    assert rect.width > rect.height


def test_interval_target_works_without_a_key(tmp_path, lead_sheet):
    result = run(
        lead_sheet,
        tmp_path / "out.pdf",
        PipelineOptions(target="-M3", renderer="verovio"),
        workdir=tmp_path / "work",
    )
    assert result.transposition.interval.directedName == "M-3"


def test_passthrough_keeps_hand_written_credits(tmp_path, lead_sheet):
    """Credit stripping is for OMR debris, not for scores a human authored."""
    result = run(
        lead_sheet,
        tmp_path / "out.pdf",
        PipelineOptions(target="C", renderer="verovio"),
        workdir=tmp_path / "work",
    )
    assert result.cleanup.credits_removed == 0


def test_unknown_engine_is_reported_clearly():
    from transposer.errors import EngineUnavailableError

    with pytest.raises(EngineUnavailableError, match="unknown OMR engine"):
        get_engine("nonesuch")


def test_merge_pdfs_concatenates(tmp_path, lead_sheet):
    pymupdf = pytest.importorskip("pymupdf")
    first = tmp_path / "a.pdf"
    run(lead_sheet, first, PipelineOptions(renderer="verovio"), workdir=tmp_path / "w1")

    merged = merge_pdfs([first, first], tmp_path / "merged.pdf")
    with pymupdf.open(merged) as document:
        assert document.page_count == 2
