"""Tests for the chord-band OCR pass.

The fixtures draw synthetic pages -- staves at a known interline, barlines at
known columns, chord text at known positions -- so every assertion is against a
number the test chose rather than one that was eyeballed off a scan.

The tests that need the ``tesseract`` binary are skipped when it is missing;
everything up to and including the geometry, the command line and the mapping
from pixels to measures runs without it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from transposer import chordband
from transposer.chordocr import chord_symbol

HAVE_TESSERACT = shutil.which("tesseract") is not None
needs_tesseract = pytest.mark.skipif(
    not HAVE_TESSERACT, reason="the tesseract binary is not installed"
)

#: Fonts that carry the ASCII a chord symbol needs. The first one present wins.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def load_font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return None


def draw_page(
    interline: int = 20,
    staves: int = 2,
    measures_per_staff: int = 4,
    width: int = 1400,
    margin: int = 80,
    chords: dict[tuple[int, int], str] | None = None,
    thickness: int = 2,
) -> Image.Image:
    """A synthetic lead sheet: staves, barlines, and chord text above them.

    ``chords`` maps ``(staff_index, measure_index)`` to the text drawn at the
    start of that measure, in the band above the staff.
    """
    staff_height = interline * 4
    gap = interline * 7
    height = margin * 2 + staves * (staff_height + gap)

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = load_font(round(interline * 1.6))

    left, right = margin, width - margin
    measure_width = (right - left) / measures_per_staff

    y = margin + gap
    for staff_index in range(staves):
        for line in range(5):
            top = y + line * interline
            draw.rectangle([left, top, right, top + thickness - 1], fill=0)

        bottom = y + staff_height + thickness - 1
        for measure in range(measures_per_staff + 1):
            x = round(left + measure * measure_width)
            draw.rectangle([x, y, x + thickness - 1, bottom], fill=0)

        if chords and font is not None:
            for (index, measure), text in chords.items():
                if index != staff_index:
                    continue
                x = round(left + measure * measure_width) + interline // 2
                draw.text((x, y - round(interline * 2.4)), text, fill=0, font=font)

        y += staff_height + gap

    return image


def as_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"))


# -- staff and band geometry ----------------------------------------------


@pytest.mark.parametrize("interline", [12, 20, 28])
def test_finds_every_staff(interline):
    page = as_array(draw_page(interline=interline, staves=3))
    found = chordband.find_staves(page)
    assert len(found) == 3
    for staff in found:
        assert staff.bottom - staff.top == pytest.approx(interline * 4, abs=3)
        assert staff.interline == pytest.approx(interline, abs=1.5)


def test_staff_extent_matches_the_drawn_lines():
    page = as_array(draw_page(interline=20, staves=1, width=1400, margin=80))
    (staff,) = chordband.find_staves(page)
    assert staff.left == pytest.approx(80, abs=4)
    assert staff.right == pytest.approx(1400 - 80, abs=4)


def test_blank_page_has_no_staves():
    assert chordband.find_staves(np.full((400, 600), 255, dtype=np.uint8)) == []


def test_band_sits_above_the_staff_and_clear_of_the_one_before():
    page = as_array(draw_page(interline=20, staves=2))
    staves = chordband.find_staves(page)
    boxes = [chordband.band_box(staves, index) for index in range(len(staves))]

    for staff, (_, top, _, bottom) in zip(staves, boxes, strict=True):
        assert bottom <= staff.top
        assert top < bottom

    # The second band must not reach back into the first staff.
    assert boxes[1][1] >= staves[0].bottom


def test_band_is_clipped_at_the_top_of_the_page():
    page = as_array(draw_page(interline=20, staves=1, margin=8))
    staves = chordband.find_staves(page)
    left, top, right, _bottom = chordband.band_box(staves, 0)
    assert top >= 0
    assert left >= 0
    assert right <= page.shape[1]


# -- barlines --------------------------------------------------------------


def test_finds_the_barlines_that_were_drawn():
    page = as_array(draw_page(interline=20, staves=1, measures_per_staff=4))
    (staff,) = chordband.find_staves(page)
    barlines = chordband.find_barlines(page, staff)

    expected = [80 + round(index * (1400 - 160) / 4) for index in range(5)]
    assert len(barlines) == len(expected)
    for found, want in zip(barlines, expected, strict=True):
        assert found == pytest.approx(want, abs=4)


def test_bounds_follow_the_engraved_measure_widths():
    """MusicXML records each measure's engraved width, and engravers do not
    space bars equally: the first bar of a system carries the clef, the key
    signature and often a repeat, so it can be half again as wide as its
    neighbours. Dividing the system evenly puts a chord written over bar 1 into
    bar 2.
    """
    bounds = chordband.measure_bounds_from_widths([305, 287, 166, 102, 190], 137, 2273)
    assert bounds[0] == 137
    assert bounds[-1] == 2273
    # Bar 1 is 305/1050 of the system, not a fifth of it.
    assert bounds[1] == pytest.approx(137 + (2273 - 137) * 305 / 1050, abs=1)


def test_widths_that_are_missing_or_zero_are_refused():
    assert chordband.measure_bounds_from_widths([], 0, 100) is None
    assert chordband.measure_bounds_from_widths([100, None, 50], 0, 100) is None
    assert chordband.measure_bounds_from_widths([0, 0], 0, 100) is None


def test_engraved_widths_beat_barlines_and_equal_division():
    """The whole point: a system whose barlines are over-detected still places
    chords correctly, because the score knows the real proportions."""
    from music21 import layout, meter, note, stream

    part = stream.Part()
    for number, width in enumerate([300, 100, 100, 100], start=1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
            measure.insert(0, layout.SystemLayout(isNew=True))
        measure.layoutWidth = width
        measure.insert(0, note.Rest(quarterLength=4.0))
        part.append(measure)
    score = stream.Score()
    score.insert(0, part)

    # Barlines here are nonsense -- stems and a repeat sign read as barlines.
    page = [
        chordband.StaffChords(
            staff=make_staff(0, left=0, right=600),
            barlines=[0, 40, 55, 70, 210, 300, 380, 450, 600],
            chords=[
                # x=250 is inside bar 1, which is half the system wide.
                chordband.BandChord("C", "C", staff_index=0, x=250, confidence=90),
            ],
        ),
    ]

    added, notes = chordband.apply_chords(score, page)
    assert added == 1, notes

    from music21 import harmony

    placed = {
        m.number
        for m in score.parts[0].getElementsByClass("Measure")
        if list(m.getElementsByClass(harmony.ChordSymbol))
    }
    assert placed == {1}, "equal division would have put this in bar 2"


def test_measure_bounds_fall_back_to_equal_division():
    """When barline detection disagrees with the score, geometry still works."""
    bounds = chordband.measure_bounds([100, 500], left=100, right=500, count=4)
    assert bounds == [100.0, 200.0, 300.0, 400.0, 500.0]


def test_measure_bounds_use_detected_barlines_when_the_count_agrees():
    bounds = chordband.measure_bounds([100, 180, 300, 500], left=100, right=500, count=3)
    assert bounds == [100.0, 180.0, 300.0, 500.0]


# -- reading the band ------------------------------------------------------


def test_the_command_line_asks_for_the_lstm_engine():
    command = chordband.tesseract_command(Path("band.png"))
    assert "--oem" in command
    assert command[command.index("--oem") + 1] == "1"


def test_tsv_is_requested_as_a_parameter_not_a_config_file():
    """``tesseract ... tsv`` needs a config file that lives beside the
    traineddata. Point TESSDATA_PREFIX at a bare directory -- which is exactly
    what a container does when it downloads one eng.traineddata for Audiveris --
    and tesseract cannot find it, prints "Can't open tsv" to stderr, exits 0,
    and emits plain text. Every word is then silently lost in parsing. Setting
    the underlying parameter does not depend on the tessdata layout at all.
    """
    command = chordband.tesseract_command(Path("band.png"))
    assert "-c" in command
    assert any(part == "tessedit_create_tsv=1" for part in command)
    assert "tsv" not in command  # not as a bare config-file argument


def test_the_command_line_can_drop_the_whitelist():
    command = chordband.tesseract_command(Path("band.png"), whitelist=None)
    assert not any("whitelist" in part for part in command)


def test_no_character_whitelist_by_default():
    """A whitelist forbids exactly the characters the repair pass decodes.

    Restricting the alphabet to what a chord symbol is *spelled* with removes
    the evidence: a flat comes back as P, v or Y, a seven as T or /, and the
    confusion table exists to map those back. On a real engraved chart the
    whitelist cost every flat chord on the page.
    """
    assert chordband.CHORD_WHITELIST is None
    command = chordband.tesseract_command(Path("band.png"))
    assert not any("whitelist" in part for part in command)


def test_the_band_is_tall_enough_for_where_chords_are_engraved():
    """Chord symbols sit three to four interlines above the top staff line, not
    one. A band that stops short of them reads note stems and beams instead."""
    assert chordband.BAND_HEIGHT >= 4.5

    staff = chordband.Staff(top=440, bottom=520, left=100, right=2000, interline=20.0)
    _, top, _, bottom = chordband.band_box([staff], 0)
    assert bottom <= 440
    assert 440 - top >= 90  # reaches the chord row, ~4.5 interlines up


def test_plain_text_output_is_not_silently_read_as_no_words():
    """The failure above produced real output and zero words, with no error.

    A parser that returns nothing when handed something it does not understand
    is indistinguishable from a page with no chords on it, which is what made
    this cost an afternoon.
    """
    plain = "Cm\n\nGm\n\nEb7\n\nAb\n"
    with pytest.raises(chordband.TesseractOutputError):
        chordband.parse_tsv(plain)


def test_empty_output_is_simply_no_words():
    assert chordband.parse_tsv("") == []


def test_a_tsv_header_with_no_rows_is_no_words():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext"
    )
    assert chordband.parse_tsv(header + "\n") == []


def test_tsv_rows_are_parsed():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext"
    )
    row = "5\t1\t1\t1\t1\t1\t100\t10\t40\t30\t92.5\tAm7"
    (word,) = chordband.parse_tsv(f"{header}\n{row}\n")
    assert word.text == "Am7"
    assert word.x == 120  # left + width // 2
    assert word.confidence == pytest.approx(92.5)


def test_words_that_are_not_chords_are_discarded():
    words = [
        chordband.BandWord("Refrain", x=10, confidence=90),
        chordband.BandWord("Fm?", x=40, confidence=80),
        chordband.BandWord("Bb7", x=90, confidence=85),
    ]
    chords = chordband.words_to_chords(words, staff_index=0)
    assert [chord.figure for chord in chords] == ["Fm7", "Bb7"]
    assert [chord.x for chord in chords] == [40, 90]


def test_low_confidence_words_are_discarded():
    words = [
        chordband.BandWord("C", x=10, confidence=12),
        chordband.BandWord("G7", x=40, confidence=88),
    ]
    chords = chordband.words_to_chords(words, staff_index=0, min_confidence=30)
    assert [chord.figure for chord in chords] == ["G7"]


def test_readings_from_two_modes_are_merged_by_position():
    """Neither segmentation mode wins everywhere, so both are kept."""
    passes = [
        [
            chordband.BandWord("Gm", x=100, confidence=65),
            chordband.BandWord("A7", x=300, confidence=69),
        ],
        [
            chordband.BandWord("Gm7", x=102, confidence=78),
            chordband.BandWord("Af", x=301, confidence=0),
        ],
    ]
    merged = chordband.merge_readings(passes, window=26)
    assert [word.text for word in merged] == ["Gm7", "A7"]


def test_a_readable_chord_beats_a_more_confident_smudge():
    passes = [
        [chordband.BandWord("Bb7", x=50, confidence=40)],
        [chordband.BandWord("Refrain", x=52, confidence=95)],
    ]
    merged = chordband.merge_readings(passes, window=26)
    assert [word.text for word in merged] == ["Bb7"]


def test_words_further_apart_than_the_window_are_both_kept():
    passes = [
        [chordband.BandWord("C", x=50, confidence=90)],
        [chordband.BandWord("F", x=400, confidence=90)],
    ]
    merged = chordband.merge_readings(passes, window=26)
    assert [word.text for word in merged] == ["C", "F"]


@needs_tesseract
def test_reads_chord_symbols_off_a_synthetic_page(tmp_path):
    if load_font(20) is None:
        pytest.skip("no scalable font available to draw the fixture with")

    wanted = {(0, 0): "C", (0, 1): "Am7", (0, 2): "F", (0, 3): "G7"}
    page_path = tmp_path / "page.png"
    draw_page(interline=22, staves=1, measures_per_staff=4, chords=wanted).save(page_path)

    chords = chordband.read_page(page_path)
    figures = [chord.figure for chord in chords]

    assert "Am7" in figures, figures
    assert "G7" in figures, figures
    # And they came back in the order they are written on the page.
    assert [chord.x for chord in chords] == sorted(chord.x for chord in chords)


@needs_tesseract
def test_a_page_with_no_text_reads_as_no_chords(tmp_path):
    page_path = tmp_path / "page.png"
    draw_page(interline=20, staves=2, chords=None).save(page_path)
    assert chordband.read_page(page_path) == []


# -- putting the chords into the score -------------------------------------


def build_score(measures: int = 8, per_system: int = 4):
    """A score of empty 4/4 bars, broken into systems like an engraver would."""
    from music21 import layout, meter, note, stream

    part = stream.Part()
    for number in range(1, measures + 1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
        if (number - 1) % per_system == 0:
            measure.insert(0, layout.SystemLayout(isNew=True))
        measure.insert(0, note.Rest(quarterLength=4.0))
        part.append(measure)

    score = stream.Score()
    score.insert(0, part)
    return score


def test_systems_are_read_off_the_score():
    systems = chordband.score_systems(build_score(measures=8, per_system=4))
    assert [[m.number for m in system] for system in systems] == [
        [1, 2, 3, 4],
        [5, 6, 7, 8],
    ]


def test_a_score_without_system_breaks_is_one_system():
    from music21 import note, stream

    part = stream.Part()
    for number in range(1, 4):
        measure = stream.Measure(number=number)
        measure.insert(0, note.Rest(quarterLength=4.0))
        part.append(measure)
    score = stream.Score()
    score.insert(0, part)

    systems = chordband.score_systems(score)
    assert len(systems) == 1
    assert [m.number for m in systems[0]] == [1, 2, 3]


def make_staff(index: int, left: int = 100, right: int = 500) -> chordband.Staff:
    top = 100 + index * 200
    return chordband.Staff(
        top=top, bottom=top + 80, left=left, right=right, interline=20.0
    )


def test_chords_land_in_the_measure_they_were_written_over():
    from music21 import harmony

    score = build_score(measures=8, per_system=4)
    # Two staves on the page, four measures each, 100px per measure.
    page = [
        chordband.StaffChords(
            staff=make_staff(0),
            barlines=[100, 200, 300, 400, 500],
            chords=[
                chordband.BandChord("C", "C", staff_index=0, x=105, confidence=90),
                chordband.BandChord("Am7", "Am7", staff_index=0, x=305, confidence=90),
            ],
        ),
        chordband.StaffChords(
            staff=make_staff(1),
            barlines=[100, 200, 300, 400, 500],
            chords=[
                chordband.BandChord("F", "F", staff_index=1, x=205, confidence=90),
            ],
        ),
    ]

    added, notes = chordband.apply_chords(score, page)
    assert added == 3, notes

    found = {}
    for measure in score.parts[0].getElementsByClass("Measure"):
        for symbol in measure.getElementsByClass(harmony.ChordSymbol):
            found[measure.number] = symbol.figure
    assert found == {1: "C", 3: "Am7", 6: "F"}


def test_the_beat_within_the_measure_follows_the_x_position():
    from music21 import harmony

    score = build_score(measures=4, per_system=4)
    page = [
        chordband.StaffChords(
            staff=make_staff(0),
            barlines=[100, 200, 300, 400, 500],
            chords=[
                # Halfway through bar 1 of a 4/4 bar is beat 3, offset 2.0.
                chordband.BandChord("G", "G", staff_index=0, x=150, confidence=90),
            ],
        ),
    ]

    added, _ = chordband.apply_chords(score, page)
    assert added == 1

    measure = score.parts[0].getElementsByClass("Measure")[0]
    (symbol,) = measure.getElementsByClass(harmony.ChordSymbol)
    assert float(symbol.offset) == pytest.approx(2.0)


def test_a_chord_already_in_the_measure_is_not_duplicated():
    from music21 import harmony

    score = build_score(measures=4, per_system=4)
    first = score.parts[0].getElementsByClass("Measure")[0]
    first.insert(0.0, harmony.ChordSymbol("C"))

    page = [
        chordband.StaffChords(
            staff=make_staff(0),
            barlines=[100, 200, 300, 400, 500],
            chords=[chordband.BandChord("C", "C", staff_index=0, x=110, confidence=90)],
        ),
    ]

    added, _ = chordband.apply_chords(score, page)
    assert added == 0
    assert len(first.getElementsByClass(harmony.ChordSymbol)) == 1


def test_a_different_chord_on_an_occupied_beat_is_left_out():
    """Two chord symbols on one beat is a recognition artefact, not a chart."""
    from music21 import harmony

    score = build_score(measures=4, per_system=4)
    first = score.parts[0].getElementsByClass("Measure")[0]
    first.insert(0.0, harmony.ChordSymbol("C"))

    page = [
        chordband.StaffChords(
            staff=make_staff(0),
            barlines=[100, 200, 300, 400, 500],
            chords=[chordband.BandChord("F", "F", staff_index=0, x=110, confidence=90)],
        ),
    ]

    added, _ = chordband.apply_chords(score, page)
    assert added == 0


def test_a_changed_spelling_is_reported():
    """The band pass repairs mangled text, and repair can be wrong.

    "Br7" is either Bb7 with a mangled flat or Bm7 with a mangled m, and the
    grammar picks the minor. A user who can see that it happened can fix it in
    the MusicXML; one who cannot gets a wrong chord and no reason to look.
    """
    score = build_score(measures=4, per_system=4)
    page = [
        chordband.StaffChords(
            staff=make_staff(0),
            barlines=[100, 200, 300, 400, 500],
            chords=[
                chordband.BandChord("Bm7", "Br7", staff_index=0, x=110, confidence=90),
                chordband.BandChord("C", "C", staff_index=0, x=310, confidence=90),
            ],
        ),
    ]

    added, notes = chordband.apply_chords(score, page)
    assert added == 2
    assert any("'Br7'" in note and "'Bm7'" in note for note in notes), notes
    # The one that did not change is not worth a line.
    assert not any("'C'" in note for note in notes), notes


def test_a_page_that_disagrees_about_the_systems_is_refused():
    score = build_score(measures=8, per_system=4)  # two systems
    page = [
        chordband.StaffChords(
            staff=make_staff(index),
            barlines=[100, 200, 300, 400, 500],
            chords=[
                chordband.BandChord("C", "C", staff_index=index, x=110, confidence=90)
            ],
        )
        for index in range(3)  # three staves on the page
    ]

    added, notes = chordband.apply_chords(score, page)
    assert added == 0
    assert any("system" in note for note in notes)


@needs_tesseract
def test_a_synthetic_chart_reads_end_to_end(tmp_path):
    """Page image in, chord symbols in the right bars of the score out."""
    from music21 import harmony

    if load_font(20) is None:
        pytest.skip("no scalable font available to draw the fixture with")

    # Deliberately the awkward ones: a flat root, a sharp root, a two-character
    # symbol, and the qualities that come back from the LSTM engine with the
    # seven rendered as a slash.
    written = {
        (0, 0): "Bb7", (0, 1): "F#m7", (0, 2): "Cmaj7", (0, 3): "Dm7b5",
        (1, 0): "Eb", (1, 1): "Gm7", (1, 2): "A7", (1, 3): "Csus4",
    }
    page_path = tmp_path / "chart.png"
    draw_page(
        interline=22, staves=2, measures_per_staff=4, chords=written
    ).save(page_path)

    score = build_score(measures=8, per_system=4)
    added, notes = chordband.read_and_apply(
        score, [page_path], workdir=tmp_path / "bands"
    )

    found = {}
    for measure in score.parts[0].getElementsByClass("Measure"):
        for symbol in measure.getElementsByClass(harmony.ChordSymbol):
            found[measure.number] = symbol.figure

    # Bar numbers: staff 0 is bars 1-4, staff 1 is bars 5-8. Expectations are
    # built through the same helper as the code under test, so the comparison is
    # not about how music21 chooses to spell a flat.
    chart = dict(enumerate([written[key] for key in sorted(written)], start=1))
    expected = {number: chord_symbol(figure).figure for number, figure in chart.items()}

    # Exactly which symbols the recogniser gets is a property of Tesseract and
    # of whichever font the fixture found on this machine -- macOS draws it in
    # Arial and Linux CI in DejaVu Sans, and they do not read identically. What
    # must hold on any of them is that most of a clean chart is read, and that
    # nothing read lands in the wrong bar.
    assert added >= 6, notes
    assert found, notes
    for number, figure in found.items():
        assert figure == expected[number], (
            f"bar {number}: read {figure!r}, chart says {expected[number]!r}"
        )


def test_a_grand_staff_maps_two_staves_to_one_system():
    """A piano system is two staves; the chord band belongs to the upper one."""
    from music21 import harmony

    score = build_score(measures=8, per_system=4)  # two systems
    page = []
    for index in range(4):  # four staves = two systems of two
        page.append(
            chordband.StaffChords(
                staff=make_staff(index),
                barlines=[100, 200, 300, 400, 500],
                chords=[
                    chordband.BandChord(
                        "D", "D", staff_index=index, x=110, confidence=90
                    )
                ],
            )
        )

    added, notes = chordband.apply_chords(score, page)
    # Only the top staff of each system carries chords, so two are added.
    assert added == 2, notes
    numbers = [
        measure.number
        for measure in score.parts[0].getElementsByClass("Measure")
        if list(measure.getElementsByClass(harmony.ChordSymbol))
    ]
    assert numbers == [1, 5]
