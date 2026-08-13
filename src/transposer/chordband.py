"""Reading the chord band with Tesseract's LSTM engine.

Audiveris drives Tesseract through its Java bindings and initialises the
*legacy* engine -- the pre-4.0 character classifier. On the condensed fonts used
for chord charts that engine does two things badly: it mangles the symbols it
does find (:mod:`transposer.chordocr` repairs those) and, far more damagingly,
it never proposes most of them at all. A symbol that was never proposed cannot
be repaired, and on a typical chart that is the majority of them.

Tesseract 4 and 5 ship an LSTM line recogniser that is markedly better on short,
isolated tokens in display faces. This module runs it directly, over just the
strip of page where chord symbols live:

    find the staves ──▶ crop the band above each ──▶ OCR it (--oem 1)
        ──▶ keep what the chord grammar accepts ──▶ place it by x position

Two things make this safe to bolt onto recognised output rather than a second
guess at the whole page:

* **The band is not the music.** Cropping to the strip above the top staff line
  removes noteheads, beams and lyrics from what Tesseract is asked to read, so
  the language model is not fighting the score.
* **Nothing is trusted on its own.** Every word goes through the same chord
  grammar the repair pass uses, which rejects prose, and a chord is only placed
  when the page's system count agrees with the score's. A chord in the wrong bar
  is worse than a chord that was never read.

The placement uses systems rather than a global measure index because that is
the alignment that survives disagreement: an engine that missed one barline in
bar 12 still put the right number of systems on the page.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .chordocr import chord_symbol, repair_chord_symbol

#: Characters a chord symbol can be made of. Restricting the LSTM's output
#: alphabet is worth more here than it would be on prose: the band contains
#: almost nothing else, and it stops "Bb" coming back as "Bb." or "8b".
CHORD_WHITELIST = "ABCDEFGabdefgijmnorsu#0123456789+-/()°ø"

#: Tesseract page segmentation modes to try, in order. A chord band is one line
#: of sparse text, and which of these reads it better depends on how far apart
#: the symbols are, so both are tried and the better result kept.
PAGE_SEGMENTATION_MODES = (7, 11)

#: Words below this confidence are dropped before the chord grammar sees them.
#: The grammar is strict, but it is strict about *shape*, and a 5%-confidence
#: "G7" is as likely to be a smudge that happens to fit.
MIN_CONFIDENCE = 30.0

#: How tall the chord band is, in interlines above the top staff line.
BAND_HEIGHT = 2.6
#: And how much clear air to leave between the band and the staff itself, so
#: ledger lines and high noteheads stay out of the crop.
BAND_GAP = 0.2

#: Tesseract's models want text around 30-40 pixels tall. At the interline the
#: preprocessing pass aims for, chord text is smaller than that, so the crop is
#: scaled up before it is handed over.
TARGET_BAND_HEIGHT = 110
MAX_BAND_SCALE = 4.0

#: Chord symbols are written on beats, so the x position is quantised to the
#: beat before it is used as an offset. Engravers space bars by content, and a
#: symbol is drawn a little to the right of the beat it belongs to, so anything
#: finer than a beat would read that spacing as musical intent. This is the
#: fallback for a measure with no time signature in scope.
DEFAULT_BEAT_QUANTUM = 1.0


@dataclass(frozen=True)
class Staff:
    """One five-line staff, in page pixel coordinates."""

    top: int
    bottom: int
    left: int
    right: int
    interline: float


@dataclass(frozen=True)
class BandWord:
    """One word Tesseract returned, with its centre in page coordinates."""

    text: str
    x: int
    confidence: float


@dataclass(frozen=True)
class BandChord:
    """A word from the band that the chord grammar accepted."""

    figure: str
    original: str
    staff_index: int
    x: int
    confidence: float


@dataclass
class StaffChords:
    """Everything read off the band above one staff."""

    staff: Staff
    barlines: list[int] = field(default_factory=list)
    chords: list[BandChord] = field(default_factory=list)


# -- finding the staves ----------------------------------------------------


def find_staves(
    gray: np.ndarray,
    min_line_coverage: float = 0.35,
    max_gap_ratio: float = 1.6,
) -> list[Staff]:
    """Locate every five-line staff on a page, top to bottom.

    Staff lines are the only thing on a page of music that runs nearly its whole
    width, so the row projection of the ink finds them with no model of anything
    else. Runs of such rows are lines; five lines at a consistent spacing are a
    staff.
    """
    if gray.ndim != 2 or min(gray.shape) < 20:
        return []

    binary = gray <= _otsu(gray)
    height, width = binary.shape
    row_ink = binary.sum(axis=1)
    line_rows = row_ink >= min_line_coverage * width
    if not line_rows.any():
        return []

    lines = _group_true_runs(line_rows)
    if len(lines) < 5:
        return []

    staves: list[Staff] = []
    index = 0
    while index + 4 < len(lines):
        group = lines[index : index + 5]
        centres = [(start + end) / 2 for start, end in group]
        gaps = [b - a for a, b in zip(centres, centres[1:])]
        smallest, largest = min(gaps), max(gaps)

        if smallest > 0 and largest <= smallest * max_gap_ratio:
            top = group[0][0]
            bottom = group[-1][1] - 1
            left, right = _horizontal_extent(binary, group)
            if right > left:
                staves.append(
                    Staff(
                        top=int(top),
                        bottom=int(bottom),
                        left=int(left),
                        right=int(right),
                        interline=float(sum(gaps) / len(gaps)),
                    )
                )
            index += 5
            continue
        index += 1

    del height
    return staves


def _group_true_runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Return ``[(start, stop), ...]`` for each run of True, stop exclusive."""
    padded = np.concatenate(([False], flags, [False]))
    changes = np.flatnonzero(np.diff(padded))
    return [(int(start), int(stop)) for start, stop in zip(changes[::2], changes[1::2])]


