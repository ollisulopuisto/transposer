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
#:
#: This is a floor, not a target. A PDF that is a wrapper around a scanned
#: image has a fixed amount of detail in it, and asking for more dpi than the
#: embedded image holds just interpolates. What actually matters downstream is
#: staff-line spacing in pixels, which :mod:`transposer.preprocess` measures and
#: corrects; this number only needs to be high enough not to throw information
#: away on the way in.
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

    def replace_pages(self, pages: list[Path]) -> None:
        """Swap in a processed version of the rasterised pages."""
        self.pages = list(pages)


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


def native_resolution(pdf_path: Path) -> float | None:
    """The effective dpi of the largest image embedded in a PDF's first page.

    A scan-in-a-PDF has a fixed pixel count; rendering it at a higher dpi than
    this only interpolates. Returns ``None`` for a PDF whose first page is real
    vector content, where resolution is a free choice.
    """
    import pymupdf

    try:
        with pymupdf.open(pdf_path) as document:
            if document.page_count == 0:
                return None
            page = document[0]
            images = page.get_images(full=True)
            if not images:
                return None
            width_inches = page.rect.width / 72
            if width_inches <= 0:
                return None
            widest = max(entry[2] for entry in images)
            return widest / width_inches
    except Exception:  # pragma: no cover - malformed PDFs are not our problem
        return None


#: The resolution Audiveris rasterises PDFs at. It is a fixed constant on its
#: side, so a PDF we build for it has to declare page dimensions that make its
#: render come back out at the pixel size we intended.
PDF_ASSUMED_DPI = 300


def images_to_pdf(
    pages: list[Path], target: Path, assumed_dpi: int = PDF_ASSUMED_DPI
) -> Path:
    """Wrap page images back into a single PDF.

    Engines that read PDFs treat one file as one book, so after preprocessing
    has replaced the pages with enhanced images, rebundling keeps a multi-page
    score together instead of splitting it into one book per page.

    Page geometry matters here. A PDF page is measured in points, and an image
    dropped onto a page sized at 72 dpi declares itself several times larger
    than the paper it came from -- which an engine then re-rasterises at its own
    resolution into something enormous. Sizing each page as if the image were
    ``assumed_dpi`` keeps the round trip close to 1:1.
    """
    import pymupdf
    from PIL import Image

    if not pages:
        raise ValueError("no pages to bundle")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    scale = 72 / assumed_dpi

    with pymupdf.open() as document:
        for page in pages:
            # Pixel dimensions, not PyMuPDF's page rect: opening an image as a
            # document converts it to points using whatever dpi the file's
            # metadata claims, which is not what we are sizing against.
            with Image.open(page) as image:
                width, height = image.size
            sheet = document.new_page(width=width * scale, height=height * scale)
            sheet.insert_image(sheet.rect, filename=str(page))
        document.save(target, deflate=True)
    return target


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
