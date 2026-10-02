"""Fixture: GitPython repositories opened without a git-operation declaration, and outside the adapters.

Read as if it lived in `modules/workspace/internal/`, `opens_without_a_declaration` and the
module-level open are the violations; the declared and exempt functions are fine. Read as if it
lived anywhere else, every open here is a violation.
"""

from __future__ import annotations

import git
from git import Repo

from winter_cli.modules.workspace.internal.git_operation import GitOperationDeclaration, GitOperationExemption

MODULE_LEVEL = git.Repo("/somewhere")


class SomeAdapter:
    def opens_without_a_declaration(self, path):
        with git.Repo(str(path)) as r:
            return r.git.status()

    def clones_without_a_declaration(self, url, dest):
        return Repo.clone_from(url, str(dest))

    @GitOperationDeclaration("fetch")
    def declared(self, path):
        with git.Repo(str(path)) as r:
            return r.git.fetch()

    @GitOperationExemption("discovery")
    def exempt(self, path):
        with git.Repo(str(path)) as r:
            return r.git.worktree("list")

    @GitOperationDeclaration("status")
    def declared_with_a_nested_helper(self, path):
        def helper():
            return git.Repo(str(path))

        return helper()
