"""Key detection and transposition of a music21 score.

The interesting part of transposing a real lead sheet is not moving the
noteheads -- music21 does that in one call -- it is everything around them:

* the key signature has to move and stay spelled sensibly,
* chord symbols above the staff have to move *and* keep readable names,
* a piece with no notated key signature still needs a source key to move from,
* transposing instruments want a written key that differs from the sounding one.

:func:`transpose_score` handles all of that and reports what it did.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from music21 import chord, expressions, harmony, interval, key, note, stream

from .chordtext import looks_like_chord_symbol, transpose_chord_symbol_text
from .keys import (
    Direction,
    TargetSpec,
    interval_between_keys,
    parse_target,
    simplify_key,
    transposed_key,
)

#: Written-pitch offset for common transposing instruments, expressed as the
#: interval from *sounding* pitch to *written* pitch. A B flat trumpet playing
#: written C sounds B flat, so its written part sits a major second above
#: concert pitch.
INSTRUMENT_TRANSPOSITIONS: dict[str, str] = {
    "concert": "P1",
    "c": "P1",
    "bb": "M2",
    "bb-trumpet": "M2",
    "bb-clarinet": "M2",
    "bb-soprano-sax": "M2",
    "bb-tenor-sax": "M9",
    "eb-alto-sax": "M6",
    "eb-baritone-sax": "M13",
    "eb-clarinet": "m-3",
    "f-horn": "P5",
    "f-english-horn": "P5",
    "a-clarinet": "m3",
    "g-alto-flute": "P4",
    "guitar": "P8",
    "bass": "P8",
}


@dataclass
class TranspositionReport:
    """What :func:`transpose_score` decided and did."""

    source_key: key.Key | None
    source_key_origin: str
    target_key: key.Key | None
    written_key: key.Key | None
    interval: interval.Interval
    instrument: str | None = None
    instrument_interval: interval.Interval | None = None
    notes_moved: int = 0
    chord_symbols_moved: int = 0
    text_chords_moved: int = 0
    key_signatures_rewritten: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-paragraph, human-readable description of the transposition."""
        src = self.source_key.name if self.source_key else "unknown key"
        dst = self.written_key.name if self.written_key else "target"
        bits = [
            f"{src} → {dst} ({self.interval.directedName}, "
            f"{self.interval.semitones:+d} semitones)",
            f"source key {self.source_key_origin}",
            f"{self.notes_moved} notes",
        ]
        if self.chord_symbols_moved:
            bits.append(f"{self.chord_symbols_moved} chord symbols")
        if self.text_chords_moved:
            bits.append(f"{self.text_chords_moved} textual chord names")
        if self.key_signatures_rewritten:
            bits.append(f"{self.key_signatures_rewritten} key signatures")
        if self.instrument and self.instrument not in {"concert", "c"}:
            bits.append(f"written for {self.instrument}")
        return "; ".join(bits)


def detect_key(score: stream.Score | stream.Stream) -> tuple[key.Key | None, str]:
    """Work out what key a score is in.

    Returns the key plus a word describing where it came from: ``"notated"``
    when the score carries a real :class:`music21.key.Key`, ``"signature"``
    when only a bare key signature was present and the mode had to be guessed,
    ``"analysed"`` when the pitches themselves were used, and ``"unknown"``
    when nothing worked.
    """
    signatures = list(score.recurse().getElementsByClass(key.KeySignature))

    for candidate in signatures:
        if isinstance(candidate, key.Key):
            return candidate, "notated"

    analysed: key.Key | None = None
    try:
        analysed = score.analyze("key")
    except Exception:  # pragma: no cover - analysis needs pitches to chew on
        analysed = None

    if signatures:
        signature = signatures[0]
        # A bare signature says how many sharps/flats, not major vs minor.
        # Trust the pitch analysis for the mode when the two agree on
        # accidentals, otherwise assume major.
        if analysed is not None and analysed.sharps == signature.sharps:
            return analysed, "signature"
        return signature.asKey("major"), "signature"

    if analysed is not None:
        return analysed, "analysed"

    return None, "unknown"


