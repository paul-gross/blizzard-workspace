"""Convention test — `get_standalone_repos()` stays on the git/lifecycle seam.

Convention: winter#160. `RepositoryFactory` exposes two repo-enumeration seams:

- `get_standalone_repos()` — user-declared standalones only. Reserved for the
  git/lifecycle call sites (sync, push, merge, prune, destroy, snapshot,
  `repo_handler`/`workspace_handler` worktree listing) that mean "repos that
  are not worktreed", plus the doctor core-probe's disk-presence check.
- `get_extension_repos()` — standalones plus project repos carrying a root
  `winter-ext.toml`. Every extension-consuming feature (doctor's extension
  probe, lint, graph, the capability registry, service-manifest collection,
  provision handlers, hooks, skills/agents projection) must resolve through
  this seam instead, or a declared project-repo extension silently drops out
  of that feature (winter#160 F4/F5: prune reported a declared project repo's
  source checkout as an orphan clone; destroy skipped a project-repo
  extension's `on_env_destroy` hook).

This test walks every `.py` under `src/winter_cli/` for a
`get_standalone_repos` attribute access and fails if the file isn't in
`GIT_LIFECYCLE_ALLOWED_FILES`. To extend the allowlist, add the file with a
one-line rationale naming which git/lifecycle flow it belongs to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conventions.conftest import SRC_ROOT, location, walk_src

CONVENTION_DOC = "winter#160"

# Files permitted to call `get_standalone_repos()`. Paths are relative to
# `src/winter_cli/`. Every entry names the git/lifecycle flow (or, for the
# doctor/PluginRegistry carve-outs, the reason it isn't extension-consuming).
GIT_LIFECYCLE_ALLOWED_FILES = frozenset(
    {
        # DI container — PluginRegistry.discover walks repos for a root plugin.py
        # (dashboard-TUI discovery), deliberately standalone-only; see the
        # container.py comment at the wiring site.
        "container.py",
        # Defines get_standalone_repos() and calls it internally from
        # get_extension_repos() (standalone-first dedupe) and find_standalone()
        # (singleton + standalone lookup namespace).
        "modules/workspace/repository_factory.py",
        # Doctor core-probe — disk-presence check for declared repos, not a
        # winter-ext.toml reader.
        "modules/doctor/core_probe_service.py",
        # winter ws merge
        "modules/workspace/workspace_merge_service.py",
        # winter ws push
        "modules/workspace/workspace_push_service.py",
        # winter ws sync / winter ws pull
        "modules/workspace/workspace_sync_service.py",
        # winter ws snapshot
        "modules/workspace/workspace_snapshot_service.py",
        # winter ws init — the git-clone lifecycle loop for standalones stays
        # standalone-only; project-repo extensions clone via reconcile_projects().
        "modules/workspace/init_service.py",
        # winter repo <name> / winter ws standalone
        "modules/workspace/handlers/repo_handler.py",
        # winter ws worktrees
        "modules/workspace/handlers/workspace_handler.py",
    }
)


def _relative_module(file_path: Path) -> str | None:
    try:
        return file_path.relative_to(SRC_ROOT).as_posix()
    except ValueError:
        return None


def find_git_lifecycle_seam_violations(file_path: Path, tree: ast.Module) -> list[str]:
    rel = _relative_module(file_path)
    if rel is not None and rel in GIT_LIFECYCLE_ALLOWED_FILES:
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "get_standalone_repos":
            violations.append(
                f"{location(file_path, node)}: calls get_standalone_repos() outside the "
                f"git/lifecycle allowlist — extension-consuming callers must use "
                f"get_extension_repos() instead ({CONVENTION_DOC})"
            )
    return violations


def test_get_standalone_repos_stays_on_git_lifecycle_seam() -> None:
    all_violations: list[str] = []
    for path, tree in walk_src():
        all_violations.extend(find_git_lifecycle_seam_violations(path, tree))
    if all_violations:
        pytest.fail("\n".join(["get_standalone_repos() seam violations:", *all_violations]))


def test_fixture_violation_is_detected() -> None:
    fixture = Path(__file__).parent / "fixtures" / "violating_git_lifecycle_seam.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture))
    violations = find_git_lifecycle_seam_violations(fixture, tree)
    assert violations, "fixture must trigger at least one violation"
    assert any("get_standalone_repos()" in v for v in violations)
