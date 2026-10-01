"""The env-target declaration: how a command's own arguments report the one env it targets.

A command positional that names feature envs carries one `EnvTargetDeclaration` as its click
parameter callback. Click runs it only while parsing the invoked command, so only the
command's own arguments are ever reported: handlers, services, and nested resolution (a
provision run's per-env service start) never set the span's `winter.env`.

The declaration hands its tokens to `EnvTargetService`, which reports `winter.env` through
`ICommandAnnotator` when the tokens resolve to exactly one feature env.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import click

from winter_cli.cli_context import CliContext
from winter_cli.core.tracing import ICommandAnnotator
from winter_cli.modules.workspace.models import Workspace
from winter_cli.modules.workspace.pattern_match import matches_any_pattern, resolve_name_patterns
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository

logger = logging.getLogger(__name__)

# The scope name of the workspace itself: a target, but never a feature env.
_WORKSPACE_SCOPE = "workspace"


class EnvNameDiscovery:
    """The names of the feature envs that exist on disk."""

    def __init__(
        self, workspace_repo: IReadWorkspaceRepository, repo_factory: RepositoryFactory, workspace: Workspace
    ) -> None:
        self._workspace_repo = workspace_repo
        self._repo_factory = repo_factory
        self._workspace = workspace

    def names(self) -> list[str]:
        project_repos = self._repo_factory.get_project_repos()
        return [env.name for env in self._workspace_repo.get_environments(self._workspace, project_repos)]


class EnvTargetService:
    """Reports `winter.env` for the one feature env a command's env-target tokens resolve to.

    The env segment of each token (the part before any `/`) is a target. The rules:

    - A literal segment counts as written, so `alpha` counts before alpha exists.
    - A glob segment counts through env discovery.
    - The `workspace` scope never counts.
    - With `discovered_only`, a literal counts only when it is a discovered env (`lint` takes
      repo-or-env names, so a literal may be a repo).
    - No targets, or more than one env, reports nothing.

    With tracing off the service does nothing, discovery included. It never raises and never
    changes the command's outcome: any failure is swallowed.
    """

    def __init__(
        self,
        annotator: ICommandAnnotator,
        tracing_enabled: bool,
        discovery_factory: Callable[[], EnvNameDiscovery],
    ) -> None:
        self._annotator = annotator
        self._tracing_enabled = tracing_enabled
        self._discovery_factory = discovery_factory

    def report(self, tokens: Sequence[str], *, trailing_non_targets: int = 0, discovered_only: bool = False) -> None:
        """Annotate the command span with the env `tokens` resolve to, when it is exactly one.

        The last `trailing_non_targets` tokens are not targets (`ws connect`'s FEATURE_BRANCH).
        """
        if not self._tracing_enabled:
            return
        try:
            env_name = self._single_env(tokens, trailing_non_targets, discovered_only)
            if env_name is not None:
                self._annotator.annotate_env(env_name)
        except Exception:
            logger.debug("could not report the command's target env", exc_info=True)

    def _single_env(self, tokens: Sequence[str], trailing_non_targets: int, discovered_only: bool) -> str | None:
        targets = tokens[: max(len(tokens) - trailing_non_targets, 0)]
        segments = sorted({token.split("/", 1)[0] for token in targets} - {"", _WORKSPACE_SCOPE})
        if not segments:
            return None
        if discovered_only:
            discovered = self._discovery_factory().names()
            envs = {name for name in discovered if matches_any_pattern(name, "", segments)}
        else:
            envs = set(resolve_name_patterns(segments, lambda: self._discovery_factory().names()))
        if len(envs) != 1:
            return None
        return next(iter(envs))


class EnvTargetDeclaration:
    """Click parameter callback that every env-target positional carries.

    `trailing_non_targets` is how many trailing tokens of the positional are not targets
    (`ws connect`'s FEATURE_BRANCH, `ws reset`'s REF, `ws restack`'s BASE). `discovered_only`
    marks `lint`'s repo-or-env names, which count only when they are discovered envs.

    The callback hands the tokens over and returns them unchanged. It never raises: reporting
    is observation only, and a command parses, runs, and exits exactly as it does with
    tracing off.
    """

    def __init__(self, trailing_non_targets: int = 0, *, discovered_only: bool = False) -> None:
        self.trailing_non_targets = trailing_non_targets
        self.discovered_only = discovered_only

    def __call__(self, ctx: click.Context, param: click.Parameter, value: object) -> object:
        if ctx.resilient_parsing or not isinstance(ctx.obj, CliContext):
            return value
        try:
            ctx.obj.container.env_target_service().report(
                _tokens(value), trailing_non_targets=self.trailing_non_targets, discovered_only=self.discovered_only
            )
        except Exception:
            logger.debug("could not report the command's target env", exc_info=True)
        return value


def _tokens(value: object) -> tuple[str, ...]:
    """The positional's raw tokens: nothing for an absent value, one for a single argument."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value)
    return ()
