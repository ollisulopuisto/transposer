"""Recovering chord symbols that OCR mangled.

On a lead sheet the chord symbols are the point, and they are also the thing
optical character recognition handles worst. Audiveris drives Tesseract's
*legacy* engine, which on the condensed fonts used for chord charts confuses a
small, stable set of glyphs::

    Fm7  ->  Fm?     C7  ->  CT      Am7 ->  Am?
    Bb7  ->  BW      Eb  ->  E'P     Gm7 ->  Gm"!

The important thing about that list is what is *right* in it: the root letter
survives, and the symbol is in the correct place on the page. Only the quality
suffix is scrambled. So the repair does not need to find chord symbols -- it
needs to re-spell text that is already known to sit above a staff.

The approach is a confusion-aware match against the chord grammar rather than a
list of hand-written substitutions:

* the root must be a literal A-G, optionally with a recognisable accidental.
  A misread root would transpose to the wrong chord, which is worse than no
  chord at all, so roots are never guessed.
* the suffix is matched against the qualities that actually appear on charts,
  under an edit distance where known OCR confusions are cheap and everything
  else is expensive.
* a match is accepted only when it is both good and unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Glyphs Tesseract substitutes for each other on chart fonts. Each entry maps
#: a character we want to the shapes it gets misread as.
CONFUSIONS: dict[str, str] = {
    "7": "?T1lIi|!j)¡í7ƒ",
    "b": "PW6&Þþ",
    "#": "H+ﬀ",
    "m": "nrhМ",
    "9": "gq",
    "6": "G",
    "5": "S",
    "4": "A",
    "0": "OoQ°",
    "s": "S5$",
    "u": "vn",
    "a": "oe",
    "d": "cl",
    "j": "iJ",
}

#: Built once: ``(wanted, seen) -> cost``.
_SUBSTITUTION_COST: dict[tuple[str, str], float] = {}
for _wanted, _seen_chars in CONFUSIONS.items():
    for _seen in _seen_chars:
        _SUBSTITUTION_COST[(_wanted, _seen)] = 0.35
        _SUBSTITUTION_COST[(_wanted.upper(), _seen)] = 0.35

#: Characters OCR invents out of nothing -- specks, quote marks, stray serifs.
#: Deleting one of these from the observed text is nearly free. Bars and
#: exclamation marks are deliberately *not* here: they are how a 7 comes back,
#: and treating them as specks would silently turn "Gm7" into "Gm".
_NOISE = set("'\"`‘’“”.,·•*^~-_ ¦")

#: Chord qualities that actually appear on charts, in the spelling we emit.
QUALITIES: tuple[str, ...] = (
    "", "m", "7", "m7", "maj7", "6", "m6", "9", "m9", "maj9",
    "11", "13", "sus", "sus2", "sus4", "7sus4", "m7b5", "dim", "dim7",
    "aug", "+", "add9", "2", "5", "69", "6/9", "7b9", "7#9", "7b5",
    "7#5", "9sus4", "m11", "m13", "mmaj7", "°", "ø",
)

#: How bad a match may be before it is rejected, per character of the suffix.
_MAX_COST_PER_CHAR = 0.55
#: And an absolute ceiling, so a long stretch of mush cannot accumulate enough
#: budget to "match" something. Badly damaged text is better left as text.
_MAX_TOTAL_COST = 1.0
#: How much better the best candidate must be than the runner-up.
_MIN_MARGIN = 0.15

_ROOTS = set("ABCDEFG")
_ACCIDENTAL_FOR = {
    "b": "b", "B": "b", "P": "b", "W": "b", "6": "b", "&": "b",
    "#": "#", "H": "#", "+": "#",
    "♭": "b", "♯": "#", "-": "b",
}


@dataclass(frozen=True)
class ChordRepair:
    """The outcome of trying to re-spell one piece of text."""

    original: str
    repaired: str | None
    cost: float = 0.0
    changed: bool = False

    def __bool__(self) -> bool:
        return self.repaired is not None


def repair_chord_symbol(text: str, max_cost_per_char: float = _MAX_COST_PER_CHAR) -> ChordRepair:
    """Try to read ``text`` as a chord symbol, correcting OCR damage.

    Returns a :class:`ChordRepair` whose ``repaired`` field is ``None`` when the
    text could not be read as a chord with enough confidence.

    >>> repair_chord_symbol("Fm?").repaired
    'Fm7'
    >>> repair_chord_symbol("CT").repaired
    'C7'
    >>> repair_chord_symbol("Somewhere").repaired is None
    True
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 12:
        return ChordRepair(text, None)

    stripped = raw.lstrip("".join(_NOISE))
    if not stripped or stripped[0] not in _ROOTS:
        return ChordRepair(text, None)

    root = stripped[0]
    # Specks between the root and its accidental are common ("E'P" for E flat),
    # so clear them before deciding whether an accidental is present.
    rest = stripped[1:].lstrip("".join(_NOISE))

    accidental = ""
    if rest:
        candidate = _ACCIDENTAL_FOR.get(rest[0])
        # "B" reads as a flat sign only when something follows it; a bare "AB"
        # is not a chord, but "AB7" is A flat seven misread.
        if candidate and not (rest[0] in _ROOTS and len(rest) == 1):
            accidental = candidate
            rest = rest[1:]

    best, second = _best_quality(rest, max_cost_per_char)
    if best is None:
        return ChordRepair(text, None)

    quality, cost = best
    if second is not None and second[1] - cost < _MIN_MARGIN:
        # Two readings fit equally well; guessing between them is not repair.
        return ChordRepair(text, None)

    repaired = f"{root}{accidental}{quality}"
    return ChordRepair(text, repaired, cost=cost, changed=repaired != raw)


