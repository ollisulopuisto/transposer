"""Repairing the mistakes optical music recognition reliably makes.

OMR output is never clean, but its mistakes are not random -- a handful of them
show up on almost every scan, and each has a safe automatic fix:

* chord symbols the text recogniser mangled into ``Fm?`` or ``CT``, which on a
  lead sheet are the whole point (see :mod:`transposer.chordocr`),
* a plain treble clef read as an octave-displaced one, which silently moves a
  whole part an octave,
* a one- or two-bar "modulation" caused by a smudge next to a barline, which a
  transposition then faithfully propagates,
* stray OCR fragments promoted to page credits, which pile up over the first
  system,
* a movement title that never reaches the title field.

Each pass here is separately switchable and reports what it changed, because a
cleanup that silently edits music is worse than no cleanup at all.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from music21 import clef, key, stream


@dataclass
class CleanupReport:
    """What the cleanup passes changed."""

    chords_promoted: int = 0
    clefs_flattened: int = 0
    key_changes_dropped: int = 0
    credits_removed: int = 0
    text_removed: int = 0
    lyrics_attached: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.chords_promoted
            or self.clefs_flattened
            or self.key_changes_dropped
            or self.credits_removed
            or self.text_removed
            or self.lyrics_attached
        )


def flatten_octave_clefs(score: stream.Stream) -> int:
    """Turn octave-displaced clefs into their plain equivalents.

    A treble-8vb clef is a real thing (tenor voices use it), but an OMR engine
    inventing one is far more common than a scan actually containing one, and
    the consequence -- an entire part sounding an octave off -- is bad enough
    to be worth correcting by default.

    The notes under the clef move with it. MusicXML stores sounding pitches, so
    dropping the displacement without shifting the notes would leave them
    drawn an octave below where the scan had them, on a ladder of ledger lines.
    """
    changed = 0
    for part in _parts_of(score):
        events = sorted(
            (
                (element.getOffsetInHierarchy(part), element)
                for element in part.recurse().getElementsByClass(clef.Clef)
            ),
            key=lambda pair: pair[0],
        )
        for index, (offset, element) in enumerate(events):
            octave_change = getattr(element, "octaveChange", 0) or 0
            if not octave_change:
                continue

            end = events[index + 1][0] if index + 1 < len(events) else None
            _shift_notes(part, offset, end, -octave_change)

            replacement = clef.clefFromString(f"{element.sign}{element.line}")
            holder = element.activeSite
            if holder is None:
                element.octaveChange = 0
            else:
                local_offset = element.offset
                holder.remove(element)
                holder.insert(local_offset, replacement)
            changed += 1
    return changed


def _shift_notes(
    part: stream.Stream, start: float, end: float | None, octaves: int
) -> None:
    """Move every note in ``[start, end)`` by whole octaves."""
    if not octaves:
        return
    from music21 import interval, note

    # A chromatic interval ("12 semitones") respells flats as sharps on the
    # way; the named perfect octave keeps every accidental exactly as written.
    step = interval.Interval("P8" if octaves > 0 else "P-8")
    for element in part.recurse().getElementsByClass(note.NotRest):
        offset = element.getOffsetInHierarchy(part)
        if offset < start:
            continue
        if end is not None and offset >= end:
            continue
        for _ in range(abs(octaves)):
            element.transpose(step, inPlace=True)


def drop_spurious_key_changes(
    score: stream.Stream,
    mode: str = "auto",
    min_measures: int = 4,
) -> tuple[int, list[str]]:
    """Remove mid-score key signature changes that look like recognition noise.

    ``mode`` is one of:

    ``keep``
        Change nothing.
    ``drop``
        Keep only the first key signature in each part.
    ``auto``
        Drop a change that reverts to the previous signature within
        ``min_measures`` measures. A real modulation stays; a smudge that
        invented one sharp for two bars does not.
    """
    if mode == "keep":
        return 0, []

    removed = 0
    notes: list[str] = []

    for part in _parts_of(score):
        measures = list(part.getElementsByClass(stream.Measure))
        entries: list[tuple[int, stream.Measure, key.KeySignature]] = []
        for index, measure in enumerate(measures):
            for signature in measure.getElementsByClass(key.KeySignature):
                entries.append((index, measure, signature))

        if len(entries) < 2:
            continue

        doomed: list[tuple[stream.Measure, key.KeySignature]] = []

        if mode == "drop":
            doomed = [(m, s) for _, m, s in entries[1:]]
        elif mode == "auto":
            for position, (index, measure, signature) in enumerate(entries):
                if position == 0:
                    continue
                previous = entries[position - 1][2]
                if signature.sharps == previous.sharps:
                    # A restatement of the same signature is redundant, not wrong.
                    doomed.append((measure, signature))
                    continue
                following = entries[position + 1] if position + 1 < len(entries) else None
                if following is None:
                    continue
                reverts = following[2].sharps == previous.sharps
                span = following[0] - index
                if reverts and span < min_measures:
                    doomed.append((measure, signature))
        else:
            raise ValueError(f"unknown key-change mode {mode!r}")

        for measure, signature in doomed:
            measure.remove(signature)
            removed += 1
            number = measure.number if measure.number is not None else "?"
            notes.append(
                f"dropped a key signature change to {signature.sharps:+d} "
                f"accidental(s) at measure {number}"
            )

    return removed, _dedupe(notes)


def strip_credits(musicxml: Path, keep_texts: Iterable[str] = ()) -> int:
    """Delete stray ``<credit>`` blocks from an exported MusicXML file.

    Audiveris promotes any text it finds near the page edge to a credit, so a
    misread lyric or a page number ends up stacked above the first system.
    Only credits whose text matches something we deliberately kept -- normally
    the title and the composer -- survive; pass no texts to drop them all and
    let the renderer draw the header from the score's metadata instead.

    Returns the number of credits removed.
    """
    musicxml = Path(musicxml)
    if musicxml.suffix.lower() == ".mxl":
        return 0

    try:
        tree = ET.parse(musicxml)
    except ET.ParseError:
        return 0

    wanted = {_normalise(text) for text in keep_texts if text}
    root = tree.getroot()

    removed = 0
    for element in list(root.findall("credit")):
        text = _normalise(" ".join(w.text or "" for w in element.findall("credit-words")))
        if text and text in wanted:
            continue
        root.remove(element)
        removed += 1

    if removed:
        tree.write(musicxml, encoding="utf-8", xml_declaration=True)
    return removed


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def drop_unparsed_text(score: stream.Stream, keep_chords: bool = True) -> int:
    """Remove floating text expressions that OMR could not make sense of.

    Lyrics an engine failed to attach to notes come through as free text and
    land on top of the staff. When the OCR is unusable, deleting it produces a
    cleaner chart than leaving it in.
    """
    from music21 import expressions

    from .chordtext import looks_like_chord_symbol

    removed = 0
    for element in list(score.recurse().getElementsByClass(expressions.TextExpression)):
        content = (element.content or "").strip()
        if keep_chords and looks_like_chord_symbol(content):
            continue
        holder = element.activeSite
        if holder is None:
            continue
        holder.remove(element)
        removed += 1
    return removed


def ensure_title(score: stream.Score) -> None:
    """Promote a movement name into the title when the title is empty.

    Audiveris writes the piece's name to ``movement-title``; renderers look at
    ``work-title``, so a recognised title otherwise vanishes.
    """
    metadata = score.metadata
    if metadata is None:
        return
    if metadata.title:
        return
    movement = getattr(metadata, "movementName", None)
    if movement:
        metadata.title = movement


def scrub_metadata(score: stream.Score, drop_garbled_composer: bool = True) -> list[str]:
    """Drop metadata values that are obviously OCR noise.

    A composer field of ``I'IflroIdArkn`` helps nobody; an empty one at least
    does not claim something false.
    """
    notes: list[str] = []
    metadata = score.metadata
    if metadata is None or not drop_garbled_composer:
        return notes

    composer = (metadata.composer or "").strip()
    if composer and _looks_garbled(composer):
        metadata.composer = None
        notes.append(f"removed an unreadable composer name ({composer!r})")
    return notes


def clean_score(
    score: stream.Score,
    plain_clefs: bool = True,
    key_changes: str = "auto",
    drop_text: bool = False,
    fix_metadata: bool = True,
    repair_chords: bool = True,
    attach_text_lyrics: bool = True,
) -> CleanupReport:
    """Run the in-memory cleanup passes and report what changed."""
    report = CleanupReport()

    if repair_chords:
        from .chordocr import promote_chord_symbols

        report.chords_promoted, chord_notes = promote_chord_symbols(score)
        if report.chords_promoted:
            report.notes.append(
                f"recovered {report.chords_promoted} chord symbol(s) from text the "
                "recogniser could not parse"
            )
            report.notes.extend(chord_notes)

    if plain_clefs:
        report.clefs_flattened = flatten_octave_clefs(score)
        if report.clefs_flattened:
            report.notes.append(
                f"replaced {report.clefs_flattened} octave-displaced clef(s) with "
                "plain ones (use --keep-clefs if the score really uses them)"
            )

    dropped, notes = drop_spurious_key_changes(score, mode=key_changes)
    report.key_changes_dropped = dropped
    report.notes += notes

    if attach_text_lyrics:
        from .lyrics import attach_lyrics, drop_debris

        report.lyrics_attached = attach_lyrics(score)
        if report.lyrics_attached:
            report.notes.append(
                f"attached {report.lyrics_attached} piece(s) of recognised text to "
                "the notes they sit under, as lyrics; they were floating words "
                "carrying coordinates from the page they were read off, which do "
                "not survive re-engraving"
            )

        debris = drop_debris(score)
        if debris:
            report.text_removed += debris
            report.notes.append(
                f"removed {debris} one- or two-character fragment(s) left above "
                "the staff by the text recogniser"
            )

    if drop_text:
        removed = drop_unparsed_text(score)
        report.text_removed += removed
        if removed:
            report.notes.append(
                f"removed {removed} unreadable text item(s) from the page"
            )

    if fix_metadata:
        ensure_title(score)
        report.notes += scrub_metadata(score)

    return report


#: Characters a real name can contain. Anything else -- typographic ligatures
#: like "ﬂ", stray box-drawing, mangled punctuation -- means Tesseract guessed.
_GARBLE = re.compile(r"[^A-Za-zÀ-ÿ0-9 .,'&/()\-]")


def _looks_garbled(text: str) -> bool:
    """Crude test for OCR mush.

    Deliberately conservative: it should never fire on a real name, because the
    cost of a false positive is deleting information the scan actually had.
    """
    if _GARBLE.search(text):
        return True
    words = [w for w in re.split(r"[\s.,]+", text) if w]
    for word in words:
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", word)
        if len(letters) >= 4 and not re.search(r"[aeiouyAEIOUYàèéêëïîôùûü]", letters):
            return True
        # Case flipping mid-word ("IﬂroIdArkn") is a strong OCR tell.
        if len(letters) >= 4 and len(re.findall(r"[a-z][A-Z]", letters)) >= 2:
            return True
    return False


def _parts_of(score: stream.Stream) -> list[stream.Stream]:
    parts = list(score.getElementsByClass(stream.Part))
    return parts or [score]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
