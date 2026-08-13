"""Shared types for the rendering backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import RenderFailedError
from ..omr.base import Availability


@dataclass(frozen=True)
class PageSize:
    """A paper size in tenths of a millimetre, which is Verovio's unit."""

    name: str
    width: int
    height: int

    def landscape(self) -> PageSize:
        return PageSize(f"{self.name}-landscape", self.height, self.width)

    @property
    def width_mm(self) -> float:
        return self.width / 10

    @property
    def height_mm(self) -> float:
        return self.height / 10

    @property
    def width_pt(self) -> float:
        return self.width_mm * 72 / 25.4

    @property
    def height_pt(self) -> float:
        return self.height_mm * 72 / 25.4

    # CairoSVG measures its output in CSS pixels at 96 dpi, so a page handed to
    # it in points comes out three quarters of the intended size.
    @property
    def width_px(self) -> float:
        return self.width_mm * 96 / 25.4

    @property
    def height_px(self) -> float:
        return self.height_mm * 96 / 25.4


PAGE_SIZES: dict[str, PageSize] = {
    "a4": PageSize("a4", 2100, 2970),
    "a3": PageSize("a3", 2970, 4200),
    "a5": PageSize("a5", 1480, 2100),
    "letter": PageSize("letter", 2159, 2794),
    "legal": PageSize("legal", 2159, 3556),
    "tabloid": PageSize("tabloid", 2794, 4318),
}


def resolve_page_size(name: str, landscape: bool = False) -> PageSize:
    key = (name or "a4").strip().lower()
    if key not in PAGE_SIZES:
        known = ", ".join(sorted(PAGE_SIZES))
        raise RenderFailedError(f"unknown paper size {name!r}; known sizes: {known}")
    size = PAGE_SIZES[key]
    return size.landscape() if landscape else size


@dataclass
class RenderResult:
    """A rendered document plus anything useful produced along the way."""

    pdf: Path
    renderer: str
    page_count: int = 0
    svg_pages: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Renderer(ABC):
    name: str = "renderer"
    description: str = ""

    @abstractmethod
    def availability(self) -> Availability:
        """Report whether this renderer can run right now."""

    @abstractmethod
    def render(
        self,
        musicxml: Path,
        target: Path,
        page_size: PageSize,
        scale: int = 40,
        workdir: Path | None = None,
    ) -> RenderResult:
        """Engrave ``musicxml`` into a PDF at ``target``."""

    def require_available(self) -> None:
        status = self.availability()
        if not status.ok:
            raise RenderFailedError(f"renderer {self.name!r}: {status.reason}")