def _horizontal_extent(binary: np.ndarray, lines: list[tuple[int, int]]) -> tuple[int, int]:
    """Where the staff lines start and end, ignoring stray marks in the margin.

    A column counts as staff only when most of the five lines are inked there,
    which a page number or a fold shadow will not manage.
    """
    rows = np.zeros(binary.shape[0], dtype=bool)
    for start, stop in lines:
        rows[start:stop] = True

    hits = binary[rows].sum(axis=0)
    needed = max(3, round(sum(stop - start for start, stop in lines) * 0.6))
    inked = np.flatnonzero(hits >= needed)
    if inked.size == 0:
        return 0, 0
    return int(inked[0]), int(inked[-1])


def band_box(staves: list[Staff], index: int) -> tuple[int, int, int, int]:
    """The crop rectangle ``(left, top, right, bottom)`` for a staff's band.

    The band stops short of the staff below it and of the staff above, so a
    tight page layout narrows the band rather than letting it swallow a
    neighbouring system's noteheads.
    """
    staff = staves[index]
    gap = round(staff.interline * BAND_GAP)
    bottom = max(0, staff.top - gap)
    top = max(0, bottom - round(staff.interline * BAND_HEIGHT))

    if index > 0:
        # Leave the previous staff's bottom line out of the crop.
        top = max(top, staves[index - 1].bottom + 1)
    if top >= bottom:
        top = max(0, bottom - 1)

    return staff.left, top, staff.right, bottom


# -- barlines --------------------------------------------------------------


def find_barlines(
    gray: np.ndarray, staff: Staff, min_coverage: float = 0.85
) -> list[int]:
    """Columns where a line runs the full height of the staff.

    Stems reach only part of the way, and beams are horizontal, so a column that
    is inked from the top line to the bottom one is a barline.
    """
    binary = gray <= _otsu(gray)
    region = binary[staff.top : staff.bottom + 1, staff.left : staff.right + 1]
    if region.size == 0:
        return []

    height = region.shape[0]
    full = region.sum(axis=0) >= min_coverage * height
    return [
        int(staff.left + (start + stop - 1) / 2) for start, stop in _group_true_runs(full)
    ]


