"""Turning recognised free text back into lyrics, and sweeping up the rest.

Audiveris has a ``lyrics`` processing switch, and it is on. On a real scan it
still hands back the words as free-floating ``<words>`` elements rather than as
``<lyric>`` attached to notes -- each carrying the x and y it had on the page it
was read from.

That is fine as long as nobody re-engraves the music. Transposing does exactly
that: a new key, different accidentals, different note spacing, a different
staff width. Every one of those coordinates is now a position on a page that no
longer exists, and the words land on top of each other -- which is what a two
verse song looked like coming out of this pipeline.

A lyric attached to a note has no coordinates to be wrong. The renderer places
it under its note and widens the bar to fit, which is what engraving software is
for. So the repair is to put the text back where it belongs:

    <words> below the staff  ──▶  syllables on the notes underneath them

Two things decide where each one goes. Vertically, rows of text are verses: the
top row is verse one. Horizontally, a phrase is split into syllables and laid
across consecutive notes from the one nearest where the phrase started.

Nothing here invents an alignment the scan did not have. When a phrase cannot be
placed -- a bar with no notes in it, text above the staff, a note that already
carries a lyric -- the text is left exactly as it was.
"""

from __future__ import annotations

from .chordocr import is_above_staff

#: How far apart two rows of text have to be, in MusicXML tenths, before they
#: count as different verses. Staff lines are ten tenths apart, so this is about
#: two of them: comfortably more than the wobble in one row's baselines, and
#: comfortably less than the gap between verses.
VERSE_ROW_TOLERANCE = 20.0

#: Syllable separators OCR leaves in a phrase. A real hyphen, the spaced hyphen
#: engravers use between syllables, and the dashes Tesseract substitutes.
_SYLLABLE_BREAKS = {"-", "–", "—", "‑", "_"}

#: Text this short above a staff is not a word. It is half of a chord symbol the
#: recogniser tore apart, or a speck it read as a letter.
_DEBRIS_LENGTH = 2


def attach_lyrics(score) -> int:
    """Attach below-staff text to the notes it sits under, as lyrics.

    Returns the number of text elements consumed.
    """
    from music21 import expressions, stream

    attached = 0
    for part in _parts(score):
        for measure in part.getElementsByClass(stream.Measure):
            texts = [
                element
                for element in measure.getElementsByClass(expressions.TextExpression)
                if not is_above_staff(element)
            ]
            if not texts:
                continue

            notes = _singable(measure)
            if not notes:
                # Nothing to sing it on. Better a floating word than a lyric
                # invented onto a rest.
                continue

            verses = _verse_numbers(texts)
            for element in texts:
                syllables = _syllables(element.content or "")
                if not syllables:
                    continue
                if _place(element, syllables, notes, verses[id(element)]):
                    holder = element.activeSite
                    if holder is not None:
                        holder.remove(element)
                    attached += 1
    return attached


def _singable(measure) -> list:
    """The notes a lyric can go on, in time order.

    A bar with more than one voice keeps its notes in ``Voice`` streams rather
    than in the measure itself, so asking the measure directly finds almost
    none of them -- and a phrase then loses every syllable after its first,
    which engraves as one word trailed by a row of hyphens going nowhere.

    Where there are voices, the first one is the melody and the rest are
    accompaniment. Nobody sings the accompaniment.
    """
    from music21 import note, stream

    voices = list(measure.getElementsByClass(stream.Voice))
    holder = voices[0] if voices else measure
    return sorted(
        holder.getElementsByClass(note.NotRest), key=lambda item: float(item.offset)
    )


