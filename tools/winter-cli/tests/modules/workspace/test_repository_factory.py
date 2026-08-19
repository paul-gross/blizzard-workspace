from __future__ import annotations

import logging

import pytest

from tests.conftest import FakeFilesystem
from winter_cli.config.models import (
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
    one — with a warning naming the now-redundant [[standalone_repository]]
    declaration. Keeping the standalone entry (rather than the project-repo entry)
    while both declarations exist is what keeps `ExtensionAgentsMdService`'s
    routing-row fork from firing early — see winter#160."""
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
    assert any(
        "winter-context" in record.message and "standalone_repository" in record.message for record in caplog.records
    )


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
