"""Exception types raised across the pipeline."""

from __future__ import annotations


class TransposerError(Exception):
    """Base class for every error this package raises deliberately."""


class UnsupportedInputError(TransposerError):
    """The input file is not something any stage of the pipeline can read."""


class EngineUnavailableError(TransposerError):
    """An OMR or rendering engine was requested but is not installed/usable."""


class OmrFailedError(TransposerError):
    """An OMR engine ran but produced nothing usable."""


class RenderFailedError(TransposerError):
    """A renderer ran but produced no PDF."""


class KeySpecError(TransposerError, ValueError):
    """A target key / interval specification could not be parsed."""
