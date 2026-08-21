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


def test_music_font_accidentals_become_plain_unicode():
    """CairoSVG has no music font, so "F#m7" would otherwise render as "F□m7"."""
    from transposer.render.verovio_renderer import substitute_text_glyphs

    svg = (
        '<tspan font-size="405px">F</tspan>'
        '<tspan font-family="Leipzig" font-size="720px"></tspan>'
        '<tspan font-size="405px">m7</tspan>'
    )
    result = substitute_text_glyphs(svg)
    assert "♯" in result
    assert "Leipzig" not in result
    assert ">F<" in result and ">m7<" in result


def test_flats_and_naturals_are_substituted_too():
    from transposer.render.verovio_renderer import substitute_text_glyphs

    assert "♭" in substitute_text_glyphs(
        '<tspan font-family="Leipzig" font-size="720px"></tspan>'
    )
    assert "♮" in substitute_text_glyphs(
        '<tspan font-family="Leipzig" font-size="720px"></tspan>'
    )


def test_unknown_music_glyphs_are_left_alone():
    """Better a box than a silently wrong symbol."""
    from transposer.render.verovio_renderer import substitute_text_glyphs

    svg = '<tspan font-family="Leipzig" font-size="720px"></tspan>'
    assert substitute_text_glyphs(svg) == svg


def test_ordinary_text_runs_are_untouched():
    from transposer.render.verovio_renderer import substitute_text_glyphs

    svg = '<tspan font-size="405px">Dm7</tspan>'
    assert substitute_text_glyphs(svg) == svg


def test_a_rendered_chord_sheet_has_no_missing_glyph_boxes(tmp_path):
    """End to end: a score with an F sharp chord must render its accidental."""
    part = m21.stream.Part()
    part.append(m21.key.Key("C", "major"))
    part.append(m21.meter.TimeSignature("4/4"))
    part.append(m21.harmony.ChordSymbol("F#m7"))
    note = m21.note.Note("f#4")
    note.quarterLength = 4.0
    part.append(note)
    score = m21.stream.Score()
    score.append(part)

    source = tmp_path / "sharp.musicxml"
    source.write_bytes(GeneralObjectExporter().parse(score))

    result = run(
        source,
        tmp_path / "out.pdf",
        PipelineOptions(target="0", renderer="verovio"),
        workdir=tmp_path / "work",
    )
    svg = result.render.svg_pages[0].read_text(encoding="utf-8")
    assert "♯" in svg
    assert 'font-family="Leipzig"' not in svg


# -- limits on what an upload may ask the machine to do -------------------


def test_a_pdf_with_too_many_pages_is_refused(tmp_path):
    """Every page is rasterised and then recognised. A thousand-page upload is
    not a scan of a song, it is a request to occupy the machine."""
    import pymupdf

    from transposer.errors import UnsupportedInputError
    from transposer.ingest import ingest

    big = tmp_path / "big.pdf"
    with pymupdf.open() as document:
        for _ in range(12):
            document.new_page(width=200, height=200)
        document.save(big)

    ingested = ingest(big, tmp_path / "work")
    with pytest.raises(UnsupportedInputError, match="pages"):
        ingested.rasterize(dpi=72, max_pages=10)


def test_a_pdf_within_the_page_limit_is_fine(tmp_path):
    import pymupdf

    from transposer.ingest import ingest

    small = tmp_path / "small.pdf"
    with pymupdf.open() as document:
        document.new_page(width=200, height=200)
        document.save(small)

    ingested = ingest(small, tmp_path / "work")
    assert len(ingested.rasterize(dpi=72, max_pages=10)) == 1


def test_an_image_with_an_absurd_pixel_count_is_refused(tmp_path):
    """A few kilobytes of PNG can decompress to gigabytes of pixels."""
    from PIL import Image

    from transposer.errors import UnsupportedInputError
    from transposer.ingest import guard_image_size

    path = tmp_path / "bomb.png"
    Image.new("L", (40, 40), 255).save(path)

    with pytest.raises(UnsupportedInputError, match="pixels"):
        guard_image_size(path, max_pixels=100)


def test_an_ordinary_image_passes_the_guard(tmp_path):
    from PIL import Image

    from transposer.ingest import guard_image_size

    path = tmp_path / "fine.png"
    Image.new("L", (40, 40), 255).save(path)
    guard_image_size(path, max_pixels=100_000)
