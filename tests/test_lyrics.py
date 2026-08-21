"""Tests for turning recognised free text back into lyrics.

Audiveris has a ``lyrics`` switch and it is on, but on a real scan it still
emits the words as free-floating ``<words>`` carrying the x and y they had on
the original page. Re-engraving lays the music out differently -- different
staff width, different note spacing, a different key -- so every one of those
coordinates is now wrong, and the words land on top of each other.

Attached to notes as real lyrics, the renderer places them itself, and a two
verse song comes out as two readable rows.
"""

from __future__ import annotations

import music21 as m21
import pytest

from transposer.lyrics import attach_lyrics, drop_debris


def make_part(pitches="cdefgab", quarter_length=1.0, measures=2):
    """A part with numbered measures of plain quarter notes."""
    part = m21.stream.Part()
    index = 0
    for number in range(1, measures + 1):
        measure = m21.stream.Measure(number=number)
        if number == 1:
            measure.insert(0, m21.meter.TimeSignature("4/4"))
        for beat in range(4):
            measure.insert(
                beat * quarter_length,
                m21.note.Note(pitches[index % len(pitches)] + "4", quarterLength=quarter_length),
            )
            index += 1
        part.append(measure)
    return part


def add_text(measure, content, offset=0.0, absolute_y=-80):
    element = m21.expressions.TextExpression(content)
    element.style.absoluteY = absolute_y
    measure.insert(offset, element)
    return element


def score_of(part):
    score = m21.stream.Score()
    score.insert(0, part)
    return score


# -- attaching -------------------------------------------------------------


def test_text_below_the_staff_becomes_a_lyric_on_the_nearest_note():
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "way", offset=2.0)
    score = score_of(part)

    assert attach_lyrics(score) == 1

    lyrics = {
        float(n.offset): n.lyric
        for n in measure.getElementsByClass(m21.note.Note)
        if n.lyric
    }
    assert lyrics == {2.0: "way"}
    assert not list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


def test_a_phrase_is_spread_over_consecutive_notes():
    """"Some - where" is two syllables, not one word on one note."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "Some - where", offset=0.0)
    score = score_of(part)

    assert attach_lyrics(score) == 1

    sung = [n.lyric for n in measure.getElementsByClass(m21.note.Note)]
    assert sung[:2] == ["Some", "where"]
    assert sung[2] is None


def test_a_hyphen_marks_the_syllable_as_continuing():
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "Rain - bow", offset=0.0)
    score = score_of(part)
    attach_lyrics(score)

    first, second = list(measure.getElementsByClass(m21.note.Note))[:2]
    assert first.lyrics[0].syllabic == "begin"
    assert second.lyrics[0].syllabic == "end"


def test_a_word_on_its_own_is_a_single_syllable():
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "blue,", offset=1.0)
    score = score_of(part)
    attach_lyrics(score)

    note = next(n for n in measure.getElementsByClass(m21.note.Note) if n.lyric)
    assert note.lyrics[0].syllabic == "single"


def test_two_rows_of_text_become_two_verses():
    """The rows are the verses, and the higher row is verse one."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "Some", offset=0.0, absolute_y=-80)
    add_text(measure, "Same", offset=0.0, absolute_y=-110)
    score = score_of(part)

    assert attach_lyrics(score) == 2

    note = next(iter(measure.getElementsByClass(m21.note.Note)))
    by_number = {lyric.number: lyric.text for lyric in note.lyrics}
    assert by_number == {1: "Some", 2: "Same"}


def test_syllables_reach_notes_inside_voices():
    """A bar with two voices keeps its notes in Voice streams, not in the
    measure, so looking only at the measure finds one note and drops every
    syllable after the first -- which engraves as a word followed by a row of
    hyphens going nowhere.
    """
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.insert(0, m21.meter.TimeSignature("4/4"))

    melody = m21.stream.Voice(id="1")
    for beat in range(4):
        melody.insert(float(beat), m21.note.Note("c4", quarterLength=1.0))
    measure.insert(0, melody)

    accompaniment = m21.stream.Voice(id="2")
    accompaniment.insert(0.0, m21.note.Note("g3", quarterLength=4.0))
    measure.insert(0, accompaniment)

    add_text(measure, "O - ver the Rain - bow", offset=0.0)
    part.append(measure)
    score = score_of(part)

    assert attach_lyrics(score) == 1

    sung = [n.lyric for n in melody.getElementsByClass(m21.note.Note)]
    assert sung == ["O", "ver", "the", "Rain"]
    # The second voice is accompaniment; nobody sings it.
    assert not any(n.lyrics for n in accompaniment.getElementsByClass(m21.note.Note))


def test_text_above_the_staff_is_not_a_lyric():
    """Chord symbols and tempo marks live up there."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "Fm7", offset=0.0, absolute_y=32)
    score = score_of(part)

    assert attach_lyrics(score) == 0
    assert list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


def test_a_measure_with_no_notes_keeps_its_text():
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.insert(0, m21.note.Rest(quarterLength=4.0))
    add_text(measure, "way", offset=0.0)
    part.append(measure)
    score = score_of(part)

    assert attach_lyrics(score) == 0
    assert list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


def test_existing_lyrics_are_not_disturbed():
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    first = next(iter(measure.getElementsByClass(m21.note.Note)))
    first.lyric = "already"
    add_text(measure, "way", offset=2.0)
    score = score_of(part)

    attach_lyrics(score)
    assert first.lyric == "already"


def test_stale_positions_do_not_survive():
    """The coordinates were measured on a page this is not being engraved as."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "way", offset=2.0)
    score = score_of(part)
    attach_lyrics(score)

    note = next(n for n in measure.getElementsByClass(m21.note.Note) if n.lyric)
    assert getattr(note.lyrics[0].style, "absoluteX", None) is None


# -- debris ---------------------------------------------------------------


@pytest.mark.parametrize("junk", ["m", "P", "l", "|", "'"])
def test_one_character_debris_above_the_staff_is_dropped(junk):
    """A stray "m" over bar 3 is a piece of a chord symbol the OCR tore in half,
    and it engraves as a letter floating over the music."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, junk, offset=0.0, absolute_y=32)
    score = score_of(part)

    assert drop_debris(score) == 1
    assert not list(score.recurse().getElementsByClass(m21.expressions.TextExpression))


@pytest.mark.parametrize("kept", ["C", "F", "Am", "G7", "1.", "2.", "1", "2"])
def test_real_short_text_is_kept(kept):
    """A one-letter chord symbol is one character too, and so is a repeat
    ending's number -- with or without the dot Audiveris may not have read.
    Deleting one of those loses the score's structure, which is worse than
    leaving a stray digit on the page."""
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, kept, offset=0.0, absolute_y=32)
    score = score_of(part)

    assert drop_debris(score) == 0


def test_debris_below_the_staff_is_left_for_the_lyrics_pass():
    part = make_part()
    measure = part.getElementsByClass(m21.stream.Measure)[0]
    add_text(measure, "m", offset=0.0, absolute_y=-80)
    score = score_of(part)

    assert drop_debris(score) == 0
