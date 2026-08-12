"""Parser for the textual notation produced by the Mozart OMR project.

Mozart (https://github.com/aashrafh/Mozart) does not emit MusicXML. It emits a
compact bracket notation, one line per detected staff::

    [ \\meter<"4/4"> a1/4 b1/8 {c2/4,e2/4} d1/4. ]

and wraps several staves in an outer ``{ ... }`` block. The grammar is small:

``\\meter<"n/d">``
    Time signature for the staff. Optional.
``<letter><accidentals><octave>/<denominator>[.]``
    A note. ``&`` is a flat and ``&&`` a double flat, ``#`` a sharp and ``##``
    a double sharp; the accidental sits between letter and octave. The
    denominator is the note value (``4`` is a crotchet) and a trailing dot adds
    half again.

    Mozart numbers octaves from the bottom of the treble staff rather than
    scientifically: its ``c1`` is middle C, so :data:`OCTAVE_OFFSET` is added
    when building real pitches. Mozart's own reference output for its
    ``testcases/04.PNG`` -- a two-octave scale drawn from middle C -- reads
    ``c1 d1 ... b2``, which is what pins the mapping down.
``{note,note,...}``
    A chord: several notes sounding together.

This module turns that into a music21 score so the rest of the pipeline does
not have to care which engine produced the music.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from music21 import chord, duration, meter, note, stream

from ..errors import OmrFailedError

_METER_RE = re.compile(r'\\meter<"(?P<num>\d+)/(?P<den>\d+)">')
_NOTE_RE = re.compile(
    r"""^
    (?P<step>[a-gA-G])
    (?P<accidental>\#{1,2}|&{1,2})?
    (?P<octave>-?\d+)
    /
    (?P<denominator>\d+)
    (?P<dots>\.*)
    $""",
    re.VERBOSE,
)

_ACCIDENTALS = {None: "", "": "", "#": "#", "##": "##", "&": "-", "&&": "--"}

#: Added to Mozart's octave numbers to get scientific pitch notation.
OCTAVE_OFFSET = 3


@dataclass
class MozartStaff:
    """One recognised staff: an optional meter plus its events."""

    meter: tuple[int, int] | None
    events: list[note.Note | chord.Chord]


def parse_mozart_text(text: str) -> list[MozartStaff]:
    """Parse Mozart's output into a list of staves."""
    staves: list[MozartStaff] = []
    for line in _staff_lines(text):
        staves.append(_parse_staff(line))
    if not staves:
        raise OmrFailedError("Mozart produced no recognisable staves")
    return staves


def mozart_text_to_score(text: str, title: str | None = None) -> stream.Score:
    """Build a music21 score out of Mozart's textual output.

    Every staff becomes a measure-less :class:`music21.stream.Part`; music21's
    ``makeNotation`` then bars it up according to the detected meter. Mozart
    only ever reads treble-clef single staves, so the result is one part.
    """
    staves = parse_mozart_text(text)

    score = stream.Score()
    if title:
        score.insert(0, _metadata(title))

    part = stream.Part()
    part.id = "P1"

    first_meter = next((s.meter for s in staves if s.meter), None)
    if first_meter:
        part.append(meter.TimeSignature(f"{first_meter[0]}/{first_meter[1]}"))

    current = first_meter
    for staff in staves:
        if staff.meter and staff.meter != current:
            part.append(meter.TimeSignature(f"{staff.meter[0]}/{staff.meter[1]}"))
            current = staff.meter
        for event in staff.events:
            part.append(event)

    if not part.notes:
        raise OmrFailedError("Mozart recognised no notes")

    score.append(part)
    return score.makeNotation()


def _staff_lines(text: str) -> list[str]:
    """Yield the ``[...]`` blocks, ignoring the optional outer braces."""
    return [match.strip() for match in re.findall(r"\[(.*?)\]", text, flags=re.DOTALL)]


def _parse_staff(line: str) -> MozartStaff:
    detected_meter: tuple[int, int] | None = None
    meter_match = _METER_RE.search(line)
    if meter_match:
        detected_meter = (int(meter_match.group("num")), int(meter_match.group("den")))
        line = _METER_RE.sub(" ", line)

    events: list[note.Note | chord.Chord] = []
    for token in _tokenize(line):
        if token.startswith("{"):
            events.append(_parse_chord(token))
        else:
            parsed = _parse_note(token)
            if parsed is not None:
                events.append(parsed)
    return MozartStaff(meter=detected_meter, events=events)


def _tokenize(line: str) -> list[str]:
    """Split a staff line into note and chord tokens.

    Chords are brace-delimited and contain commas, so a plain ``split()`` would
    tear them apart.
    """
    tokens: list[str] = []
    buffer = ""
    depth = 0
    for character in line:
        if character == "{":
            depth += 1
            buffer += character
        elif character == "}":
            depth -= 1
            buffer += character
            if depth == 0:
                tokens.append(buffer)
                buffer = ""
        elif character.isspace() and depth == 0:
            if buffer:
                tokens.append(buffer)
                buffer = ""
        else:
            buffer += character
    if buffer:
        tokens.append(buffer)
    return [token for token in tokens if token]


def _parse_chord(token: str) -> chord.Chord:
    inner = token.strip().lstrip("{").rstrip("}")
    members = [_parse_note(part) for part in inner.split(",") if part.strip()]
    members = [m for m in members if m is not None]
    if not members:
        raise OmrFailedError(f"empty chord in Mozart output: {token!r}")
    built = chord.Chord([m.pitch for m in members])
    built.duration = members[0].duration
    return built


def _parse_note(token: str) -> note.Note | None:
    token = token.strip().rstrip(",")
    if not token:
        return None

    match = _NOTE_RE.match(token)
    if match is None:
        # Mozart occasionally emits a stray symbol name; skipping is friendlier
        # than aborting a whole page over one glyph.
        return None

    step = match.group("step").upper()
    accidental = _ACCIDENTALS.get(match.group("accidental"), "")
    octave = int(match.group("octave"))
    denominator = int(match.group("denominator"))
    dots = len(match.group("dots"))

    built = note.Note(f"{step}{accidental}{octave + OCTAVE_OFFSET}")
    built.duration = duration.Duration(4.0 / denominator)
    if dots:
        built.duration.dots = dots
    return built


def _metadata(title: str):
    from music21 import metadata as m21_metadata

    md = m21_metadata.Metadata()
    md.title = title
    return md
