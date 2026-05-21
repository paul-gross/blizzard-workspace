from __future__ import annotations

from typing import Protocol


class ICliInputValidationService(Protocol):
    """Protocol for CLI input validation — handlers depend on this seam.

    Implementations reject invalid inputs by raising the surface error type
    appropriate to their rendering engine (e.g. ``click.ClickException`` for a
    click-driven CLI).
    """

    def validate_git_url(self, url: str) -> None: ...
