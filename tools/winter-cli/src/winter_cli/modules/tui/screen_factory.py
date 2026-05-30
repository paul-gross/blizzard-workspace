from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from winter_cli.container import Container


class ScreenFactory:
    def __init__(self, container: Container) -> None:
        self._container = container

    def workspace_screen(self):
        return self._container.workspace_screen()

    def worktree_detail_screen(self, worktree_name: str, focused_repo: str | None = None):
        return self._container.worktree_detail_screen(worktree_name=worktree_name, focused_repo=focused_repo)

    def standalone_detail_screen(self, repo_name: str):
        return self._container.standalone_detail_screen(repo_name=repo_name)

    def error_log_screen(self):
        return self._container.error_log_screen()
