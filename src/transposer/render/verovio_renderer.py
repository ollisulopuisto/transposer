"""Verovio renderer -- the always-available engraving path.

Verovio ships as a self-contained Python wheel with its own music fonts, so
this backend works on a bare container with no system packages at all. It
renders each page to SVG; CairoSVG turns those into single-page PDFs, which are
then concatenated.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import RenderFailedError
from ..ingest import merge_pdfs
from ..omr.base import Availability
from .base import PageSize, RenderResult, Renderer

#: Verovio options that make a page look like sheet music rather than a demo.
_BASE_OPTIONS: dict[str, object] = {
    "adjustPageHeight": False,
    "adjustPageWidth": False,
    "breaks": "auto",
    "footer": "none",
    "header": "auto",
    "pageMarginTop": 100,
    "pageMarginBottom": 100,
    "pageMarginLeft": 100,
    "pageMarginRight": 100,
    "svgViewBox": True,
    "svgRemoveXlink": True,
    "spacingLinear": 0.25,
    "spacingNonLinear": 0.6,
}


def _ensure_resources(verovio) -> None:
    """Point Verovio at its bundled fonts, in *this* thread.

    Verovio keeps its default resource path in thread-local storage, and the
    Python package only sets it at import time -- which happens on the main
    thread. A toolkit built on a worker thread therefore comes up with no fonts
    and refuses to load anything, so the path is set again on every render.
    """
    from importlib.resources import files

    verovio.setDefaultResourcePath(str(files("verovio") / "data"))


class VerovioRenderer(Renderer):
    name = "verovio"
    description = "Verovio (bundled Python wheel) -- no system dependencies"

    def availability(self) -> Availability:
        try:
            import verovio  # noqa: F401
        except ImportError as exc:  # pragma: no cover - dependency is required
            return Availability(False, f"verovio is not importable: {exc}")
        try:
            import cairosvg  # noqa: F401
        except ImportError as exc:
            return Availability(
                False,
                "cairosvg is not importable (it needs the system Cairo library): "
                f"{exc}",
            )
        return Availability(True)

    def render(
        self,
        musicxml: Path,
        target: Path,
        page_size: PageSize,
        scale: int = 40,
        workdir: Path | None = None,
    ) -> RenderResult:
        self.require_available()

        import cairosvg
        import verovio

        _ensure_resources(verovio)

        musicxml = Path(musicxml)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        workdir = Path(workdir or target.parent / "render")
        workdir.mkdir(parents=True, exist_ok=True)

        toolkit = verovio.toolkit()
        toolkit.setOptions(
            {
                **_BASE_OPTIONS,
                "pageWidth": page_size.width,
                "pageHeight": page_size.height,
                "scale": int(scale),
            }
        )

        if not toolkit.loadFile(str(musicxml)):
            raise RenderFailedError(f"Verovio could not read {musicxml.name}")

        page_count = toolkit.getPageCount()
        if page_count < 1:
            raise RenderFailedError("Verovio rendered zero pages")

        svg_pages: list[Path] = []
        pdf_pages: list[Path] = []

        for index in range(1, page_count + 1):
            svg = toolkit.renderToSVG(index)
            svg_path = workdir / f"page-{index:03d}.svg"
            svg_path.write_text(svg, encoding="utf-8")
            svg_pages.append(svg_path)

            pdf_path = workdir / f"page-{index:03d}.pdf"
            cairosvg.svg2pdf(
                bytestring=svg.encode("utf-8"),
                write_to=str(pdf_path),
                output_width=page_size.width_px,
                output_height=page_size.height_px,
            )
            pdf_pages.append(pdf_path)

        merge_pdfs(pdf_pages, target)

        return RenderResult(
            pdf=target,
            renderer=self.name,
            page_count=page_count,
            svg_pages=svg_pages,
        )