def _best_quality(
    observed: str, max_cost_per_char: float
) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
    """Return the two cheapest quality readings of ``observed``."""
    budget = min(_MAX_TOTAL_COST, max_cost_per_char * max(len(observed), 1))

    scored: list[tuple[str, float]] = []
    for quality in QUALITIES:
        cost = _distance(quality, observed)
        if cost <= budget:
            scored.append((quality, cost))

    if not scored:
        return None, None

    scored.sort(key=lambda pair: (pair[1], len(pair[0])))
    return scored[0], (scored[1] if len(scored) > 1 else None)


def _distance(wanted: str, observed: str) -> float:
    """Edit distance where known OCR confusions and specks are cheap.

    Plain Levenshtein would rank ``Fm?`` as equally close to ``Fm7`` and
    ``Fm9``; weighting the substitutions by what Tesseract actually does breaks
    that tie the right way.
    """
    rows, columns = len(wanted), len(observed)
    previous = [float(index) for index in range(columns + 1)]

    for i in range(1, rows + 1):
        current = [float(i)]
        for j in range(1, columns + 1):
            want_char = wanted[i - 1]
            seen_char = observed[j - 1]

            if want_char == seen_char:
                substitute = previous[j - 1]
            else:
                substitute = previous[j - 1] + _SUBSTITUTION_COST.get(
                    (want_char, seen_char),
                    0.4 if want_char.lower() == seen_char.lower() else 1.0,
                )

            # Deleting a speck from the observed text is nearly free; dropping a
            # character the chord grammar requires is not.
            delete = current[j - 1] + (0.1 if seen_char in _NOISE else 0.9)
            insert = previous[j] + 0.9

            current.append(min(substitute, delete, insert))
        previous = current

    return previous[columns]


def repair_all(texts: list[str]) -> list[ChordRepair]:
    """Convenience wrapper for scoring a page's worth of text at once."""
    return [repair_chord_symbol(text) for text in texts]


def is_above_staff(element) -> bool:
    """Whether a text item sits above its staff, where chord symbols live.

    MusicXML's ``default-y`` is measured up from the top staff line, so chord
    symbols come through positive and lyrics negative. Items with no position
    at all are allowed through, because the chord grammar is strict enough to
    reject prose on its own.
    """
    placement = getattr(element, "placement", None)
    if placement == "above":
        return True
    if placement == "below":
        return False

    if not getattr(element, "hasStyleInformation", False):
        return True
    position = getattr(element.style, "absoluteY", None)
    if position is None:
        return True
    return position > 0


