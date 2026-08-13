import music21 as m21

from transposer.cleanup import (
    clean_score,
    drop_spurious_key_changes,
    drop_unparsed_text,
    ensure_title,
    flatten_octave_clefs,
    scrub_metadata,
    strip_credits,
)


def score_with_key_changes(*sharps_per_measure):
    part = m21.stream.Part()
    for index, sharps in enumerate(sharps_per_measure, start=1):
        measure = m21.stream.Measure(number=index)
        if sharps is not None:
            measure.append(m21.key.KeySignature(sharps))
        measure.append(m21.note.Note("c4", quarterLength=4))
        part.append(measure)
    score = m21.stream.Score()
    score.append(part)
    return score


def test_a_two_bar_key_change_is_dropped():
    """A signature that reverts almost immediately is a smudge, not a modulation."""
    score = score_with_key_changes(-3, None, -2, None, -3, None)
    removed, notes = drop_spurious_key_changes(score, mode="auto")
    assert removed == 1
    assert notes
    remaining = [k.sharps for k in score.recurse().getElementsByClass(m21.key.KeySignature)]
    assert remaining == [-3, -3]


def test_a_real_modulation_survives():
    score = score_with_key_changes(-3, None, None, None, 2, None, None, None, None)
    removed, _ = drop_spurious_key_changes(score, mode="auto")
    assert removed == 0


def test_drop_mode_keeps_only_the_first():
    score = score_with_key_changes(-3, None, 2, None, 4, None, None, None, None)
    removed, _ = drop_spurious_key_changes(score, mode="drop")
    assert removed == 2
    assert [k.sharps for k in score.recurse().getElementsByClass(m21.key.KeySignature)] == [-3]


def test_keep_mode_changes_nothing():
    score = score_with_key_changes(-3, None, -2, None, -3, None)
    removed, _ = drop_spurious_key_changes(score, mode="keep")
    assert removed == 0


def test_a_repeated_identical_signature_is_redundant():
    score = score_with_key_changes(-3, -3, None)
    removed, _ = drop_spurious_key_changes(score, mode="auto")
    assert removed == 1


def test_flattening_an_octave_clef_keeps_the_notes_where_they_were_drawn():
    """Dropping a treble-8vb without raising the notes buries them in ledger lines."""
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.append(m21.clef.Treble8vbClef())
    measure.append(m21.note.Note("E-3", quarterLength=4))
    part.append(measure)
    score = m21.stream.Score()
    score.append(part)

    assert flatten_octave_clefs(score) == 1

    clefs = list(score.recurse().getElementsByClass(m21.clef.Clef))
    assert isinstance(clefs[0], m21.clef.TrebleClef)
    assert not clefs[0].octaveChange
    assert [n.nameWithOctave for n in score.recurse().notes] == ["E-4"]


def test_flattening_preserves_flat_spelling():
    """A chromatic octave respells G flat as F sharp; a named octave does not."""
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.append(m21.clef.Treble8vbClef())
    for name in ["G-3", "A-3", "B-3"]:
        measure.append(m21.note.Note(name, quarterLength=1))
    part.append(measure)
    score = m21.stream.Score()
    score.append(part)

    flatten_octave_clefs(score)
    assert [n.nameWithOctave for n in score.recurse().notes] == ["G-4", "A-4", "B-4"]


def test_plain_clefs_are_left_alone():
    part = m21.stream.Part()
    measure = m21.stream.Measure(number=1)
    measure.append(m21.clef.TrebleClef())
    measure.append(m21.note.Note("c4", quarterLength=4))
    part.append(measure)
    score = m21.stream.Score()
    score.append(part)
    assert flatten_octave_clefs(score) == 0


def test_dropping_unreadable_text_spares_chord_symbols():
    part = m21.stream.Part()
    part.insert(0, m21.expressions.TextExpression("Bb7"))
    part.insert(0, m21.expressions.TextExpression("trey-He: me"))
    part.append(m21.note.Note("c4", quarterLength=4))
    score = m21.stream.Score()
    score.append(part)

    assert drop_unparsed_text(score) == 1
    texts = score.recurse().getElementsByClass(m21.expressions.TextExpression)
    remaining = [t.content for t in texts]
    assert remaining == ["Bb7"]


def test_a_movement_name_becomes_the_title():
    score = m21.stream.Score()
    score.insert(0, m21.metadata.Metadata())
    score.metadata.movementName = "Somewhere over the Rainbow"
    ensure_title(score)
    assert score.metadata.title == "Somewhere over the Rainbow"


def test_an_existing_title_is_not_overwritten():
    score = m21.stream.Score()
    score.insert(0, m21.metadata.Metadata())
    score.metadata.title = "Real Title"
    score.metadata.movementName = "Other"
    ensure_title(score)
    assert score.metadata.title == "Real Title"