def resolve_instrument(name: str | None) -> tuple[str | None, interval.Interval | None]:
    """Map an instrument key name onto its sounding-to-written interval."""
    if not name:
        return None, None
    slug = name.strip().lower().replace("_", "-").replace(" ", "-")
    if slug not in INSTRUMENT_TRANSPOSITIONS:
        known = ", ".join(sorted(INSTRUMENT_TRANSPOSITIONS))
        raise ValueError(f"unknown instrument {name!r}; known values: {known}")
    return slug, interval.Interval(INSTRUMENT_TRANSPOSITIONS[slug])


def plan_interval(
    score: stream.Score,
    target: TargetSpec | str,
    direction: Direction = "auto",
    octave_shift: int = 0,
    instrument: str | None = None,
) -> tuple[interval.Interval, TranspositionReport]:
    """Decide how far to move the music, without touching it yet."""
    spec = parse_target(target) if isinstance(target, str) else target
    source_key, origin = detect_key(score)
    warnings: list[str] = []

    instrument_slug, instrument_interval = resolve_instrument(instrument)

    if spec.is_key:
        if source_key is None:
            raise ValueError(
                "cannot transpose to a named key: the source key could not be "
                "determined. Give an interval instead (for example --to -M3)."
            )
        if origin == "analysed":
            warnings.append(
                "the score carries no key signature; the source key was guessed "
                f"from its pitches as {source_key.name}"
            )
        base = interval_between_keys(
            source_key, spec.key, direction=direction, octave_shift=octave_shift
        )
        target_key: key.Key | None = key.Key(spec.key.tonic.name, source_key.mode)
        if spec.key.mode != source_key.mode:
            warnings.append(
                f"target {spec.key.name} was requested but the source is "
                f"{source_key.mode}; keeping the source mode and moving the "
                f"tonic to {spec.key.tonic.name}"
            )
    else:
        base = spec.interval
        if octave_shift:
            base = interval.Interval(noteStart=base.noteStart, noteEnd=base.noteEnd) if False else base
            base = _add_octaves(base, octave_shift)
        target_key = transposed_key(source_key, base) if source_key else None

    total = base
    if instrument_interval is not None and instrument_interval.semitones != 0:
        total = _combine(base, instrument_interval)

    written_key = transposed_key(source_key, total) if source_key else None

    report = TranspositionReport(
        source_key=source_key,
        source_key_origin=origin,
        target_key=target_key,
        written_key=written_key,
        interval=total,
        instrument=instrument_slug,
        instrument_interval=instrument_interval,
        warnings=warnings,
    )
    return total, report


def transpose_score(
    score: stream.Score,
    target: TargetSpec | str,
    direction: Direction = "auto",
    octave_shift: int = 0,
    instrument: str | None = None,
    simplify_enharmonics: bool = True,
    transpose_text_chords: bool = True,
    in_place: bool = False,
) -> tuple[stream.Score, TranspositionReport]:
    """Transpose ``score`` and return the moved score plus a report.

    ``target`` may be a key name (``"C"``, ``"a-molli"``) or an interval
    (``"-M3"``, ``"+5"``). ``simplify_enharmonics`` respells the destination key
    when the enharmonic equivalent has fewer accidentals, so a request that
    would land in G sharp major produces A flat major instead.
    ``transpose_text_chords`` also rewrites chord names that an OMR engine left
    as plain text rather than parsing into real harmony elements.
    """
    iv, report = plan_interval(
        score,
        target,
        direction=direction,
        octave_shift=octave_shift,
        instrument=instrument,
    )

    result = score if in_place else copy.deepcopy(score)

    report.notes_moved = sum(
        1 for _ in result.recurse().getElementsByClass(note.NotRest)
    )
    report.chord_symbols_moved = sum(
        1 for _ in result.recurse().getElementsByClass(harmony.Harmony)
    )

    result.transpose(iv, inPlace=True)

    # music21 transposes ChordSymbol pitches but can leave the printed figure
    # stale, which would export the old chord name on top of the new pitches.
    _refresh_chord_symbols(result)

    if transpose_text_chords:
        report.text_chords_moved = _transpose_text_chords(result, iv)

    report.key_signatures_rewritten = _rewrite_key_signatures(
        result, simplify_enharmonics=simplify_enharmonics
    )

    if report.written_key is not None and simplify_enharmonics:
        simplified = simplify_key(report.written_key)
        if simplified.tonic.name != report.written_key.tonic.name:
            report.warnings.append(
                f"respelled {report.written_key.name} as {simplified.name} "
                "(fewer accidentals)"
            )
        report.written_key = simplified

    return result, report


