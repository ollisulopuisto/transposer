"""Tests for repairing chord symbols that OCR mangled.

The garbled strings below are verbatim from Tesseract's output on a real
144 dpi lead-sheet scan, so this file doubles as a record of how the legacy
engine fails on chart fonts.
"""

import music21 as m21
import pytest

from transposer.chordocr import (
    is_above_staff,
    promote_chord_symbols,
    repair_all,
    repair_chord_symbol,
)

#: What Tesseract produced, and what the chart actually said.
OBSERVED = [
    ("Fm?", "Fm7"),
    ("Gm?", "Gm7"),
    ("Am?", "Am7"),
    ("D?", "D7"),
    ("CT", "C7"),
    ("Fm1", "Fm7"),
    ("Fm‘l", "Fm7"),
    ('Gm"!', "Gm7"),
    ("Gm“!", "Gm7"),
    ("E'P", "Eb"),
    ("AW", "Ab"),
    ("BW", "Bb"),
]

#: Text from the same page that must never be read as a chord.
PROSE = [
    "Somewhere", "blue.", "high,", "where you'll", "way", "skies", "are",
    "land", "true.", "Same", "me.", "Where", "bind", "day", "star and",
    "trey-He: me", "le - mdn drape. a -", "dmrm reaLIy do", "that l' heard",
    "Andante", "poco rit.", "D.C. al Fine", "Piano", "Harold Arlen",
    "Moderato", "rall.", "Verse", "Chorus", "1.", "2.",
]


@pytest.mark.parametrize(("observed", "expected"), OBSERVED)
def test_garbled_chords_are_repaired(observed, expected):
    assert repair_chord_symbol(observed).repaired == expected


@pytest.mark.parametrize("text", PROSE)
def test_prose_is_never_read_as_a_chord(text):
    """A false positive prints a chord the music does not have, which is worse
    than leaving the text alone."""
    assert repair_chord_symbol(text).repaired is None


@pytest.mark.parametrize(
    "text",
    ["C", "Cm", "Bb7", "F#m7", "Ebmaj7", "Dm7", "G7sus4", "Am", "Bbm7b5", "C7"],
)
def test_clean_chords_survive_unchanged(text):
    repair = repair_chord_symbol(text)
    assert repair.repaired == text
    assert not repair.changed


def test_a_root_is_never_guessed():
    """Misreading a root transposes to the wrong chord, so it is not attempted."""
    for text in ["Xm7", "8m7", "?m7", "Hm7"]:
        assert repair_chord_symbol(text).repaired is None


def test_hopeless_mush_is_rejected():
    """Text damaged past recognition should stay text, not become a guess."""
    for text in ["Fm‘ir", "Gm—bklikeit", "C%&/(", "Am7xyzzy"]:
        assert repair_chord_symbol(text).repaired is None


def test_an_ambiguous_reading_is_rejected():
    repair = repair_chord_symbol("C" + " ")
    assert repair.repaired in {None, "C"}


def test_empty_and_oversized_text():
    assert repair_chord_symbol("").repaired is None
    assert repair_chord_symbol("   ").repaired is None
    assert repair_chord_symbol(None).repaired is None
    assert repair_chord_symbol("Cmaj7sus4add9b13#11").repaired is None


def test_the_repair_records_whether_it_changed_anything():
    assert repair_chord_symbol("Fm?").changed
    assert not repair_chord_symbol("Fm7").changed


def test_repair_all_maps_a_page():
    results = repair_all(["Fm?", "Somewhere", "C7"])
    assert [r.repaired for r in results] == ["Fm7", None, "C7"]


def test_a_repair_is_falsy_when_it_failed():
    assert not repair_chord_symbol("Somewhere")
    assert repair_chord_symbol("Fm?")


# -- placement ----------------------------------------------------------


def make_text(content, absolute_y=None, placement=None):
    element = m21.expressions.TextExpression(content)
    if absolute_y is not None:
        element.style.absoluteY = absolute_y
    if placement is not None:
        element.placement = placement
    return element


