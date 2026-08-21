"""Engraving backends: MusicXML in, PDF (and SVG preview) out."""

from __future__ import annotations

from collections.abc import Callable

from ..errors import RenderFailedError
from .base import PageSize, Renderer, RenderResult
from .musescore import MuseScoreRenderer
from .verovio_renderer import VerovioRenderer

__all__ = [
    "RENDERERS",
    "MuseScoreRenderer",
    "PageSize",
    "RenderResult",
    "Renderer",
    "VerovioRenderer",
    "describe_renderers",
    "get_renderer",
    "select_renderer",
]

RENDERERS: dict[str, Callable[[], Renderer]] = {
    "verovio": VerovioRenderer,
    "musescore": MuseScoreRenderer,
}


def get_renderer(name: str) -> Renderer:
    key = (name or "").strip().lower()
    if key not in RENDERERS:
        known = ", ".join(sorted(RENDERERS))
        raise RenderFailedError(f"unknown renderer {name!r}; known renderers: {known}")
    return RENDERERS[key]()


def select_renderer(preference: str = "auto") -> Renderer:
    """Pick a renderer.

    MuseScore engraves better than anything else available offline, so it wins
    when installed; Verovio is the always-there fallback because it ships as a
    Python wheel.
    """
    if preference and preference != "auto":
        renderer = get_renderer(preference)
        renderer.require_available()
        return renderer

    for name in ("musescore", "verovio"):
        renderer = get_renderer(name)
        if renderer.availability().ok:
            return renderer

    raise RenderFailedError("no renderer is available")


def describe_renderers():
    for name in sorted(RENDERERS):
        renderer = get_renderer(name)
        status = renderer.availability()
        mark = "ok" if status.ok else "unavailable"
        line = f"{renderer.name:<12} [{mark}]  {renderer.description}"
        if not status.ok:
            line += f"\n{'':<14}  → {status.reason}"
        yield line
