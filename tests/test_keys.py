import pytest
from music21 import key

from transposer.errors import KeySpecError
from transposer.keys import (
    interval_between_keys,
    parse_target,
    simplify_key,
    transposed_key,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("C", "C major"),
        ("c", "C minor"),
        ("Cm", "C minor"),
        ("C major", "C major"),
        ("C minor", "C minor"),
        ("Eb", "E- major"),
        ("E-", "E- major"),
        ("E♭", "E- major"),
        ("F#m", "F# minor"),
        ("Bb major", "B- major"),
        ("Bbm", "B- minor"),
        ("  g  ", "G minor"),
    ],
)
def test_parse_key_names(spec, expected):
    assert parse_target(spec).key.name == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("C-duuri", "C major"),
        ("c-molli", "C minor"),
        ("a-molli", "A minor"),
        ("Ab-duuri", "A- major"),
        ("Eb-molli", "E- minor"),
        ("C dur", "C major"),
        ("a moll", "A minor"),
    ],
)
def test_parse_finnish_and_german_keys(spec, expected):
    """A hyphen is a flat in music21 and a separator in Finnish; both must work."""
    assert parse_target(spec).key.name == expected


def test_h_is_b_natural():
    assert parse_target("H").key.tonic.name == "B"
    assert parse_target("h").key.name == "B minor"


@pytest.mark.parametrize(
    ("spec", "directed"),
    [
        ("-M3", "M-3"),
        ("+P5", "P5"),
        ("M3", "M3"),
        ("m6", "m6"),
    ],
)
def test_parse_named_intervals(spec, directed):
    assert parse_target(spec).interval.directedName == directed


@pytest.mark.parametrize(
    ("spec", "semitones"),
    [
        ("-4", -4),
        ("+7", 7),
        ("3", 3),
        ("down 3", -3),
        ("up 5", 5),
        ("-2 semitones", -2),
    ],
)
def test_parse_semitones(spec, semitones):
    assert parse_target(spec).interval.semitones == semitones


@pytest.mark.parametrize("spec", ["", "  ", "Q", "H#m7b5x", "purple", "M99x"])
def test_bad_specs_raise(spec):
    with pytest.raises(KeySpecError):
        parse_target(spec)


def test_eb_to_c_is_a_minor_third_down():
    iv = interval_between_keys("E-", "C")
    assert iv.directedName == "m-3"
    assert iv.semitones == -3


def test_direction_overrides_the_shortest_path():
    assert interval_between_keys("E-", "C", direction="up").directedName == "M6"
    assert interval_between_keys("E-", "C", direction="down").directedName == "m-3"


def test_octave_shift_stacks_on_top():
    iv = interval_between_keys("E-", "C", direction="down", octave_shift=-1)
    assert iv.semitones == -15


def test_unison_when_keys_match():
    assert interval_between_keys("C", "C").semitones == 0


def test_tritone_ties_resolve_downwards():
    """C to F sharp is six semitones either way; singers prefer down."""
    assert interval_between_keys("C", "F#").semitones == -6


def test_transposed_key_keeps_the_mode():
    source = key.Key("E-", "minor")
    assert transposed_key(source, interval_between_keys("E-", "C")).name == "C minor"


@pytest.mark.parametrize(
    ("tonic", "expected"),
    [
        ("G#", "A-"),  # 8 sharps -> 4 flats
        ("D-", "D-"),  # 5 flats stays; C# major would be 7 sharps
        ("C", "C"),
    ],
)
def test_simplify_key_prefers_fewer_accidentals(tonic, expected):
    assert simplify_key(key.Key(tonic, "major")).tonic.name == expected