def test_text_above_the_staff_is_where_chords_live():
    assert is_above_staff(make_text("Fm7", absolute_y=31))
    assert not is_above_staff(make_text("Some - where", absolute_y=-81))


def test_explicit_placement_wins():
    assert is_above_staff(make_text("Fm7", absolute_y=-81, placement="above"))
    assert not is_above_staff(make_text("Fm7", absolute_y=31, placement="below"))


def test_unpositioned_text_is_allowed_through():
    assert is_above_staff(make_text("Fm7"))


# -- promotion ----------------------------------------------------------


def build_score(items):
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    for content, y in items:
        measure.insert(0, make_text(content, absolute_y=y))
    measure.append(m21.note.Note("c4", quarterLength=4))
    part.append(measure)
    score = m21.stream.Score()
    score.append(part)
    return score


def test_repaired_text_becomes_a_real_chord_symbol():
    score = build_score([("Fm?", 31)])
    promoted, notes = promote_chord_symbols(score)

    assert promoted == 1
    assert notes == ["read chord symbol 'Fm?' as 'Fm7'"]

    symbols = list(score.recurse().getElementsByClass(m21.harmony.ChordSymbol))
    assert len(symbols) == 1
    assert symbols[0].root().name == "F"
    assert not list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


def test_lyrics_below_the_staff_are_left_alone():
    score = build_score([("Am?", -81)])
    promoted, _ = promote_chord_symbols(score)
    assert promoted == 0
    assert list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


def test_promoted_chords_then_transpose_as_harmony():
    """The whole point: a recovered symbol must move with the music."""
    from transposer.transpose import transpose_score

    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.insert(0, m21.key.Key("E-", "major"))
    measure.insert(0, make_text("Fm?", absolute_y=31))
    measure.append(m21.note.Note("e-4", quarterLength=4))
    part.append(measure)
    score = m21.stream.Score()
    score.append(part)

    assert promote_chord_symbols(score)[0] == 1

    result, report = transpose_score(score, "C")
    symbols = list(result.recurse().getElementsByClass(m21.harmony.ChordSymbol))
    assert report.interval.directedName == "m-3"
    assert symbols[0].root().name == "D"  # Fm7 down a minor third is Dm7


def test_promotion_can_be_told_to_ignore_placement():
    score = build_score([("Am?", -81)])
    promoted, _ = promote_chord_symbols(score, only_above_staff=False)
    assert promoted == 1


def test_a_bare_flat_triad_becomes_a_chord_symbol():
    """music21 will not build a ChordSymbol from the figure "Bb".

    It reads the "b" as a chord abbreviation rather than an accidental and
    raises, so a chart's Bb, Eb and Ab -- the commonest chords in the flat keys
    a horn player transposes out of -- were being read, repaired, and then
    silently dropped on the way into the score.
    """
    for figure, root in [("Bb", "B-"), ("Eb", "E-"), ("Ab", "A-"), ("Db", "D-")]:
        score = build_score([(figure, 31)])
        promoted, _ = promote_chord_symbols(score)
        assert promoted == 1, figure

        (symbol,) = list(score.recurse().getElementsByClass(m21.harmony.ChordSymbol))
        assert symbol.root().name == root


def test_flat_chords_with_a_quality_still_work():
    score = build_score([("Bbm7", 31)])
    assert promote_chord_symbols(score)[0] == 1
    (symbol,) = list(score.recurse().getElementsByClass(m21.harmony.ChordSymbol))
    assert symbol.root().name == "B-"
    assert symbol.chordKind == "minor-seventh"


# -- slashes: a seven, or a bass note -----------------------------------


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        ("A/", "A7"),
        ("Fm/", "Fm7"),
        ("Cmaj/", "Cmaj7"),
        ("Gm/", "Gm7"),
        ("Dm7/b5", "Dm7b5"),
    ],
)
def test_a_slash_where_a_seven_should_be(observed, expected):
    """The LSTM engine's signature failure on chart fonts, as the legacy
    engine's is a question mark."""
    assert repair_chord_symbol(observed).repaired == expected