def test_garbled_composer_names_are_removed():
    score = m21.stream.Score()
    score.insert(0, m21.metadata.Metadata())
    score.metadata.composer = "I'IﬂroIdArkn"
    notes = scrub_metadata(score)
    assert notes
    assert not score.metadata.composer


def test_real_composer_names_survive():
    for name in ["Harold Arlen", "J. S. Bach", "Jean Sibelius", "Music21", "Saint-Saëns"]:
        score = m21.stream.Score()
        score.insert(0, m21.metadata.Metadata())
        score.metadata.composer = name
        scrub_metadata(score)
        assert score.metadata.composer == name


def test_strip_credits_removes_everything_by_default(tmp_path):
    path = tmp_path / "score.musicxml"
    path.write_text(
        """<?xml version="1.0"?>
<score-partwise version="4.0">
  <credit><credit-words>Somewhere over the Rainbow</credit-words></credit>
  <credit><credit-words>D?</credit-words></credit>
  <part-list/>
</score-partwise>""",
        encoding="utf-8",
    )
    assert strip_credits(path) == 2
    assert "credit" not in path.read_text(encoding="utf-8")


def test_strip_credits_can_keep_named_ones(tmp_path):
    path = tmp_path / "score.musicxml"
    path.write_text(
        """<?xml version="1.0"?>
<score-partwise version="4.0">
  <credit><credit-words>Somewhere over the Rainbow</credit-words></credit>
  <credit><credit-words>D?</credit-words></credit>
  <part-list/>
</score-partwise>""",
        encoding="utf-8",
    )
    assert strip_credits(path, keep_texts=["Somewhere over the Rainbow"]) == 1
    assert "Rainbow" in path.read_text(encoding="utf-8")


def test_clean_score_reports_what_it_did():
    score = score_with_key_changes(-3, None, -2, None, -3, None)
    report = clean_score(score)
    assert report.changed
    assert report.key_changes_dropped == 1
    assert report.notes


# -- bars that do not add up ---------------------------------------------


def bar_of(part, number, contents):
    measure = m21.stream.Measure(number=number)
    if number == 1:
        measure.insert(0, m21.meter.TimeSignature("4/4"))
    offset = 0.0
    for length in contents:
        measure.insert(offset, m21.note.Note("c4", quarterLength=length))
        offset += length
    part.append(measure)
    return measure


def test_bars_that_do_not_fill_are_named():
    """"6 measures whose rhythm did not add up" tells you something is wrong
    and not where. The bar numbers are the difference between that and knowing
    which three bars to open in a notation editor."""
    from transposer.cleanup import incomplete_measures

    part = m21.stream.Part()
    bar_of(part, 1, [1, 1, 1, 1])
    bar_of(part, 2, [1, 1])
    bar_of(part, 3, [1, 1, 1, 1])
    bar_of(part, 4, [1, 1, 1, 0.5])
    score = m21.stream.Score()
    score.insert(0, part)

    found = incomplete_measures(score)
    assert [entry.number for entry in found] == [2, 4]
    assert found[0].actual == 2.0
    assert found[0].expected == 4.0


def test_a_complete_score_reports_nothing():
    from transposer.cleanup import incomplete_measures

    part = m21.stream.Part()
    bar_of(part, 1, [1, 1, 1, 1])
    bar_of(part, 2, [2, 2])
    score = m21.stream.Score()
    score.insert(0, part)

    assert incomplete_measures(score) == []


def test_a_pickup_bar_is_not_a_mistake():
    """A score that opens with an anacrusis is written that way on purpose."""
    from transposer.cleanup import incomplete_measures

    part = m21.stream.Part()
    pickup = bar_of(part, 0, [1])
    pickup.paddingLeft = 3.0
    bar_of(part, 1, [1, 1, 1, 1])
    score = m21.stream.Score()
    score.insert(0, part)

    assert incomplete_measures(score) == []


def test_the_same_bar_in_two_staves_is_reported_once():
    """A grand staff misreads both hands of a bar together; the user only needs
    to be told about the bar."""
    from transposer.cleanup import incomplete_measures

    score = m21.stream.Score()
    for _ in range(2):
        part = m21.stream.Part()
        bar_of(part, 1, [1, 1, 1, 1])
        bar_of(part, 2, [1, 1])
        score.insert(0, part)

    assert [entry.number for entry in incomplete_measures(score)] == [2]


def test_the_cleanup_report_names_them():
    from transposer.cleanup import clean_score

    part = m21.stream.Part()
    bar_of(part, 1, [1, 1, 1, 1])
    bar_of(part, 2, [1, 1])
    score = m21.stream.Score()
    score.insert(0, part)

    report = clean_score(score)
    assert any("bar 2" in note and "2 of 4" in note for note in report.notes), report.notes
