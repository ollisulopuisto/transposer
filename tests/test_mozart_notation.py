"""Tests for the Mozart bracket-notation parser.

The samples here are in the format Mozart's own README documents and its
``main.py`` emits, so they double as a record of what we believe that format is.
"""

import music21 as m21
import pytest

from transposer.errors import OmrFailedError
from transposer.omr.mozart_notation import (
    mozart_text_to_score,
    parse_mozart_text,
)


def test_a_single_staff_with_a_meter():
    staves = parse_mozart_text('[ \\meter<"4/4"> a1/4 b1/4 c2/4 d2/4 ]')
    assert len(staves) == 1
    assert staves[0].meter == (4, 4)
    assert [n.nameWithOctave for n in staves[0].events] == ["A4", "B4", "C5", "D5"]


def test_several_staves_in_one_block():
    text = '{\n[ \\meter<"4/4"> a1/4 ]\n[ b1/8 c2/8 ]\n}'
    staves = parse_mozart_text(text)
    assert len(staves) == 2
    assert staves[1].meter is None
    assert len(staves[1].events) == 2


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("a1/4", "A4"),
        ("a#1/4", "A#4"),
        ("a##1/4", "A##4"),
        ("a&1/4", "A-4"),
        ("a&&1/4", "A--4"),
        ("g2/16", "G5"),
    ],
)
def test_accidentals_use_mozart_spelling(token, expected):
    staff = parse_mozart_text(f"[ {token} ]")[0]
    assert staff.events[0].nameWithOctave == expected


@pytest.mark.parametrize(
    ("token", "quarters"),
    [
        ("a1/1", 4.0),
        ("a1/2", 2.0),
        ("a1/4", 1.0),
        ("a1/8", 0.5),
        ("a1/16", 0.25),
        ("a1/32", 0.125),
        ("a1/4.", 1.5),
        ("a1/2.", 3.0),
    ],
)
def test_durations_and_dots(token, quarters):
    staff = parse_mozart_text(f"[ {token} ]")[0]
    assert staff.events[0].duration.quarterLength == quarters


def test_chords_are_grouped():
    staff = parse_mozart_text("[ {a1/4,c2/4,e2/4} b1/4 ]")[0]
    assert isinstance(staff.events[0], m21.chord.Chord)
    assert [p.nameWithOctave for p in staff.events[0].pitches] == ["A4", "C5", "E5"]
    assert isinstance(staff.events[1], m21.note.Note)


def test_unrecognised_tokens_are_skipped_not_fatal():
    """One misclassified glyph should not cost the whole page."""
    staff = parse_mozart_text("[ a1/4 ??? b1/4 ]")[0]
    assert len(staff.events) == 2


def test_building_a_score():
    score = mozart_text_to_score('[ \\meter<"4/4"> a1/4 b1/4 c2/4 d2/4 ]', title="demo")
    assert isinstance(score, m21.stream.Score)
    assert len(score.recurse().notes) == 4
    assert score.metadata.title == "demo"
    signatures = list(score.recurse().getElementsByClass(m21.meter.TimeSignature))
    assert str(signatures[0].ratioString) == "4/4"


def test_a_mozart_score_can_be_transposed():
    from transposer.transpose import transpose_score

    score = mozart_text_to_score('[ \\meter<"4/4"> a1/4 b1/4 c2/4 d2/4 ]')
    result, report = transpose_score(score, "+M2")
    assert report.interval.directedName == "M2"
    assert [n.nameWithOctave for n in result.recurse().notes] == ["B4", "C#5", "D5", "E5"]


def test_empty_output_is_an_error():
    with pytest.raises(OmrFailedError):
        parse_mozart_text("nothing here")


def test_a_staff_with_no_notes_is_an_error():
    with pytest.raises(OmrFailedError):
        mozart_text_to_score("[ ]")


def test_mozart_octave_numbers_are_offset_from_scientific_pitch():
    """Mozart's own reference output for testcases/04.PNG is a scale from middle C.

    The image is a two-octave treble-clef scale drawn from the ledger line below
    the staff, so ``c1`` must come out as C4 and ``b2`` as B5.
    """
    staff = parse_mozart_text(
        "[ c1/4 d1/4 e1/4 f1/4 g1/4 a1/4 b1/4 c2/4 d2/4 e2/4 f2/4 g2/4 a2/4 b2/4]"
    )[0]
    names = [n.nameWithOctave for n in staff.events]
    assert names[0] == "C4"
    assert names[-1] == "B5"
    assert names == [
        "C4", "D4", "E4", "F4", "G4", "A4", "B4",
        "C5", "D5", "E5", "F5", "G5", "A5", "B5",
    ]
