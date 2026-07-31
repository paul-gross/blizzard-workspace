from __future__ import annotations

import re

import click

from winter_cli.modules.workspace.models.domain_model import ProjectRepository, StandaloneRepository

ALIASES: tuple[str, ...] = ("main", "master", "default")
"""Interchangeable `{...}` token names — each expands to the repo's configured main branch."""

_TOKEN_RE = re.compile(r"(?<!@)\{([^{}]*)\}")


def resolve_ref(ref: str, repo: ProjectRepository | StandaloneRepository) -> str:
    """Expand per-repo `{main}` / `{master}` / `{default}` tokens in `ref` against `repo.main_branch`.

    Config-only, no network. The three aliases are interchangeable — each expands
    to `repo.main_branch`, which already folds the per-repo override over the
    workspace default (`RepositoryFactory` resolves that once at construction, so
    by the time a repo reaches this function `main_branch` is the effective
    value). A token may appear anywhere in `ref` — bare `{main}` and
    `origin/{main}` both work. A `ref` with no `{...}` token is returned
    unchanged, byte-for-byte. Git's own `@{...}` syntax (`@{u}`, `HEAD@{1}`,
    `master@{yesterday}`) is never treated as a winter token — only an
    unprefixed `{...}` group is — so those refs pass through unchanged for
    git to resolve natively.

    Raises `click.ClickException` — refusing before any git operation runs — when
    `ref` contains a `{...}` token that isn't one of the accepted aliases, naming
    the unknown token and listing the accepted set. Braces rather than angle
    brackets: `<main>` is a shell redirection operator and never reaches the CLI
    in the first place. Also raises when a token expands but `repo.main_branch`
    is unset, naming the repo, rather than silently substituting an empty
    segment.
    """

    def _expand(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in ALIASES:
            accepted = ", ".join(f"{{{alias}}}" for alias in ALIASES)
            raise click.ClickException(f"Unknown ref token '{{{token}}}' in '{ref}' — accepted aliases: {accepted}")
        if not repo.main_branch:
            raise click.ClickException(
                f"Repo '{repo.name}' has no main_branch configured — cannot expand '{{{token}}}' in '{ref}'"
            )
        return repo.main_branch

    return _TOKEN_RE.sub(_expand, ref)
