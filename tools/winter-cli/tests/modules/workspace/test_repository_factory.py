from __future__ import annotations

import logging

import pytest

from tests.conftest import FakeFilesystem
from winter_cli.config.models import (
    ExtensionLoad,
    ProjectRepositoryConfig,
    SingletonRepository,
    SingletonType,
    StandaloneRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST
from winter_cli.modules.workspace.repository_factory import RepositoryFactory


def test_get_workspace_repo_returns_workspace_root_singleton(
    workspace_config: WorkspaceConfig,
) -> None:
    """get_workspace_repo() resolves the workspace singleton to the workspace root."""
    factory = RepositoryFactory(workspace_config)

    workspace_repo = factory.get_workspace_repo()

    assert workspace_repo is not None
    assert workspace_repo.name == workspace_config.workspace_root.name
    assert workspace_repo.path == workspace_config.workspace_root


def test_get_workspace_repo_none_without_workspace_singleton(
    workspace_config: WorkspaceConfig,
) -> None:
    """When no workspace singleton is configured, get_workspace_repo() returns None.

    The workspace singleton is normally always present, but the accessor must not
    assume it — other singletons (product/harness) alone yield None.
    """
    config = workspace_config.model_copy(
        update={"singleton_repos": [SingletonRepository(name="product", type=SingletonType.product)]},
    )
    factory = RepositoryFactory(config)

    assert factory.get_workspace_repo() is None


# ── ref threading from config → domain ──────────────────────────────────────


def test_get_standalone_repos_threads_ref_from_config(
    workspace_config: WorkspaceConfig,
) -> None:
    """get_standalone_repos() populates StandaloneRepository.ref from StandaloneRepositoryConfig.ref."""
    config = workspace_config.model_copy(
        update={
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="pinned-ext",
                    url="git@example.com:org/pinned-ext.git",
                    ref="v1.2.0",
                ),
            ],
        },
    )
    factory = RepositoryFactory(config)

    repos = factory.get_standalone_repos()

    assert len(repos) == 1
    assert repos[0].ref == "v1.2.0"


def test_get_standalone_repos_ref_is_none_when_not_configured(
    workspace_config: WorkspaceConfig,
) -> None:
    """get_standalone_repos() leaves StandaloneRepository.ref as None when config omits ref."""
    config = workspace_config.model_copy(
        update={
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="unpinned-ext",
                    url="git@example.com:org/unpinned-ext.git",
                ),
            ],
        },
    )
    factory = RepositoryFactory(config)

    repos = factory.get_standalone_repos()

    assert len(repos) == 1
    assert repos[0].ref is None


# ── get_extension_repos() ─────────────────────────────────────────────────


def test_get_extension_repos_includes_standalones(
    workspace_config: WorkspaceConfig,
) -> None:
    """A plain standalone (no project-repo counterpart) is included as-is."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [],
            "standalone_repos": [
                StandaloneRepositoryConfig(name="my-ext", url="git@example.com:org/my-ext.git"),
            ],
        },
    )
    factory = RepositoryFactory(config, fs=FakeFilesystem())

    repos = factory.get_extension_repos()

    assert [r.name for r in repos] == ["my-ext"]
    assert repos[0].path == config.workspace_root / "my-ext"


def test_get_extension_repos_includes_project_repo_with_manifest(
    workspace_config: WorkspaceConfig,
) -> None:
    """A project repo whose projects/<name>/ root carries a winter-ext.toml is eligible."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="winter-docs", url="git@example.com:org/winter-docs.git"),
            ],
            "standalone_repos": [],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-docs"
    fs = FakeFilesystem(files={main_path / EXT_MANIFEST: ""})
    factory = RepositoryFactory(config, fs=fs)

    repos = factory.get_extension_repos()

    assert [r.name for r in repos] == ["winter-docs"]
    assert repos[0].path == main_path