def merge_chord_symbols(primary, secondary) -> tuple[int, list[str]]:
    """Copy chord symbols from ``secondary`` into ``primary``.

    Binarising a page sharpens the text and blurs the noteheads, so the run
    that reads chord symbols best is not the run that reads notes best. Rather
    than choose, the pipeline can do both and merge -- but only when the two
    passes agree on the bar structure, since a chord copied into the wrong
    measure is worse than a chord that was never read.

    Returns the number of symbols added and any notes for the report.
    """
    from music21 import harmony, stream

    primary_measures = _measures_by_number(primary)
    secondary_measures = _measures_by_number(secondary)

    if not primary_measures or not secondary_measures:
        return 0, ["the second pass produced no measures, so nothing was merged"]

    if len(primary_measures) != len(secondary_measures):
        return 0, [
            "the two recognition passes disagreed on the number of measures "
            f"({len(primary_measures)} vs {len(secondary_measures)}), so their "
            "chord symbols were not merged"
        ]

    added = 0
    for number, target_measure in primary_measures.items():
        source_measure = secondary_measures.get(number)
        if source_measure is None:
            continue

        present = list(target_measure.getElementsByClass(harmony.ChordSymbol))
        # A bar practically never restates the same chord symbol, so an
        # identical figure anywhere in it means the two passes found the same
        # chord and merely disagreed about the beat.
        figures = {symbol.figure for symbol in present}
        occupied = {round(float(symbol.offset), 3) for symbol in present}

        for symbol in source_measure.getElementsByClass(harmony.ChordSymbol):
            offset = round(float(symbol.offset), 3)
            if symbol.figure in figures:
                continue
            if any(abs(offset - taken) < 0.5 for taken in occupied):
                # Something is already written at that beat; two chord symbols
                # on one beat is a recognition artefact, not a chart.
                continue
            import copy as _copy

            target_measure.insert(offset, _copy.deepcopy(symbol))
            occupied.add(offset)
            figures.add(symbol.figure)
            added += 1

    notes = []
    if added:
        notes.append(
            f"merged {added} chord symbol(s) that only the second, "
            "text-optimised recognition pass could read"
        )
    del stream
    return added, notes


def _measures_by_number(score) -> dict[int, object]:
    """First measure with each number, taken from the first part that has any."""
    from music21 import stream

    for part in score.getElementsByClass(stream.Part):
        measures = list(part.getElementsByClass(stream.Measure))
        if measures:
            return {
                measure.number: measure
                for measure in measures
                if measure.number is not None
            }
    return {}


def promote_chord_symbols(score, only_above_staff: bool = True) -> tuple[int, list[str]]:
    """Turn repaired chord text into real chord symbols.

    Text expressions above the staff that read as chord symbols become
    :class:`music21.harmony.ChordSymbol` objects, which means they transpose as
    harmony -- root, bass and quality together -- rather than as text, and they
    engrave in the chord-symbol style rather than as a floating comment.

    Returns the number promoted and a note for each spelling that changed.
    """
    from music21 import expressions, harmony

    promoted = 0
    notes: list[str] = []

    for element in list(score.recurse().getElementsByClass(expressions.TextExpression)):
        content = (element.content or "").strip()
        if not content:
            continue
        if only_above_staff and not is_above_staff(element):
            continue

        repair = repair_chord_symbol(content)
        if not repair.repaired:
            continue

        try:
            symbol = harmony.ChordSymbol(repair.repaired)
        except Exception:
            # music21 could not build a chord from a figure our grammar
            # accepted; leaving the text alone is the safe outcome.
            continue

        holder = element.activeSite
        if holder is None:
            continue
        offset = element.offset
        holder.remove(element)
        holder.insert(offset, symbol)
        promoted += 1

        if repair.changed:
            notes.append(f"read chord symbol {content!r} as {repair.repaired!r}")

    return promoted, notes