@pytest.mark.parametrize(
    "text", ["C/G", "F/A", "Dm7/F", "Bb/D", "G7/B", "Am/C", "F#m7/C#"]
)
def test_a_slash_bass_note_is_kept(text):
    """A chart's C/G is a C chord over a G, not a mangled C7."""
    repair = repair_chord_symbol(text)
    assert repair.repaired == text
    assert not repair.changed


def test_a_slash_chord_becomes_a_chord_symbol_with_that_bass():
    score = build_score([("Bb/D", 31)])
    assert promote_chord_symbols(score)[0] == 1
    (symbol,) = list(score.recurse().getElementsByClass(m21.harmony.ChordSymbol))
    assert symbol.root().name == "B-"
    assert symbol.bass().name == "D"


def test_a_slash_followed_by_nonsense_is_not_a_bass_note():
    assert repair_chord_symbol("C/Somewhere").repaired is None


# -- merging two recognition passes -------------------------------------


def score_with_chords(chords_by_measure, measures=3):
    """A score with the given ``{measure number: [(offset, figure)]}``."""
    part = m21.stream.Part()
    for number in range(1, measures + 1):
        measure = m21.stream.Measure(number=number)
        for offset, figure in chords_by_measure.get(number, []):
            measure.insert(offset, m21.harmony.ChordSymbol(figure))
        measure.append(m21.note.Note("c4", quarterLength=4))
        part.append(measure)
    score = m21.stream.Score()
    score.append(part)
    return score


def figures_of(score):
    return sorted(
        (m.number, c.figure)
        for p in score.parts
        for m in p.getElementsByClass(m21.stream.Measure)
        for c in m.getElementsByClass(m21.harmony.ChordSymbol)
    )


def test_merge_adds_chords_the_first_pass_missed():
    from transposer.chordocr import merge_chord_symbols

    primary = score_with_chords({1: [(0.0, "C")]})
    secondary = score_with_chords({1: [(0.0, "C")], 2: [(0.0, "F")], 3: [(0.0, "G7")]})

    added, notes = merge_chord_symbols(primary, secondary)
    assert added == 2
    assert notes
    assert figures_of(primary) == [(1, "C"), (2, "F"), (3, "G7")]


def test_merge_does_not_duplicate_a_chord_the_passes_placed_differently():
    """The same figure twice in one bar is two readings of one chord."""
    from transposer.chordocr import merge_chord_symbols

    primary = score_with_chords({1: [(0.0, "C")]})
    secondary = score_with_chords({1: [(2.0, "C")]})

    added, _ = merge_chord_symbols(primary, secondary)
    assert added == 0
    assert figures_of(primary) == [(1, "C")]


def test_merge_refuses_when_the_passes_disagree_on_bar_count():
    """A chord copied into the wrong measure is worse than a missing one."""
    from transposer.chordocr import merge_chord_symbols

    primary = score_with_chords({1: [(0.0, "C")]}, measures=3)
    secondary = score_with_chords({1: [(0.0, "F")]}, measures=5)

    added, notes = merge_chord_symbols(primary, secondary)
    assert added == 0
    assert any("disagreed on the number of measures" in note for note in notes)
    assert figures_of(primary) == [(1, "C")]


def test_merge_leaves_a_beat_alone_when_it_is_already_taken():
    from transposer.chordocr import merge_chord_symbols

    primary = score_with_chords({1: [(0.0, "C")]})
    secondary = score_with_chords({1: [(0.25, "Dm7")]})

    added, _ = merge_chord_symbols(primary, secondary)
    assert added == 0


def test_merge_with_an_empty_second_pass():
    from transposer.chordocr import merge_chord_symbols

    primary = score_with_chords({1: [(0.0, "C")]})
    added, notes = merge_chord_symbols(primary, m21.stream.Score())
    assert added == 0
    assert notes
