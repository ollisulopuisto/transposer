"""OMR engine registry.

Engines are looked up by name; ``"auto"`` picks the best available one for the
input at hand. The registry is a plain dict of factories so that constructing an
engine (which probes the filesystem and the environment) only happens on demand.
"""

from __future__ import annotations

from typing import Callable, Iterable

from ..errors import EngineUnavailableError
from ..ingest import IngestedInput
from .audiveris import AudiverisEngine
from .base import Availability, OmrEngine, OmrResult
from .mozart import MozartEngine
from .oemer import OemerEngine
from .passthrough import PassthroughEngine

__all__ = [
    "Availability",
    "OmrEngine",
    "OmrResult",
    "AudiverisEngine",
    "MozartEngine",
    "OemerEngine",
    "PassthroughEngine",
    "ENGINES",
    "get_engine",
    "available_engines",
    "select_engine",
]

ENGINES: dict[str, Callable[[], OmrEngine]] = {
    "passthrough": PassthroughEngine,
    "audiveris": AudiverisEngine,
    "oemer": OemerEngine,
    "mozart": MozartEngine,
}


def get_engine(name: str) -> OmrEngine:
    """Instantiate an engine by name."""
    key = (name or "").strip().lower()
    if key not in ENGINES:
        known = ", ".join(sorted(ENGINES))
        raise EngineUnavailableError(f"unknown OMR engine {name!r}; known engines: {known}")
    return ENGINES[key]()


def all_engines() -> list[OmrEngine]:
    """Every engine, constructed, in preference order."""
    return sorted((factory() for factory in ENGINES.values()), key=lambda e: e.priority)


def available_engines() -> list[tuple[OmrEngine, Availability]]:
    """Every engine paired with whether it can run here."""
    return [(engine, engine.availability()) for engine in all_engines()]


def select_engine(source: IngestedInput, preference: str = "auto") -> OmrEngine:
    """Pick an engine for ``source``.

    A score file always goes through the passthrough engine -- running OMR on
    MusicXML would be absurd. Otherwise ``auto`` walks the engines in priority
    order and returns the first that is both available and able to handle the
    input.
    """
    if source.is_score:
        return PassthroughEngine()

    if preference and preference != "auto":
        engine = get_engine(preference)
        engine.require_available()
        return engine

    reasons: list[str] = []
    for engine in all_engines():
        if isinstance(engine, PassthroughEngine):
            continue
        status = engine.availability()
        if status.ok:
            return engine
        reasons.append(f"  {engine.name}: {status.reason}")

    raise EngineUnavailableError(
        "no OMR engine is available for this input. Tried:\n" + "\n".join(reasons)
    )


def describe_engines() -> Iterable[str]:
    """Human-readable engine list for ``transposer engines``."""
    for engine, status in available_engines():
        mark = "ok" if status.ok else "unavailable"
        line = f"{engine.name:<12} [{mark}]  {engine.description}"
        if not status.ok:
            line += f"\n{'':<14}  → {status.reason}"
        yield line