def measure_bounds(
    barlines: list[int], left: float, right: float, count: int
) -> list[float]:
    """Return ``count + 1`` measure boundaries across one system.

    Detected barlines are used when there are exactly as many as the score says
    there should be. When there are not, the system is divided evenly instead:
    engravers space bars by content rather than equally, so this is approximate,
    but it is approximate in the middle of a bar rather than wrong about which
    bar it is -- and the quantisation to beats absorbs the rest.
    """
    if count <= 0:
        return [float(left), float(right)]

    inner = sorted(x for x in barlines if left < x < right)
    if len(inner) == count - 1:
        return [float(left), *(float(x) for x in inner), float(right)]

    step = (right - left) / count
    return [float(left + step * index) for index in range(count + 1)]


# -- reading the band ------------------------------------------------------


def tesseract_binary() -> str | None:
    """The ``tesseract`` executable, or ``None`` when it is not installed."""
    return shutil.which("tesseract")


def tesseract_command(
    image: Path,
    psm: int = 7,
    whitelist: str | None = CHORD_WHITELIST,
    language: str = "eng",
    binary: str | None = None,
) -> list[str]:
    """The command line for one band.

    ``--oem 1`` is the whole point of this module: it selects the LSTM line
    recogniser rather than the legacy classifier Audiveris initialises.
    """
    command = [
        binary or tesseract_binary() or "tesseract",
        str(image),
        "stdout",
        "-l",
        language,
        "--oem",
        "1",
        "--psm",
        str(psm),
    ]
    if whitelist:
        command += ["-c", f"tessedit_char_whitelist={whitelist}"]
    # Tesseract takes its output configuration last, as a config file name.
    command.append("tsv")
    return command


def ocr_band(
    image,
    box: tuple[int, int, int, int],
    workdir: Path,
    name: str,
    whitelist: str | None = CHORD_WHITELIST,
    timeout: float = 60.0,
) -> list[BandWord]:
    """OCR one band and return its words in page coordinates."""
    from PIL import Image

    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return []

    crop = image.crop((left, top, right, bottom)).convert("L")
    scale = 1.0
    if crop.height:
        scale = min(MAX_BAND_SCALE, max(1.0, TARGET_BAND_HEIGHT / crop.height))
    if scale > 1.01:
        crop = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS
        )

    # Tesseract's layout analysis wants quiet space around the text.
    padding = 20
    padded = Image.new("L", (crop.width + padding * 2, crop.height + padding * 2), 255)
    padded.paste(crop, (padding, padding))

    workdir.mkdir(parents=True, exist_ok=True)
    band_path = workdir / f"{name}.png"
    padded.save(band_path)

    passes = []
    for psm in PAGE_SEGMENTATION_MODES:
        passes.append(
            [
                BandWord(
                    text=word.text,
                    x=int(left + (word.x - padding) / scale),
                    confidence=word.confidence,
                )
                for word in _run_tesseract(band_path, psm, whitelist, timeout)
            ]
        )

    window = max(4, round((bottom - top) * 0.5))
    return merge_readings(passes, window=window)


def merge_readings(passes: list[list[BandWord]], window: int) -> list[BandWord]:
    """Combine what several segmentation modes read off the same band.

    Neither mode wins everywhere -- one reads a symbol standing alone better,
    the other one crowded against its neighbour -- so rather than picking a mode
    per band, readings within ``window`` pixels of each other are treated as the
    same symbol and the best one kept. "Best" is: something the chord grammar
    accepts beats something it does not, whatever the confidence, because a
    confident "Refrain" is still not a chord.
    """
    ranked = sorted(
        (word for words in passes for word in words),
        key=_reading_rank,
        reverse=True,
    )

    kept: list[BandWord] = []
    for word in ranked:
        if any(abs(word.x - other.x) <= window for other in kept):
            continue
        kept.append(word)

    kept.sort(key=lambda word: word.x)
    return kept


def _reading_rank(word: BandWord) -> tuple[int, float, int]:
    readable = 1 if repair_chord_symbol(word.text).repaired else 0
    return readable, word.confidence, len(word.text)


