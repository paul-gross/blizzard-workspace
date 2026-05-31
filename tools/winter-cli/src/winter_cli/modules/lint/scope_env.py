from __future__ import annotations

from winter_cli.modules.lint.models import LintScope

# Env var names handed to every contributed lint script so it can see what
# slice of the workspace to lint. Kept here so the workspace and extension
# services emit an identical contract.
SCOPE_KIND_VAR = "WINTER_LINT_SCOPE"
SCOPE_PATHS_VAR = "WINTER_LINT_PATHS"


def lint_scope_env(scope: LintScope) -> dict[str, str]:
    """The `WINTER_LINT_*` env vars describing `scope` to a lint script.

    `WINTER_LINT_SCOPE` is the scope kind (`all` / `repo` / `env` / `changed`);
    `WINTER_LINT_PATHS` is the newline-delimited absolute paths in scope (repo
    or env directories, the workspace root, or the individual changed files).
    """
    return {
        SCOPE_KIND_VAR: scope.kind.value,
        SCOPE_PATHS_VAR: "\n".join(str(p) for p in scope.paths),
    }
