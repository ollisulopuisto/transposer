"""Verovio renderer -- the always-available engraving path.

Verovio ships as a self-contained Python wheel with its own music fonts, so
this backend works on a bare container with no system packages at all. It
renders each page to SVG; CairoSVG turns those into single-page PDFs, which are
then concatenated.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..errors import RenderFailedError
from ..ingest import merge_pdfs
from ..omr.base import Availability
from .base import PageSize, Renderer, RenderResult

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


#: Verovio draws the accidental inside a chord symbol as a character from its
#: music font rather than as a path, and CairoSVG has no music font to resolve
#: it with -- so "F#m7" comes out as "F□m7". These are the plain Unicode
#: equivalents, keyed by Verovio's own glyph names so the mapping survives a
#: change of music font.
_TEXT_GLYPH_EQUIVALENTS = {
    "figbassFlat": "♭",
    "figbassDoubleFlat": "♭♭",
    "figbassNatural": "♮",
    "figbassSharp": "♯",
    "figbassDoubleSharp": "♯♯",
    "accidentalFlat": "♭",
    "accidentalDoubleFlat": "♭♭",
    "accidentalNatural": "♮",
    "accidentalSharp": "♯",
    "accidentalDoubleSharp": "♯♯",
    "csymDiminished": "°",
    "csymHalfDiminished": "ø",
    "csymAugmented": "+",
    "csymMajorSeventh": "∆",
    "csymMinor": "-",
}

#: A music-font accidental is drawn much larger than the text beside it; a
#: text accidental at the same size would tower over the chord name.
_TEXT_GLYPH_SCALE = 0.55

_MUSIC_TSPAN = re.compile(
    r'<tspan font-family="(?P<font>[^"]+)" font-size="(?P<size>[\d.]+)px">'
    r"(?P<glyph>[^<]*)</tspan>"
)


@lru_cache(maxsize=8)
def _glyph_names(font: str) -> dict[str, str]:
    """Map codepoint (hex) to glyph name, from the font's own metadata."""
    from importlib.resources import files

    try:
        source = (files("verovio") / "data" / f"{font}.xml").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return {}
    return dict(re.findall(r'<g c="([0-9A-Fa-f]{4})"[^>]*?n="([^"]+)"', source))


def substitute_text_glyphs(svg: str) -> str:
    """Replace music-font characters in text runs with Unicode equivalents.

    Only affects ``<tspan>``s that name a font family -- the notes, clefs and
    staff accidentals are drawn as paths and are untouched.
    """

    def replace(match: re.Match) -> str:
        glyph = match.group("glyph")
        if len(glyph) != 1:
            return match.group(0)

        names = _glyph_names(match.group("font"))
        name = names.get(f"{ord(glyph):04X}")
        equivalent = _TEXT_GLYPH_EQUIVALENTS.get(name or "")
        if equivalent is None:
            return match.group(0)

        size = float(match.group("size")) * _TEXT_GLYPH_SCALE
        return f'<tspan font-size="{size:.0f}px">{equivalent}</tspan>'

    return _MUSIC_TSPAN.sub(replace, svg)


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
            svg = substitute_text_glyphs(toolkit.renderToSVG(index))
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
