"""Parsing of target-key specifications and resolution to a concrete interval.

The user-facing vocabulary is deliberately generous: ``C``, ``c``, ``C major``,
``C-duuri``, ``Am``, ``a-molli``, ``F#m``, ``Eb``, ``E-`` all parse, as do plain
interval specs such as ``-M3``, ``+P5``, ``-4`` (semitones) or ``down 4
semitones``.

Everything here is pure -- no I/O, no music21 stream mutation -- which makes it
the easiest part of the project to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from music21 import interval, key, pitch

from .errors import KeySpecError

Direction = Literal["auto", "up", "down"]

#: Words that mean "major", in the languages this project has users in.
_MAJOR_WORDS = {"", "maj", "major", "dur", "duuri", "ionian"}
#: Words that mean "minor".
_MINOR_WORDS = {"m", "min", "minor", "moll", "molli", "aeolian"}

_ACCIDENTAL_MAP = {
    "": "",
    "#": "#",
    "♯": "#",
    "##": "##",
    "♯♯": "##",
    "x": "##",
    "b": "-",
    "♭": "-",
    "-": "-",
    "bb": "--",
    "♭♭": "--",
    "--": "--",
}

# The hyphen is doing double duty: music21 spells a flat as "E-", and Finnish
# spells a key as "C-duuri". A hyphen only counts as an accidental when no
# quality word follows it.
#
# The tail after the note letter is captured as one greedy run and split in
# _split_key_tail() with ordinary string code. Previous revisions encoded the
# accidental/separator/quality split as optional regex groups, but those
# overlap ('-' and 'b' appear both in the accidental and after it), which is
# the ambiguous-quantifier shape CodeQL's py/polynomial-redos query flags and
# which backtracks super-linearly on adversarial input. One character class
# with a single quantifier cannot backtrack more than linearly.
_KEY_RE = re.compile(r"^\s*(?P<letter>[A-Ha-h])(?P<tail>[A-Za-z#♯♭\s-]*)$")

#: Run of separator characters (spaces and hyphens) between the accidental
#: and the quality word. A lone quantifier, matched once: no backtracking.
_KEY_SEP_RE = re.compile(r"[\s-]+")

#: Accidental prefixes the old regex captured greedily, longest first within
#: each character family so "bb" beats "b". Hyphens are handled separately
#: because of the "not directly before a letter" rule.
_KEY_ACCIDENTAL_PREFIXES = ("##", "♯♯", "♭♭", "bb", "#", "♯", "♭", "b", "x")

_ASCII_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _key_accidental_prefix(tail: str) -> str:
    """Return the accidental the old regex would capture at ``tail``'s start.

    Hyphens count only when the next character is not a letter, mirroring the
    old ``-{1,2}(?![A-Za-z])`` alternative: in "C-duuri" the hyphen is the
    Finnish separator, in "C-" it is a flat. Greedy two-before-one, as the
    old ``{1,2}`` quantifier was.
    """
    if tail.startswith("-"):
        if tail.startswith("--") and (len(tail) == 2 or tail[2] not in _ASCII_LETTERS):
            return "--"
        if len(tail) == 1 or tail[1] not in _ASCII_LETTERS:
            return "-"
        return ""
    for prefix in _KEY_ACCIDENTAL_PREFIXES:
        if tail.startswith(prefix):
            return prefix
    return ""


def _split_key_tail(tail: str) -> tuple[str, str] | None:
    """Split ``_KEY_RE``'s tail into ``(accidental, quality)``.

    Returns ``None`` when the remainder after the accidental and separator is
    not a plain ASCII-letter quality word, exactly the inputs the old regex
    refused to match (the caller then reports an unparseable target).
    Trailing whitespace is allowed and ignored, as the old ``\\s*$`` did.
    """
    tail = tail.rstrip()
    accidental = _key_accidental_prefix(tail)
    rest = tail[len(accidental) :]
    sep = _KEY_SEP_RE.match(rest)
    if sep is not None:
        rest = rest[sep.end() :]
    if rest and not (rest.isascii() and rest.isalpha()):
        return None
    return accidental, rest


_SEMITONE_RE = re.compile(
    r"""^\s*
    (?:(?P<word>up|down|ylös|alas)\s+)?
    (?P<sign>[+-])?
    (?P<value>\d{1,2})
    (?:\s*(?:st|semitones?|half\s*steps?|puolisävelaskel\w*)\s*|\s*)$""",
    re.IGNORECASE | re.VERBOSE,
)

# e.g. "-M3", "+P5", "m6", "d5", "A4", "P8"
_INTERVAL_RE = re.compile(r"^\s*(?:(?P<sign>[+-])\s*)?(?P<name>[PMmAd]+\d{1,2})\s*$")


@dataclass(frozen=True)
class TargetSpec:
    """A parsed ``--to`` value.

    Exactly one of :attr:`key` and :attr:`interval` is set. A ``key`` target
    means "make the music sound in this key", which requires knowing the source
    key; an ``interval`` target is absolute and needs no analysis.
    """

    raw: str
    key: key.Key | None = None
    interval: interval.Interval | None = None

    @property
    def is_key(self) -> bool:
        return self.key is not None

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.raw


def parse_target(spec: str) -> TargetSpec:
    """Parse a target specification into a :class:`TargetSpec`.

    >>> parse_target("C").key.name
    'C major'
    >>> parse_target("a-molli").key.name
    'a minor'
    >>> parse_target("-M3").interval.directedName
    'M-3'
    """
    if spec is None:
        raise KeySpecError("no target given")
    text = str(spec).strip()
    if not text:
        raise KeySpecError("empty target specification")

    # Plain semitone counts, e.g. "-3", "up 5", "+7 semitones".
    m = _SEMITONE_RE.match(text)
    if m:
        value = int(m.group("value"))
        downward = m.group("sign") == "-" or (m.group("word") or "").lower() in {"down", "alas"}
        sign = -1 if downward else 1
        return TargetSpec(raw=text, interval=interval.Interval(sign * value))

    # Named intervals, e.g. "M3", "-m6", "+P5".
    m = _INTERVAL_RE.match(text)
    if m:
        name = m.group("name")
        iv = interval.Interval(name)
        if m.group("sign") == "-":
            iv = iv.reverse()
        return TargetSpec(raw=text, interval=iv)

    # Key names.
    m = _KEY_RE.match(text)
    split = _split_key_tail(m.group("tail")) if m else None
    if split is not None:
        letter = m.group("letter")
        written = split[0].lower()
        accidental = _ACCIDENTAL_MAP.get(written)
        if accidental is None:
            raise KeySpecError(f"unrecognised accidental in {text!r}")
        quality = split[1].lower()

        if quality in _MAJOR_WORDS:
            mode = "major"
        elif quality in _MINOR_WORDS:
            mode = "minor"
        else:
            raise KeySpecError(
                f"unrecognised key quality {split[1]!r} in {text!r}; "
                "try 'major'/'minor' (or 'duuri'/'molli')"
            )

        # With no explicit quality word, fall back to the classic convention:
        # upper case is major, lower case is minor.
        if quality == "":
            mode = "major" if letter.isupper() else "minor"

        # H is B natural in German and Nordic usage, and never means anything
        # else. Note letters are otherwise read the English way, so "B" is B
        # natural and B flat is written "Bb".
        step = "B" if letter.upper() == "H" else letter.upper()
        tonic = step + accidental
        return TargetSpec(raw=text, key=key.Key(tonic, mode))

    raise KeySpecError(
        f"could not parse target {text!r}; expected a key such as 'C', 'F#m', "
        "'Bb major', or an interval such as '-M3' or '+5'"
    )


def _pitch_at(name: str, octave: int) -> pitch.Pitch:
    p = pitch.Pitch(name)
    p.octave = octave
    return p


def interval_between_keys(
    source: key.Key | pitch.Pitch | str,
    target: key.Key | pitch.Pitch | str,
    direction: Direction = "auto",
    octave_shift: int = 0,
) -> interval.Interval:
    """Return the interval that maps ``source``'s tonic onto ``target``'s.

    ``direction`` picks between the two spellings of the same pitch-class move:
    E flat to C is either a descending major third or an ascending minor sixth.
    ``auto`` takes the shorter of the two (ties go downwards, which keeps
    singers happier more often than not).

    ``octave_shift`` adds whole octaves on top of the result.
    """
    src_name = _tonic_name(source)
    tgt_name = _tonic_name(target)

    src = _pitch_at(src_name, 4)
    same_octave = _pitch_at(tgt_name, 4)

    if direction == "up":
        octave = 4 if same_octave.ps >= src.ps else 5
    elif direction == "down":
        octave = 4 if same_octave.ps <= src.ps else 3
    elif direction == "auto":
        up_octave = 4 if same_octave.ps >= src.ps else 5
        down_octave = 4 if same_octave.ps <= src.ps else 3
        up_distance = abs(_pitch_at(tgt_name, up_octave).ps - src.ps)
        down_distance = abs(_pitch_at(tgt_name, down_octave).ps - src.ps)
        # Strict "<" means a tritone tie resolves downwards.
        octave = up_octave if up_distance < down_distance else down_octave
    else:
        raise KeySpecError(f"unknown direction {direction!r}")

    dest = _pitch_at(tgt_name, octave + octave_shift)
    return interval.Interval(noteStart=src, noteEnd=dest)


def _tonic_name(value: key.Key | pitch.Pitch | str) -> str:
    if isinstance(value, key.Key):
        return value.tonic.name
    if isinstance(value, pitch.Pitch):
        return value.name
    if isinstance(value, str):
        return pitch.Pitch(value).name
    raise KeySpecError(f"cannot read a tonic out of {value!r}")


def transposed_key(source: key.Key, iv: interval.Interval) -> key.Key:
    """The key you land in when transposing ``source`` by ``iv``."""
    tonic = source.tonic.transpose(iv)
    return key.Key(tonic.name, source.mode)


def accidental_count(k: key.Key) -> int:
    """How many sharps or flats the key signature carries (absolute)."""
    return abs(k.sharps)


def simplify_key(k: key.Key) -> key.Key:
    """Swap a key for its enharmonic twin when that has fewer accidentals.

    D flat major (5 flats) stays put; G sharp major (8 sharps) becomes A flat
    major (4 flats). Ties keep the original spelling.
    """
    try:
        alternative_tonic = k.tonic.getEnharmonic()
    except Exception:  # pragma: no cover - music21 raises for exotic spellings
        return k
    try:
        alternative = key.Key(alternative_tonic.name, k.mode)
    except Exception:  # pragma: no cover
        return k
    if accidental_count(alternative) < accidental_count(k):
        return alternative
    return k
