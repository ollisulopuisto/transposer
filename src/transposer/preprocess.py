"""Getting a scanned page into the shape an OMR engine wants.

Recognition quality is governed less by a scan's nominal dpi than by one
number: **interline**, the distance in pixels between two staff lines.
Audiveris' filters are written in fractions of interline and its defaults
assume roughly 20 pixels; Tesseract's models want text around 30 pixels tall.
A page that is geometrically fine at 144 dpi can still have an interline of 7,
and at that size everything downstream is working near its rounding limits.

So this module does not resample to a dpi figure. It *measures* the interline
and scales to hit a target, which is the same decision a person makes when they
zoom a scan until the staff "looks right" before tracing it.

Nothing here invents detail that is not in the pixels. Upscaling a 144 dpi scan
does not recover what the scanner missed; it puts the information that *is*
there on a grid the downstream code handles well, and sharpening undoes the
blur that resampling introduces. Those are real, measurable gains, and they are
also the whole of what preprocessing can do.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

#: Interline, in pixels, that Audiveris is happiest with.
TARGET_INTERLINE = 20

#: Never blow a page up beyond this. Past about 4x the resampling is pure
#: interpolation and only costs time and memory.
MAX_SCALE = 4.0

#: Nor past this many pixels on a side, to keep the JVM's heap out of trouble.
MAX_DIMENSION = 10000


@dataclass
class PreprocessReport:
    """What the enhancement pass measured and did."""

    source_size: tuple[int, int] = (0, 0)
    output_size: tuple[int, int] = (0, 0)
    measured_interline: int | None = None
    line_thickness: int | None = None
    scale: float = 1.0
    skew_degrees: float = 0.0
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.measured_interline:
            resulting = round(self.measured_interline * self.scale)
            bits.append(
                f"interline {self.measured_interline}px → {resulting}px"
            )
        bits.append(f"scale {self.scale:.2f}x")
        if abs(self.skew_degrees) >= 0.05:
            bits.append(f"deskewed {self.skew_degrees:+.2f}°")
        bits.append(f"{self.source_size[0]}×{self.source_size[1]} → "
                    f"{self.output_size[0]}×{self.output_size[1]}")
        if self.steps:
            bits.append("steps: " + ", ".join(self.steps))
        return "; ".join(bits)


def measure_staff_geometry(gray: np.ndarray) -> tuple[int | None, int | None]:
    """Return ``(interline, line_thickness)`` in pixels, or ``(None, None)``.

    Uses the standard run-length trick: walk down each column and record the
    length of each black run and the white run that follows it. Over a page of
    music, the most common such pair is a staff line and the gap above the next
    one, because nothing else on the page repeats that regularly.
    """
    # "<=" not "<": Otsu returns the highest value that still belongs to the
    # dark class, so on a pure black-and-white page the threshold is 0 and a
    # strict comparison would find no ink at all.
    binary = gray <= _otsu(gray)
    height, width = binary.shape
    if height < 20 or width < 20:
        return None, None

    # Sampling every few columns is plenty and keeps this fast on big pages.
    step = max(1, width // 400)
    pairs: Counter[tuple[int, int]] = Counter()

    for column_index in range(0, width, step):
        column = binary[:, column_index]
        runs = _run_lengths(column)
        for index in range(len(runs) - 1):
            value, length = runs[index]
            next_value, next_length = runs[index + 1]
            if value and not next_value:  # black run followed by white run
                if 0 < length <= 20 and 0 < next_length <= 80:
                    pairs[(length, next_length)] += 1

    if not pairs:
        return None, None

    (thickness, gap), count = pairs.most_common(1)[0]
    if count < 20:
        return None, None

    # "Interline" is conventionally measured line-centre to line-centre.
    return thickness + gap, thickness


def estimate_skew(gray: np.ndarray, limit: float = 3.0, step: float = 0.25) -> float:
    """Estimate page skew in degrees by maximising horizontal projection contrast.

    Staff lines make a sharply peaked row-projection when they are level and a
    smeared one when they are not, so the angle whose projection has the highest
    variance is the one that levels the page.
    """
    from PIL import Image

    # Ink white on a black page, so rotation can fill the corners with "no ink"
    # and the row projection measures ink only.
    ink = (gray <= _otsu(gray)).astype(np.uint8) * 255
    if (ink > 0).mean() < 0.001:
        # A blank page has nothing to level, and every angle scores the same --
        # which would otherwise hand back whichever one was tried first.
        return 0.0
    image = Image.fromarray(ink)
    # Work on a reduced copy: skew is a global property and this is much faster.
    if max(image.size) > 1200:
        image.thumbnail((1200, 1200), Image.BILINEAR)

    best_angle, best_score = 0.0, -1.0
    angle = -limit
    while angle <= limit + 1e-9:
        rotated = image.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
        projection = np.asarray(rotated, dtype=np.float64).sum(axis=1)
        score = float(projection.var())
        if score > best_score:
            best_angle, best_score = angle, score
        angle += step
    return best_angle


def enhance(
    source: Path | str,
    target: Path | str,
    target_interline: int = TARGET_INTERLINE,
    deskew: bool = True,
    sharpen: bool = True,
    binarize: bool = False,
    max_scale: float = MAX_SCALE,
) -> PreprocessReport:
    """Enhance one page image and write it to ``target``.

    The steps, in order: measure the staff geometry, level the page, scale so
    the interline lands near ``target_interline``, and restore the edges that
    resampling softened.
    """
    from PIL import Image, ImageFilter, ImageOps

    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    image = Image.open(source)
    if image.mode in {"RGBA", "LA", "PA"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.convert("RGBA").split()[-1])
        image = background
    image = image.convert("L")

    report = PreprocessReport(source_size=image.size)
    gray = np.asarray(image)

    interline, thickness = measure_staff_geometry(gray)
    report.measured_interline = interline
    report.line_thickness = thickness
    if interline is None:
        report.notes.append(
            "could not find staff lines to measure; scaling was left alone"
        )

    if deskew:
        angle = estimate_skew(gray)
        report.skew_degrees = angle
        if abs(angle) >= 0.25:
            image = image.rotate(
                angle, resample=Image.BICUBIC, expand=True, fillcolor=255
            )
            report.steps.append(f"deskew {angle:+.2f}°")

    # Flatten the background so a grey scan gets a white page behind the ink.
    image = ImageOps.autocontrast(image, cutoff=(0, 2))
    report.steps.append("autocontrast")

    scale = 1.0
    if interline:
        scale = target_interline / interline
        scale = max(1.0, min(scale, max_scale))
        largest = max(image.size) * scale
        if largest > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(image.size)
            report.notes.append(
                f"scale capped at {scale:.2f}x to stay under {MAX_DIMENSION}px"
            )
    report.scale = scale

    if scale > 1.01:
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.LANCZOS)
        report.steps.append(f"upscale {scale:.2f}x (Lanczos)")

        if sharpen:
            # Radius tracks the scale factor: the blur resampling introduces is
            # proportional to how far the pixels were spread apart.
            radius = max(1.0, scale / 2)
            image = image.filter(
                ImageFilter.UnsharpMask(radius=radius, percent=120, threshold=2)
            )
            report.steps.append(f"unsharp mask (r={radius:.1f})")

    if binarize:
        image = Image.fromarray(_sauvola(np.asarray(image)))
        report.steps.append("Sauvola binarisation")

    report.output_size = image.size
    image.save(target)
    return report


def enhance_pages(
    pages: list[Path],
    out_dir: Path,
    **options,
) -> tuple[list[Path], list[PreprocessReport]]:
    """Enhance a list of page images into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    reports: list[PreprocessReport] = []
    for page in pages:
        destination = out_dir / f"{Path(page).stem}-enhanced.png"
        reports.append(enhance(page, destination, **options))
        written.append(destination)
    return written, reports


