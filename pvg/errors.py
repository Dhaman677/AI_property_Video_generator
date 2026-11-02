"""Exception types. Every error carries a message that is safe to show to end users."""
from __future__ import annotations
from typing import List, Optional


class PVGError(Exception):
    """Base class for all expected, user-facing errors."""


class InputError(PVGError):
    """The supplied property details / images cannot be used."""

    def __init__(self, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.problems = problems or [message]

    def __str__(self) -> str:  # pragma: no cover - trivial
        if len(self.problems) > 1:
            return "Input problems:\n  - " + "\n  - ".join(self.problems)
        return self.problems[0]


class RenderError(PVGError):
    """Rendering or encoding the video failed."""
