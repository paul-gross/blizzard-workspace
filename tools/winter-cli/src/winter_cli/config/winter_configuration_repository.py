from __future__ import annotations

from typing import Protocol

from winter_cli.config.models import (
    ProjectRepositoryConfig,
    StandaloneRepositoryConfig,
)


class IWriteWinterConfigurationRepository(Protocol):
    """Mutates the workspace's winter configuration files.

    Each method takes `local` to target `config.local.toml` instead of the shared
    `config.toml`. The local file is auto-created on first write if missing.

    Appends accept Pydantic config models — only fields the caller explicitly set
    are written. Removals match a block by its explicit `name` or URL-derived
    name and return `True` if a block was removed, `False` if not found.
    """

    def append_project_repository(self, config: ProjectRepositoryConfig, local: bool = False) -> None: ...

    def append_standalone_repository(self, config: StandaloneRepositoryConfig, local: bool = False) -> None: ...

    def remove_project_repository(self, name: str, local: bool = False) -> bool: ...

    def remove_standalone_repository(self, name: str, local: bool = False) -> bool: ...
