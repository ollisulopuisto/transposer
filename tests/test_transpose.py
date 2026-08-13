import music21 as m21
import pytest

from transposer.transpose import (
    INSTRUMENT_TRANSPOSITIONS,
    count_pitched_events,
    detect_key,
    resolve_instrument,
    transpose_score,
)

DEFAULT_NOTES = [("E-4", 1.0), ("F4", 1.0), ("G4", 1.0), ("A-4", 1.0), ("B-3", 4.0)]


def make_score(key_name="E-", mode="major", notes=DEFAULT_NOTES):
    """A one-part score in a given key, with a real key signature."""
    part = m21.stream.Part()
    part.append(m21.key.Key(key_name, mode))
    part.append(m21.meter.TimeSignature("4/4"))
    for pitch_name, quarters in notes:
        note = m21.note.Note(pitch_name)
        note.quarterLength = quarters
        part.append(note)
    score = m21.stream.Score()
    score.append(part)
    return score


def make_lead_sheet():
    """A score with chord symbols above the staff, like a real lead sheet."""
    part = m21.stream.Part()
    part.append(m21.key.Key("E-", "major"))
    part.append(m21.meter.TimeSignature("4/4"))
    for figure, pitch_name in [("E-", "e-4"), ("Cm", "c4"), ("A-maj7", "a-4"), ("B-7", "b-3")]:
        part.append(m21.harmony.ChordSymbol(figure))
        note = m21.note.Note(pitch_name)
        note.quarterLength = 4.0
        part.append(note)
    score = m21.stream.Score()
    score.append(part)
    return score


def test_detects_a_notated_key():
    detected, origin = detect_key(make_score())
    assert detected.name == "E- major"
    assert origin == "notated"


def test_detects_a_key_from_pitches_when_none_is_notated():
    part = m21.stream.Part()
    for name in ["c4", "e4", "g4", "c5", "g4", "e4", "c4"]:
        part.append(m21.note.Note(name))
    score = m21.stream.Score()
    score.append(part)
    detected, origin = detect_key(score)
    assert origin == "analysed"
    assert detected is not None


def test_transposing_to_c_moves_every_note_down_a_minor_third():
    score = make_score()
    result, report = transpose_score(score, "C")

    assert report.interval.directedName == "m-3"
    assert report.written_key.name == "C major"

    pitches = [n.nameWithOctave for n in result.recurse().notes]
    assert pitches == ["C4", "D4", "E4", "F4", "G3"]


def test_transposition_rewrites_the_key_signature():
    result, _ = transpose_score(make_score(), "C")
    signatures = list(result.recurse().getElementsByClass(m21.key.KeySignature))
    assert signatures
    assert all(s.sharps == 0 for s in signatures)


def test_the_source_score_is_left_alone():
    score = make_score()
    before = [n.nameWithOctave for n in score.recurse().notes]
    transpose_score(score, "C")
    assert [n.nameWithOctave for n in score.recurse().notes] == before


def test_in_place_transposition_mutates():
    score = make_score()
    result, _ = transpose_score(score, "C", in_place=True)
    assert result is score
    assert next(n.nameWithOctave for n in score.recurse().notes) == "C4"


def test_chord_symbols_follow_the_notes():
    result, report = transpose_score(make_lead_sheet(), "C")
    figures = [c.figure for c in result.recurse().getElementsByClass(m21.harmony.ChordSymbol)]
    roots = [c.root().name for c in result.recurse().getElementsByClass(m21.harmony.ChordSymbol)]
    assert report.chord_symbols_moved == 4
    assert roots == ["C", "A", "F", "G"]
    assert all(figure for figure in figures)


def test_interval_targets_need_no_key_analysis():
    result, report = transpose_score(make_score(), "+M2")
    assert report.interval.directedName == "M2"
    assert next(n.nameWithOctave for n in result.recurse().notes) == "F4"


def test_octave_shift_applies_on_top_of_a_key_target():
    _, report = transpose_score(make_score(), "C", octave_shift=-1)
    assert report.interval.semitones == -15


@pytest.mark.parametrize(
    ("instrument", "expected_key"),
    [
        ("bb-trumpet", "D major"),
        ("eb-alto-sax", "A major"),
        ("f-horn", "G major"),
        ("concert", "C major"),
    ],
)
def test_writing_for_a_transposing_instrument(instrument, expected_key):
    """Concert C read by a B flat trumpet is written in D."""
    _, report = transpose_score(make_score(), "C", instrument=instrument)
    assert report.written_key.name == expected_key
    assert report.target_key.name == "C major"


def test_unknown_instrument_is_rejected():
    with pytest.raises(ValueError, match="unknown instrument"):
        resolve_instrument("kazoo")


def test_every_listed_instrument_resolves():
    for name in INSTRUMENT_TRANSPOSITIONS:
        slug, iv = resolve_instrument(name)
        assert slug == name
        assert iv is not None


def test_awkward_destination_keys_are_respelled():
    """B major up a fourth is E; up a fifth would be F sharp, not G flat."""
    _, report = transpose_score(make_score("G#", "major"), "C")
    assert abs(report.written_key.sharps) <= 7


def test_text_chord_symbols_are_transposed_too():
    score = make_score()
    part = score.parts[0]
    part.insert(0, m21.expressions.TextExpression("B-7"))
    part.insert(4, m21.expressions.TextExpression("Somewhere"))

    result, report = transpose_score(score, "C")
    texts = result.recurse().getElementsByClass(m21.expressions.TextExpression)
    contents = [t.content for t in texts]

    assert report.text_chords_moved == 1
    assert "G7" in contents
    assert "Somewhere" in contents


def test_text_chords_can_be_left_alone():
    score = make_score()
    score.parts[0].insert(0, m21.expressions.TextExpression("B-7"))
    result, report = transpose_score(score, "C", transpose_text_chords=False)
    texts = result.recurse().getElementsByClass(m21.expressions.TextExpression)
    contents = [t.content for t in texts]
    assert report.text_chords_moved == 0
    assert "B-7" in contents


def test_a_named_key_target_needs_a_source_key():
    part = m21.stream.Part()
    part.append(m21.note.Rest(quarterLength=4))
    score = m21.stream.Score()
    score.append(part)
    with pytest.raises(ValueError, match="source key"):
        transpose_score(score, "C")


def test_count_pitched_events_counts_chord_tones():
    part = m21.stream.Part()
    part.append(m21.note.Note("c4"))
    part.append(m21.chord.Chord(["e4", "g4", "b4"]))
    score = m21.stream.Score()
    score.append(part)
    assert count_pitched_events(score) == 4


def test_report_summary_mentions_both_keys():
    _, report = transpose_score(make_score(), "C")
    summary = report.summary()
    assert "E- major" in summary
    assert "C major" in summary