# -- numeric helpers ------------------------------------------------------


def _run_lengths(column: np.ndarray) -> list[tuple[bool, int]]:
    """Run-length encode a boolean column as ``[(value, length), ...]``."""
    if column.size == 0:
        return []
    changes = np.flatnonzero(np.diff(column)) + 1
    boundaries = np.concatenate(([0], changes, [column.size]))
    return [
        (bool(column[start]), int(end - start))
        for start, end in zip(boundaries[:-1], boundaries[1:])
    ]


def _otsu(gray: np.ndarray) -> int:
    """Otsu's threshold, so we do not need scikit-image for one function."""
    histogram = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = histogram.sum()
    if total == 0:
        return 128

    levels = np.arange(256)
    weight_background = np.cumsum(histogram)
    weight_foreground = total - weight_background

    sum_total = float((histogram * levels).sum())
    sum_background = np.cumsum(histogram * levels)

    valid = (weight_background > 0) & (weight_foreground > 0)
    if not valid.any():
        return 128

    mean_background = np.divide(
        sum_background, weight_background, out=np.zeros(256), where=valid
    )
    mean_foreground = np.divide(
        sum_total - sum_background, weight_foreground, out=np.zeros(256), where=valid
    )
    between = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
    between[~valid] = -1
    return int(np.argmax(between))


def _sauvola(gray: np.ndarray, window: int = 25, k: float = 0.2, r: float = 128.0) -> np.ndarray:
    """Sauvola adaptive binarisation, computed with integral images.

    Better than a global threshold on a scan with uneven lighting, because the
    threshold follows the local mean and standard deviation instead of assuming
    one value works for the whole page.
    """
    image = gray.astype(np.float64)
    padding = window // 2
    padded = np.pad(image, padding + 1, mode="edge")

    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral_squared = (padded**2).cumsum(axis=0).cumsum(axis=1)

    height, width = image.shape
    rows = np.arange(height)
    columns = np.arange(width)
    top = rows[:, None]
    left = columns[None, :]
    bottom = top + window
    right = left + window

    def box(sums: np.ndarray) -> np.ndarray:
        return (
            sums[bottom, right]
            - sums[top, right]
            - sums[bottom, left]
            + sums[top, left]
        )

    area = float(window * window)
    mean = box(integral) / area
    mean_square = box(integral_squared) / area
    variance = np.clip(mean_square - mean**2, 0, None)
    deviation = np.sqrt(variance)

    threshold = mean * (1 + k * (deviation / r - 1))
    return np.where(image > threshold, 255, 0).astype(np.uint8)