def _run_tesseract(
    image: Path, psm: int, whitelist: str | None, timeout: float
) -> list[BandWord]:
    """Run one pass and parse the TSV it writes to stdout."""
    if tesseract_binary() is None:
        return []

    try:
        completed = subprocess.run(
            tesseract_command(image, psm=psm, whitelist=whitelist),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    return parse_tsv(completed.stdout)


def parse_tsv(text: str) -> list[BandWord]:
    """Parse Tesseract's TSV output into words, keeping their centre x."""
    words: list[BandWord] = []
    lines = text.splitlines()
    if not lines:
        return words

    header = lines[0].split("\t")
    try:
        left_at = header.index("left")
        width_at = header.index("width")
        conf_at = header.index("conf")
        text_at = header.index("text")
    except ValueError:
        return words

    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) <= text_at:
            continue
        content = fields[text_at].strip()
        if not content:
            continue
        try:
            left = int(fields[left_at])
            width = int(fields[width_at])
            confidence = float(fields[conf_at])
        except ValueError:
            continue
        if confidence < 0:
            continue
        words.append(
            BandWord(text=content, x=left + width // 2, confidence=confidence)
        )
    return words


def words_to_chords(
    words: list[BandWord],
    staff_index: int,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[BandChord]:
    """Keep the words the chord grammar accepts, in left-to-right order."""
    chords: list[BandChord] = []
    for word in words:
        if word.confidence < min_confidence:
            continue
        repair = repair_chord_symbol(word.text)
        if not repair.repaired:
            continue
        chords.append(
            BandChord(
                figure=repair.repaired,
                original=word.text,
                staff_index=staff_index,
                x=word.x,
                confidence=word.confidence,
            )
        )
    chords.sort(key=lambda chord: chord.x)
    return chords


def read_page(
    page: Path | str,
    workdir: Path | str | None = None,
    whitelist: str | None = CHORD_WHITELIST,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[BandChord]:
    """Every chord symbol the LSTM engine can read off one page image."""
    return [chord for staff in read_page_staves(
        page, workdir=workdir, whitelist=whitelist, min_confidence=min_confidence
    ) for chord in staff.chords]


def read_page_staves(
    page: Path | str,
    workdir: Path | str | None = None,
    whitelist: str | None = CHORD_WHITELIST,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[StaffChords]:
    """Read one page, keeping each staff's chords with its geometry."""
    import tempfile

    from PIL import Image

    page = Path(page)
    image = Image.open(page).convert("L")
    gray = np.asarray(image)

    staves = find_staves(gray)
    if not staves:
        return []

    temporary = None
    if workdir is None:
        temporary = tempfile.TemporaryDirectory(prefix="transposer-band-")
        workdir = Path(temporary.name)
    workdir = Path(workdir)

    try:
        results: list[StaffChords] = []
        for index, staff in enumerate(staves):
            words = ocr_band(
                image,
                band_box(staves, index),
                workdir,
                name=f"{page.stem}-band{index}",
                whitelist=whitelist,
            )
            results.append(
                StaffChords(
                    staff=staff,
                    barlines=find_barlines(gray, staff),
                    chords=words_to_chords(
                        words, staff_index=index, min_confidence=min_confidence
                    ),
                )
            )
        return results
    finally:
        if temporary is not None:
            temporary.cleanup()


# -- putting the chords into the score -------------------------------------


def score_systems(score) -> list[list]:
    """The score's measures, grouped the way the engraver broke them into lines.

    MusicXML records a system break as ``<print new-system="yes">``, which is
    what an OMR engine writes when it starts reading a new line of the page. A
    score with no breaks at all is treated as one system, which is right for the
    single-line snippets the tests and the passthrough engine produce.
    """
    from music21 import layout, stream

    part = None
    for candidate in score.getElementsByClass(stream.Part):
        if list(candidate.getElementsByClass(stream.Measure)):
            part = candidate
            break
    if part is None:
        return []

    systems: list[list] = []
    current: list = []
    for measure in part.getElementsByClass(stream.Measure):
        starts_system = any(
            getattr(item, "isNew", False)
            for item in measure.getElementsByClass(layout.SystemLayout)
        )
        if starts_system and current:
            systems.append(current)
            current = []
        current.append(measure)
    if current:
        systems.append(current)
    return systems


def apply_chords(score, page: list[StaffChords]) -> tuple[int, list[str]]:
    """Insert chords read off the page into the score they were recognised from.

    Returns the number added and any notes for the report. Nothing is added
    unless the staves on the page divide evenly into the score's systems, since
    that is the evidence that the two agree about the page at all.
    """
    from music21 import harmony

    systems = score_systems(score)
    if not systems:
        return 0, ["the recognised score has no measures, so no chords were placed"]

    staves = [entry for entry in page if entry.staff is not None]
    if not staves:
        return 0, []

    if len(staves) % len(systems) != 0:
        return 0, [
            f"the chord-band pass found {len(staves)} staff/staves on the page but "
            f"the recognised score has {len(systems)} system(s), so the chord "
            "symbols it read were not placed"
        ]

    per_system = len(staves) // len(systems)
    added = 0
    notes: list[str] = []

    for system_index, measures in enumerate(systems):
        # The chord band belongs to the top staff of a system; on a grand staff
        # the lower one carries the left hand, not the chart.
        entry = staves[system_index * per_system]
        if not entry.chords:
            continue

        bounds = measure_bounds(
            entry.barlines,
            left=entry.staff.left,
            right=entry.staff.right,
            count=len(measures),
        )

        for chord in entry.chords:
            index = _segment_index(bounds, chord.x)
            if index is None:
                continue
            measure = measures[index]
            span = bounds[index + 1] - bounds[index]
            if span <= 0:
                continue

            duration = float(measure.barDuration.quarterLength)
            quantum = _beat_quantum(measure)
            offset = (chord.x - bounds[index]) / span * duration
            offset = min(
                max(0.0, round(offset / quantum) * quantum),
                max(0.0, duration - quantum),
            )

            present = list(measure.getElementsByClass(harmony.ChordSymbol))
            if any(symbol.figure == chord.figure for symbol in present):
                continue
            if any(
                abs(float(symbol.offset) - offset) < quantum for symbol in present
            ):
                # Something is already written on that beat, and two chord
                # symbols on one beat is an artefact rather than a chart.
                continue

            symbol = chord_symbol(chord.figure)
            if symbol is None:
                continue
            measure.insert(offset, symbol)
            added += 1

    if added:
        notes.append(
            f"read {added} more chord symbol(s) off the page with Tesseract's LSTM "
            "engine, which the recognition pass had missed entirely"
        )
    return added, notes


def _beat_quantum(measure) -> float:
    """The length of one beat in the measure, in quarter notes."""
    from music21 import meter

    signature = measure.timeSignature or measure.getContextByClass(meter.TimeSignature)
    if signature is None:
        return DEFAULT_BEAT_QUANTUM
    try:
        beat = float(signature.beatDuration.quarterLength)
    except Exception:
        return DEFAULT_BEAT_QUANTUM
    return beat if beat > 0 else DEFAULT_BEAT_QUANTUM


def _segment_index(bounds: list[float], x: float) -> int | None:
    """Which measure a page x position falls in."""
    if len(bounds) < 2:
        return None
    if x < bounds[0] or x > bounds[-1]:
        # A symbol written out in the margin belongs to the nearest bar; a
        # symbol far outside the staff is not a chord for this system at all.
        slack = (bounds[-1] - bounds[0]) * 0.05
        if x < bounds[0] - slack or x > bounds[-1] + slack:
            return None
    for index in range(len(bounds) - 1):
        if x < bounds[index + 1]:
            return index
    return len(bounds) - 2


def read_and_apply(
    score,
    pages: list[Path],
    workdir: Path | None = None,
    whitelist: str | None = CHORD_WHITELIST,
    min_confidence: float = MIN_CONFIDENCE,
) -> tuple[int, list[str]]:
    """Run the band pass over every page of a scan and merge what it read."""
    if tesseract_binary() is None:
        return 0, [
            "the chord-band pass needs the tesseract binary on PATH; it did not run"
        ]

    staves: list[StaffChords] = []
    for page in pages:
        staves.extend(
            read_page_staves(
                page,
                workdir=workdir,
                whitelist=whitelist,
                min_confidence=min_confidence,
            )
        )
    if not staves:
        return 0, ["the chord-band pass found no staves on the page"]

    return apply_chords(score, staves)


# -- numeric helpers -------------------------------------------------------


def _otsu(gray: np.ndarray) -> int:
    """Otsu's threshold. Shared shape with :mod:`transposer.preprocess`."""
    from .preprocess import _otsu as threshold

    return threshold(gray)
