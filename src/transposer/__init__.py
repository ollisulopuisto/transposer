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
    "__version__",
    "run",
    "PipelineOptions",
    "PipelineResult",
    "transpose_score",
    "parse_target",
    "TransposerError",
    "UnsupportedInputError",
    "EngineUnavailableError",
    "OmrFailedError",
    "RenderFailedError",
    "KeySpecError",
]
