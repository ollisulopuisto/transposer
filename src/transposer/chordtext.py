"""Transposing chord symbols that arrived as plain text.

OMR engines only emit a proper MusicXML ``<harmony>`` element when they manage
to *parse* the text above the staff. Anything they recognise but cannot parse
-- ``Fm7``, ``B♭7``, ``C7/E`` on a slightly noisy scan -- comes through as a
free-floating text expression instead, and a naive transposition would move
every note while leaving those chord names pointing at the old key.

This module recognises chord-symbol-shaped text and rewrites the root (and the
bass note after a slash), leaving the quality suffix untouched. It deliberately
does *not* try to understand the suffix: ``m7b5``, ``sus4``, ``add9``, ``Δ``
and friends survive verbatim, because the suffix is key-independent.
"""

from __future__ import annotations

import re

from music21 import interval, pitch

#: A chord symbol: root, optional accidental, optional suffix, optional bass.
#: The suffix is lazy so that a trailing "/E" is read as a bass note, while a
#: "C6/9" -- where nothing after the slash is a note letter -- stays a suffix.
_CHORD_RE = re.compile(
    r"""^
    (?P<root>[A-G])
    (?P<root_accidental>\#{1,2}|b{1,2}|♯{1,2}|♭{1,2}|-{1,2})?
    (?P<suffix>\S*?)
    (?:
        \s*/\s*
        (?P<bass>[A-G])
        (?P<bass_accidental>\#{1,2}|b{1,2}|♯{1,2}|♭{1,2}|-{1,2})?
    )?
    $""",
    re.VERBOSE,
)

#: Suffix characters we accept. Anything outside this set means the text is
#: probably a lyric or a performance direction, not a chord.
_SUFFIX_OK = re.compile(r"^[0-9mMajdiousglt+\-#b°ø∆Δ()/♯♭,.]*$", re.IGNORECASE)

#: Words that look like chords but are not ("A", "Am" are real chords, but a
#: lyric syllable "A" above a staff usually is not). Kept deliberately short.
_NOT_CHORDS = {"a", "am", "be", "bed", "e", "dad", "add", "ad", "age", "ace", "bag"}

_ACCIDENTAL_TO_M21 = {
    None: "",
    "": "",
    "#": "#",
    "##": "##",
    "♯": "#",
    "♯♯": "##",
    "b": "-",
    "bb": "--",
    "♭": "-",
    "♭♭": "--",
    "-": "-",
    "--": "--",
}

#: How music21 spells accidentals, and how we want them printed back out.
_M21_TO_TEXT = {"": "", "#": "#", "##": "##", "-": "b", "--": "bb", "n": ""}


def looks_like_chord_symbol(text: str) -> bool:
    """Heuristic: does this text read as a chord symbol?

    >>> looks_like_chord_symbol("Bb7")
    True
    >>> looks_like_chord_symbol("Somewhere")
    False
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 12:
        return False
    if stripped.lower() in _NOT_CHORDS:
        return False
    match = _CHORD_RE.match(stripped)
    if match is None:
        return False
    suffix = match.group("suffix") or ""
    if not _SUFFIX_OK.match(suffix):
        return False
    # "Bb" is a chord; "Bed" is not -- reject suffixes that are pure lower-case
    # letters longer than the handful of real quality abbreviations.
    return not (
        suffix.isalpha()
        and suffix.lower()
        not in {"m", "maj", "min", "dim", "aug", "sus", "add", "mi", "ma"}
    )


def transpose_chord_symbol_text(text: str, iv: interval.Interval) -> str:
    """Move a textual chord symbol by ``iv``, keeping its quality suffix.

    >>> transpose_chord_symbol_text("Bb7", interval.Interval("M-3"))
    'Gb7'
    >>> transpose_chord_symbol_text("C/E", interval.Interval("M2"))
    'D/F#'
    """
    stripped = (text or "").strip()
    match = _CHORD_RE.match(stripped)
    if match is None:
        return text

    root = _transpose_note_name(
        match.group("root"), match.group("root_accidental"), iv
    )
    suffix = match.group("suffix") or ""

    result = f"{root}{suffix}"

    if match.group("bass"):
        bass = _transpose_note_name(
            match.group("bass"), match.group("bass_accidental"), iv
        )
        result = f"{result}/{bass}"

    # Preserve any leading/trailing whitespace the caller had.
    prefix = text[: len(text) - len(text.lstrip())]
    trailer = text[len(text.rstrip()) :]
    return f"{prefix}{result}{trailer}"


def _transpose_note_name(
    letter: str, accidental: str | None, iv: interval.Interval
) -> str:
    m21_accidental = _ACCIDENTAL_TO_M21.get(accidental or "", "")
    source = pitch.Pitch(f"{letter}{m21_accidental}4")
    moved = source.transpose(iv)
    moved = _simplify_pitch(moved)
    modifier = moved.accidental.modifier if moved.accidental else ""
    return f"{moved.step}{_M21_TO_TEXT.get(modifier, modifier)}"


#: Roots nobody prints on a chart. E sharp is F, C flat is B, and so on.
_AWKWARD_ROOTS = {"E#", "B#", "F-", "C-"}


def _simplify_pitch(p: pitch.Pitch) -> pitch.Pitch:
    """Avoid printing chord roots like ``B#``, ``Cb`` or ``Fbb``.

    Double accidentals are always respelled, as are the four single-accidental
    spellings that no chart uses. Ordinary ones are left alone: ``Gb`` is a
    perfectly good chord name and respelling it to ``F#`` would fight the key
    signature.
    """
    if p.accidental is None:
        return p
    if abs(p.accidental.alter) >= 2:
        return _simplify_pitch(p.getEnharmonic())
    if p.name in _AWKWARD_ROOTS:
        return p.getEnharmonic()
    return p
