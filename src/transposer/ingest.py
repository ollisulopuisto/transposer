"""Turning whatever the user uploaded into something an OMR engine can read."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import UnsupportedInputError

#: Raster formats we hand straight to the image-based OMR engines.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
#: Score formats that need no recognition at all.
SCORE_SUFFIXES = {".xml", ".musicxml", ".mxl", ".mid", ".midi", ".abc", ".krn"}
PDF_SUFFIXES = {".pdf"}

#: Resolution used when rasterising a PDF for engines that only take images.
#: 300 dpi is the sweet spot: below ~200 staff lines start merging, above ~400
#: recognition gets slower without getting better.
DEFAULT_DPI = 300


@dataclass
class IngestedInput:
    """A normalised view of the user's file.

    ``kind`` is one of ``"score"``, ``"pdf"`` or ``"image"``. ``pages`` is
    populated lazily by :meth:`rasterize`, so engines that read PDFs natively
    never pay for the conversion.
    """

    path: Path
    kind: str
    workdir: Path
    pages: list[Path] = field(default_factory=list)

    @property
    def is_score(self) -> bool:
        return self.kind == "score"

    def rasterize(self, dpi: int = DEFAULT_DPI) -> list[Path]:
        """Return the input as a list of PNG pages, converting if needed."""
        if self.pages:
            return self.pages

        if self.kind == "image":
            self.pages = [_normalise_image(self.path, self.workdir)]
        elif self.kind == "pdf":
            self.pages = pdf_to_images(self.path, self.workdir / "pages", dpi=dpi)
        else:
            raise UnsupportedInputError(
                f"{self.path.name} is a score file, not something to rasterize"
            )
        return self.pages


def classify(path: Path) -> str:
    """Decide which of the three input families a file belongs to."""
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in SCORE_SUFFIXES:
        return "score"
    if suffix == ".zip" and _looks_like_mxl(path):
        return "score"
    raise UnsupportedInputError(
        f"don't know how to read {path.name}; expected a PDF, an image "
        f"({', '.join(sorted(IMAGE_SUFFIXES))}) or a score file "
        f"({', '.join(sorted(SCORE_SUFFIXES))})"
    )


def ingest(path: Path, workdir: Path) -> IngestedInput:
    """Classify ``path`` and copy it into ``workdir`` so the original is safe."""
    path = Path(path)
    if not path.is_file():
        raise UnsupportedInputError(f"no such file: {path}")

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    kind = classify(path)
    local = workdir / _safe_name(path.name)
    if local.resolve() != path.resolve():
        shutil.copy2(path, local)

    return IngestedInput(path=local, kind=kind, workdir=workdir)


def pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Rasterise every page of a PDF to a PNG."""
    import pymupdf  # imported lazily: it is the heaviest dependency we have

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    with pymupdf.open(pdf_path) as document:
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
            target = out_dir / f"page-{index:03d}.png"
            pixmap.save(target)
            written.append(target)
    if not written:
        raise UnsupportedInputError(f"{pdf_path.name} has no pages")
    return written


def merge_pdfs(parts: list[Path], target: Path) -> Path:
    """Concatenate single-page PDFs into one document."""
    import pymupdf

    if not parts:
        raise ValueError("nothing to merge")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open() as merged:
        for part in parts:
            with pymupdf.open(part) as piece:
                merged.insert_pdf(piece)
        merged.save(target)
    return target


def _normalise_image(path: Path, workdir: Path) -> Path:
    """Force an image to 8-bit RGB PNG.

    Several OMR code paths (Mozart's in particular) assume three channels and
    crash on RGBA screenshots or paletted GIFs, so flatten transparency onto
    white and drop any alpha channel up front.
    """
    from PIL import Image

    target = workdir / "pages" / f"{path.stem}-rgb.png"
    target.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(path) as image:
        if image.mode in {"RGBA", "LA", "PA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.convert("RGBA").split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        image.save(target)
    return target


def _looks_like_mxl(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return "META-INF/container.xml" in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _safe_name(name: str) -> str:
    """Strip anything that could escape the working directory."""
    cleaned = Path(name).name.replace("\x00", "")
    return cleaned or "input"
