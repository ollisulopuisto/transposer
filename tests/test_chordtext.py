import pytest
from music21 import interval

from transposer.chordtext import looks_like_chord_symbol, transpose_chord_symbol_text

DOWN_A_MINOR_THIRD = interval.Interval("m-3")


@pytest.mark.parametrize(
    "text",
    ["C", "Cm", "Bb7", "F#m7", "A-maj7", "Ebmaj7", "C/E", "G7sus4", "Bbm7b5", "C6/9"],
)
def test_chord_shaped_text_is_recognised(text):
    assert looks_like_chord_symbol(text)


@pytest.mark.parametrize(
    "text",
    ["Somewhere", "rain", "N.C.", "", "   ", "Andante con moto", "Bed", "poco rit."],
)
def test_prose_is_not_mistaken_for_a_chord(text):
    assert not looks_like_chord_symbol(text)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Eb", "C"),
        ("Cm", "Am"),
        ("Gm", "Em"),
        ("Eb7", "C7"),
        ("Ab", "F"),
        ("Ab7", "F7"),
        ("Fm7", "Dm7"),
        ("Bb7", "G7"),
        ("Am7", "F#m7"),
        ("D7", "B7"),
        ("C7", "A7"),
    ],
)
def test_the_rainbow_chart_moves_from_e_flat_to_c(before, after):
    assert transpose_chord_symbol_text(before, DOWN_A_MINOR_THIRD) == after


def test_slash_chords_move_both_notes():
    assert transpose_chord_symbol_text("C/E", interval.Interval("M2")) == "D/F#"


def test_quality_suffixes_survive_untouched():
    moved = transpose_chord_symbol_text("Bbm7b5", DOWN_A_MINOR_THIRD)
    assert moved == "Gm7b5"


def test_unusual_roots_are_respelled():
    """G sharp down a minor third is E sharp on paper, which nobody writes."""
    assert transpose_chord_symbol_text("G#dim", DOWN_A_MINOR_THIRD) == "Fdim"


def test_double_accidentals_are_respelled():
    moved = transpose_chord_symbol_text("Fb", interval.Interval("m-2"))
    assert "bb" not in moved.lower()[1:]


def test_non_chord_text_is_returned_unchanged():
    assert transpose_chord_symbol_text("Somewhere", DOWN_A_MINOR_THIRD) == "Somewhere"


def test_surrounding_whitespace_is_preserved():
    assert transpose_chord_symbol_text("  Eb  ", DOWN_A_MINOR_THIRD) == "  C  "


def test_a_unison_leaves_the_symbol_alone():
    assert transpose_chord_symbol_text("Bb7", interval.Interval("P1")) == "Bb7"