def _place(element, syllables: list[tuple[str, str]], notes: list, verse: int) -> bool:
    """Lay ``syllables`` across the notes from the one nearest ``element``.

    Returns False when nothing could be attached, in which case the caller
    leaves the text alone.
    """
    from music21 import note as m21_note

    start = _nearest(notes, float(element.offset))
    placed = False
    for index, (text, syllabic) in enumerate(syllables):
        position = start + index
        if position >= len(notes):
            break
        target = notes[position]
        if any(existing.number == verse for existing in target.lyrics):
            continue
        target.lyrics.append(
            m21_note.Lyric(text=text, number=verse, syllabic=syllabic)
        )
        placed = True
    return placed


def _nearest(notes: list, offset: float) -> int:
    """Index of the note closest to ``offset``, preferring the one at or after."""
    best, best_distance = 0, None
    for index, item in enumerate(notes):
        distance = abs(float(item.offset) - offset)
        if best_distance is None or distance < best_distance:
            best, best_distance = index, distance
    return best


def _syllables(content: str) -> list[tuple[str, str]]:
    """Split a recognised phrase into ``(text, syllabic)`` pairs.

    ``"Rain - bow"`` is one word sung over two notes, so its halves are marked
    ``begin`` and ``end`` -- which is how a renderer knows to draw the hyphen
    between them. ``"blue,"`` is a whole word on one note: ``single``.
    """
    parts = [part for part in content.split() if part not in _SYLLABLE_BREAKS]
    if not parts:
        return []
    if len(parts) == 1:
        return [(parts[0], "single")]

    hyphenated = any(break_ in content for break_ in _SYLLABLE_BREAKS)
    if not hyphenated:
        # Separate words that happen to have been grouped: each stands alone.
        return [(part, "single") for part in parts]

    syllables = [(parts[0], "begin")]
    syllables += [(part, "middle") for part in parts[1:-1]]
    syllables.append((parts[-1], "end"))
    return syllables


def _verse_numbers(texts: list) -> dict[int, int]:
    """Group text elements into verses by how far down the page they sit.

    Returns ``{id(element): verse number}``, verse one being the top row.
    """
    rows: list[float] = []
    for element in texts:
        y = _vertical(element)
        if not any(abs(y - row) <= VERSE_ROW_TOLERANCE for row in rows):
            rows.append(y)
    # MusicXML measures up from the staff, so the top row is the largest value.
    rows.sort(reverse=True)

    numbers: dict[int, int] = {}
    for element in texts:
        y = _vertical(element)
        for index, row in enumerate(rows, start=1):
            if abs(y - row) <= VERSE_ROW_TOLERANCE:
                numbers[id(element)] = index
                break
        else:  # pragma: no cover - every y matches the row it created
            numbers[id(element)] = 1
    return numbers


def _vertical(element) -> float:
    if not getattr(element, "hasStyleInformation", False):
        return 0.0
    return float(getattr(element.style, "absoluteY", None) or 0.0)


def drop_debris(score) -> int:
    """Remove one- and two-character junk floating above the staff.

    A stray "m" over a bar is half a chord symbol the recogniser tore apart, and
    it engraves as a letter hanging over the music with nothing to do with it.
    Anything that reads as a chord is kept, which covers the real one-character
    cases -- a bare C or F is a chord symbol, not debris -- as is a repeat
    ending's "1." or "2.".

    Only above the staff: below it, short text is a sung syllable, and
    :func:`attach_lyrics` has a use for it.
    """
    from music21 import expressions

    from .chordocr import repair_chord_symbol

    removed = 0
    for element in list(score.recurse().getElementsByClass(expressions.TextExpression)):
        content = (element.content or "").strip()
        if not content or len(content) > _DEBRIS_LENGTH:
            continue
        if not is_above_staff(element):
            continue
        if repair_chord_symbol(content).repaired:
            continue
        if any(character.isdigit() for character in content):
            # "1." and "2." mark repeat endings.
            continue

        holder = element.activeSite
        if holder is None:
            continue
        holder.remove(element)
        removed += 1
    return removed


def _parts(score):
    from music21 import stream

    parts = list(score.getElementsByClass(stream.Part))
    return parts or [score]