def test_get_extension_repos_excludes_project_repo_without_manifest(
    workspace_config: WorkspaceConfig,
) -> None:
    """A project repo with no root winter-ext.toml is not extension-eligible."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="plain-app", url="git@example.com:org/plain-app.git"),
            ],
            "standalone_repos": [],
        },
    )
    factory = RepositoryFactory(config, fs=FakeFilesystem())

    repos = factory.get_extension_repos()

    assert repos == []


def test_get_extension_repos_dedupes_repo_declared_as_both_kinds(
    workspace_config: WorkspaceConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A repo declared as both [[project_repository]] and [[standalone_repository]]
    yields a single extension entry — the standalone checkout, not the project-repo
    one — and says nothing about it: a double declaration is a supported way to pin
    an extension to a path/prefix/ref, not a misconfiguration. Keeping the standalone
    entry (rather than the project-repo entry) is what keeps `ExtensionAgentsMdService`'s
    routing-row fork from firing while both declarations exist."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="winter-context", url="git@example.com:org/winter-context.git"),
            ],
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="winter-context",
                    url="git@example.com:org/winter-context.git",
                    path=".winter/ext/context",
                ),
            ],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-context"
    fs = FakeFilesystem(files={main_path / EXT_MANIFEST: ""})
    factory = RepositoryFactory(config, fs=fs)

    with caplog.at_level(logging.WARNING):
        repos = factory.get_extension_repos()

    assert [r.name for r in repos] == ["winter-context"]
    assert repos[0].path == config.workspace_root / ".winter/ext/context"
    assert caplog.records == []


def test_get_standalone_repos_excludes_project_repo_extensions(
    workspace_config: WorkspaceConfig,
) -> None:
    """Regression: get_standalone_repos() — the seam the git/lifecycle call sites
    (sync, push, merge, prune, destroy, snapshot, repo_handler) depend on — never
    picks up a project repo, even one carrying a root winter-ext.toml. Only
    get_extension_repos() folds project-repo extensions in."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="winter-docs", url="git@example.com:org/winter-docs.git"),
            ],
            "standalone_repos": [],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-docs"
    fs = FakeFilesystem(files={main_path / EXT_MANIFEST: ""})
    factory = RepositoryFactory(config, fs=fs)

    assert factory.get_standalone_repos() == []
    assert [r.name for r in factory.get_extension_repos()] == ["winter-docs"]


def test_get_extension_repos_defaults_fs_to_local_filesystem(
    workspace_config: WorkspaceConfig,
) -> None:
    """No `fs` injected — RepositoryFactory falls back to the real filesystem
    rather than raising, so untouched call sites keep working."""
    config = workspace_config.model_copy(update={"project_repos": [], "standalone_repos": []})
    factory = RepositoryFactory(config)

    assert factory.get_extension_repos() == []


# ── extension = false / load / entry ────────────────────────────────────────


def test_get_extension_repos_excludes_a_standalone_declaring_extension_false(
    workspace_config: WorkspaceConfig,
) -> None:
    """The repo stays a standalone — cloned, fetched, pulled, pinned — but no extension feature sees it."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [],
            "standalone_repos": [
                StandaloneRepositoryConfig(name="corpus", url="git@example.com:org/corpus.git", extension=False),
                StandaloneRepositoryConfig(name="my-ext", url="git@example.com:org/my-ext.git"),
            ],
        },
    )
    factory = RepositoryFactory(config, fs=FakeFilesystem())

    assert [r.name for r in factory.get_extension_repos()] == ["my-ext"]
    assert [r.name for r in factory.get_standalone_repos()] == ["corpus", "my-ext"]
    assert [r.name for r in factory.get_extension_standalone_repos()] == ["my-ext"]
    assert [r.name for r in factory.get_non_extension_repos()] == ["corpus"]


def test_get_extension_repos_excludes_a_manifest_bearing_project_repo_declaring_extension_false(
    workspace_config: WorkspaceConfig,
) -> None:
    """`extension = false` wins over a root winter-ext.toml; the project repo still worktrees."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="winter-docs", url="git@example.com:org/winter-docs.git", extension=False),
            ],
            "standalone_repos": [],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-docs"
    factory = RepositoryFactory(config, fs=FakeFilesystem(files={main_path / EXT_MANIFEST: ""}))

    assert factory.get_extension_repos() == []
    assert [r.name for r in factory.get_project_repos()] == ["winter-docs"]
    opted_out = factory.get_non_extension_repos()
    assert [(r.name, r.path) for r in opted_out] == [("winter-docs", main_path)]


@pytest.mark.parametrize(
    ("standalone_extension", "project_extension"),
    [(False, True), (True, False), (False, False)],
)
def test_get_extension_repos_excludes_a_double_declaration_when_either_entry_says_false(
    workspace_config: WorkspaceConfig, standalone_extension: bool, project_extension: bool
) -> None:
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(
                    name="winter-context",
                    url="git@example.com:org/winter-context.git",
                    extension=project_extension,
                ),
            ],
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="winter-context",
                    url="git@example.com:org/winter-context.git",
                    path=".winter/ext/context",
                    extension=standalone_extension,
                ),
            ],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-context"
    factory = RepositoryFactory(config, fs=FakeFilesystem(files={main_path / EXT_MANIFEST: ""}))

    assert factory.get_extension_repos() == []
    assert [r.name for r in factory.get_standalone_repos()] == ["winter-context"]
    assert [r.name for r in factory.get_non_extension_repos()] == ["winter-context"]


