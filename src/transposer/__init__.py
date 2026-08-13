"""Self-hosted sheet-music OMR, transposition and PDF engraving."""

from __future__ import annotations

__version__ = "0.1.0"

from .errors import (
    EngineUnavailableError,
    KeySpecError,
    OmrFailedError,
    RenderFailedError,
    TransposerError,
    UnsupportedInputError,
)
from .keys import parse_target
from .pipeline import PipelineOptions, PipelineResult, run
from .transpose import transpose_score

__all__ = [
    "EngineUnavailableError",
    "KeySpecError",
    "OmrFailedError",
    "PipelineOptions",
    "PipelineResult",
    "RenderFailedError",
    "TransposerError",
    "UnsupportedInputError",
    "__version__",
    "parse_target",
    "run",
    "transpose_score",
]