def _add_octaves(iv: interval.Interval, octaves: int) -> interval.Interval:
    """Return ``iv`` shifted by whole octaves, keeping its spelling."""
    result = iv
    step = interval.Interval("P8") if octaves > 0 else interval.Interval("P-8")
    for _ in range(abs(octaves)):
        result = _combine(result, step)
    return result


def _combine(first: interval.Interval, second: interval.Interval) -> interval.Interval:
    """Compose two intervals, preserving enharmonic spelling."""
    try:
        return interval.add([first, second])
    except Exception:  # pragma: no cover - fall back to a chromatic sum
        return interval.Interval(first.semitones + second.semitones)


def _refresh_chord_symbols(score: stream.Stream) -> None:
    """Re-derive each chord symbol's printed figure from its (moved) pitches."""
    for symbol in list(score.recurse().getElementsByClass(harmony.Harmony)):
        if isinstance(symbol, harmony.NoChord):
            continue
        try:
            symbol.figure = harmony.chordSymbolFigureFromChord(symbol)
        except Exception:
            # A figure music21 cannot re-derive (odd slash chords, "N.C."
            # variants) is better left as music21 transposed it than blanked.
            continue


def _transpose_text_chords(score: stream.Stream, iv: interval.Interval) -> int:
    """Rewrite chord names that came through as text expressions.

    Only text that reads unambiguously as a chord symbol is touched, so lyrics
    and performance directions are left alone.
    """
    moved = 0
    for element in list(score.recurse().getElementsByClass(expressions.TextExpression)):
        content = element.content or ""
        if not looks_like_chord_symbol(content):
            continue
        rewritten = transpose_chord_symbol_text(content, iv)
        if rewritten != content:
            element.content = rewritten
            moved += 1
    return moved


def _rewrite_key_signatures(score: stream.Stream, simplify_enharmonics: bool) -> int:
    """Normalise key signatures after a transposition.

    music21 already moved them; this pass respells any signature that ended up
    with more than seven accidentals (which cannot be notated) or that has an
    easier enharmonic equivalent.
    """
    rewritten = 0
    for signature in list(score.recurse().getElementsByClass(key.KeySignature)):
        if not isinstance(signature, key.Key):
            if abs(signature.sharps) > 7:
                signature.sharps = _fold_sharps(signature.sharps)
                rewritten += 1
            continue

        replacement = signature
        if abs(signature.sharps) > 7 or (
            simplify_enharmonics and abs(signature.sharps) > 6
        ):
            candidate = simplify_key(signature)
            if candidate.tonic.name != signature.tonic.name:
                replacement = candidate

        if replacement is not signature:
            holder = signature.activeSite
            if holder is not None:
                offset = signature.offset
                holder.remove(signature)
                holder.insert(offset, replacement)
                rewritten += 1
    return rewritten


def _fold_sharps(sharps: int) -> int:
    """Fold an impossible accidental count back into the -7..7 range."""
    while sharps > 7:
        sharps -= 12
    while sharps < -7:
        sharps += 12
    return sharps


def count_pitched_events(score: stream.Stream) -> int:
    """Number of notes and chord tones in a score -- a crude OMR quality proxy."""
    total = 0
    for element in score.recurse().notes:
        if isinstance(element, chord.Chord):
            total += len(element.pitches)
        else:
            total += 1
    return total