def test_get_standalone_repos_threads_load_and_entry_from_config(
    workspace_config: WorkspaceConfig,
) -> None:
    config = workspace_config.model_copy(
        update={
            "project_repos": [],
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="mirror",
                    url="git@example.com:org/mirror.git",
                    load=ExtensionLoad.lazy,
                    entry=["docs/agents.md", "index.md"],
                ),
            ],
        },
    )

    repo = RepositoryFactory(config, fs=FakeFilesystem()).get_standalone_repos()[0]

    assert repo.extension is True
    assert repo.load is ExtensionLoad.lazy
    assert repo.entry == ("docs/agents.md", "index.md")


def test_get_extension_repos_carries_a_project_repo_entrys_load_and_entry(
    workspace_config: WorkspaceConfig,
) -> None:
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(
                    name="winter-docs",
                    url="git@example.com:org/winter-docs.git",
                    load=ExtensionLoad.none,
                    entry=["docs/agents.md"],
                ),
            ],
            "standalone_repos": [],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-docs"
    factory = RepositoryFactory(config, fs=FakeFilesystem(files={main_path / EXT_MANIFEST: ""}))

    repo = factory.get_extension_repos()[0]

    assert repo.load is ExtensionLoad.none
    assert repo.entry == ("docs/agents.md",)


def test_get_extension_repos_double_declaration_takes_load_from_the_standalone_entry_first(
    workspace_config: WorkspaceConfig,
) -> None:
    """The standalone entry wins; the project entry fills only what the standalone leaves unset."""
    config = workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(
                    name="winter-context",
                    url="git@example.com:org/winter-context.git",
                    load=ExtensionLoad.lazy,
                    entry=["docs/agents.md"],
                ),
            ],
            "standalone_repos": [
                StandaloneRepositoryConfig(
                    name="winter-context",
                    url="git@example.com:org/winter-context.git",
                    load=ExtensionLoad.eager,
                ),
            ],
        },
    )
    main_path = config.workspace_root / "projects" / "winter-context"
    factory = RepositoryFactory(config, fs=FakeFilesystem(files={main_path / EXT_MANIFEST: ""}))

    repo = factory.get_extension_repos()[0]

    assert repo.load is ExtensionLoad.eager
    assert repo.entry == ("docs/agents.md",)


def _context_only_config(workspace_config: WorkspaceConfig, **project_keys: object) -> WorkspaceConfig:
    return workspace_config.model_copy(
        update={
            "project_repos": [
                ProjectRepositoryConfig(name="app", url="git@example.com:org/app.git", **project_keys),  # type: ignore[arg-type]
            ],
            "standalone_repos": [],
        },
    )


@pytest.mark.parametrize("keys", [{"load": ExtensionLoad.lazy}, {"load": ExtensionLoad.eager}, {"entry": ["a.md"]}])
def test_get_context_only_repos_returns_a_manifest_less_project_repo_with_an_explicit_opt_in(
    workspace_config: WorkspaceConfig, keys: dict[str, object]
) -> None:
    config = _context_only_config(workspace_config, **keys)
    factory = RepositoryFactory(config, fs=FakeFilesystem())

    repos = factory.get_context_only_repos()

    assert [r.name for r in repos] == ["app"]
    assert repos[0].path == config.workspace_root / "projects" / "app"
    assert repos[0].load is keys.get("load")
    # Never an extension: no skill, agent, hook, or service feature sees it.
    assert factory.get_extension_repos() == []


@pytest.mark.parametrize(
    "keys",
    [{}, {"load": ExtensionLoad.none}, {"load": ExtensionLoad.lazy, "extension": False}],
)
def test_get_context_only_repos_skips_a_project_repo_without_an_opt_in_or_that_opted_out(
    workspace_config: WorkspaceConfig, keys: dict[str, object]
) -> None:
    factory = RepositoryFactory(_context_only_config(workspace_config, **keys), fs=FakeFilesystem())

    assert factory.get_context_only_repos() == []


def test_get_context_only_repos_skips_a_project_repo_that_has_a_manifest(
    workspace_config: WorkspaceConfig,
) -> None:
    """A manifest-bearing project repo is already an extension, rendered through `get_extension_repos`."""
    config = _context_only_config(workspace_config, load=ExtensionLoad.lazy)
    main_path = config.workspace_root / "projects" / "app"
    factory = RepositoryFactory(config, fs=FakeFilesystem(files={main_path / EXT_MANIFEST: ""}))

    assert factory.get_context_only_repos() == []
    assert [r.name for r in factory.get_extension_repos()] == ["app"]


def test_get_context_only_repos_skips_a_project_repo_also_declared_as_a_standalone(
    workspace_config: WorkspaceConfig,
) -> None:
    config = _context_only_config(workspace_config, load=ExtensionLoad.lazy).model_copy(
        update={"standalone_repos": [StandaloneRepositoryConfig(name="app", url="git@example.com:org/app.git")]}
    )
    factory = RepositoryFactory(config, fs=FakeFilesystem())

    assert factory.get_context_only_repos() == []
