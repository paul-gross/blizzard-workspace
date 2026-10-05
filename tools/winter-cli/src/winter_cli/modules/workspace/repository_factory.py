from __future__ import annotations

from typing import Protocol

from winter_cli.config.models import (
    ExtensionLoad,
    ProjectRepositoryConfig,
    SingletonType,
    StandaloneRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.core.internal.local_filesystem import LocalFilesystem
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST
from winter_cli.modules.workspace.models import (
    ProjectRepository,
    StandaloneRepository,
)

_SINGLETON_PATHS: dict[SingletonType, tuple[str, ...]] = {
    SingletonType.workspace: (),
    SingletonType.product: ("product",),
    SingletonType.harness: ("context", "harness"),
}


class IStandaloneRepoProvider(Protocol):
    """The capability the git/lifecycle call sites (sync, push, merge, prune,
    destroy, snapshot, `repo_handler`) need from the repo factory: enumerate
    the user-declared standalone repos — repos that exist at one fixed
    workspace-root-relative location rather than one worktree per env.
    Narrowing the dependency to this Protocol keeps the consumer off the
    concrete `RepositoryFactory` and lets tests pass a plain stub.
    """

    def get_standalone_repos(self) -> list[StandaloneRepository]: ...


class IExtensionRepoProvider(Protocol):
    """The capability extension-consuming features need from the repo factory:
    enumerate every repo eligible to act as an extension — standalones plus
    project repos carrying a root `winter-ext.toml`. Distinct from
    `IStandaloneRepoProvider`: doctor, lint, graph, the capability registry,
    service-manifest collection, and provision handlers all read a
    `winter-ext.toml` and so want project-repo extensions folded in; the
    git/lifecycle call sites do not and stay on `IStandaloneRepoProvider`.
    """

    def get_extension_repos(self) -> list[StandaloneRepository]: ...


class RepositoryFactory:
    def __init__(self, config: WorkspaceConfig, fs: IFilesystemReader | None = None) -> None:
        self._config = config
        self._fs = fs if fs is not None else LocalFilesystem()

    def get_project_repos(self) -> list[ProjectRepository]:
        result: list[ProjectRepository] = []
        for r in self._config.project_repos:
            name = self._resolve_project_name(r)
            result.append(
                ProjectRepository(
                    name=name,
                    main_path=self._config.workspace_root / "projects" / name,
                    main_branch=r.main_branch or self._config.main_branch,
                    pinned=r.pinned,
                    url=r.url,
                    git_excludes=list(r.git_excludes),
                    cmd=list(r.cmd),
                    nested=r.nested,
                )
            )
        return result

    def get_singleton_repos(self) -> list[StandaloneRepository]:
        """Return implicit singletons — workspace, product, harness — discovered from the filesystem."""
        result: list[StandaloneRepository] = []
        for r in self._config.singleton_repos:
            parts = _SINGLETON_PATHS[r.type]
            path = self._config.workspace_root / "/".join(parts) if parts else self._config.workspace_root
            result.append(StandaloneRepository(name=r.name, path=path))
        return result

    def get_workspace_repo(self) -> StandaloneRepository | None:
        """Return the implicit workspace singleton — the workspace root itself.

        The workspace root is a real git repo on the workspace branch but has no
        `<env>/<repo>` location, so callers that need to surface it on its own
        (e.g. `ws worktrees`) resolve it through this focused accessor rather
        than filtering `get_singleton_repos()` by type.

        The workspace-root path resolution mirrors `get_singleton_repos()` (where
        `_SINGLETON_PATHS[SingletonType.workspace] == ()` maps to `workspace_root`);
        keep the two in sync if that mapping ever changes.
        """
        for r in self._config.singleton_repos:
            if r.type is SingletonType.workspace:
                return StandaloneRepository(name=r.name, path=self._config.workspace_root)
        return None

    def get_standalone_repos(self) -> list[StandaloneRepository]:
        """Return user-declared standalone repos from [[standalone_repository]] in config.

        These are cloned at the workspace root (or under the configured `path`) by
        `winter ws init` and may opt into extension behavior via a winter-ext.toml
        file at the repo root.
        """
        result: list[StandaloneRepository] = []
        project_entries = self._project_entries()
        for r in self._config.standalone_repos:
            name = self._resolve_standalone_name(r)
            relative_path = r.path or name
            config_dir = (self._config.workspace_root / (r.config_dir or f".winter/config/{name}")).resolve()
            project_entry = project_entries.get(name)
            result.append(
                StandaloneRepository(
                    name=name,
                    path=self._config.workspace_root / relative_path,
                    main_branch=r.main_branch or self._config.main_branch,
                    url=r.url,
                    git_excludes=list(r.git_excludes),
                    cmd=list(r.cmd),
                    prefix=r.prefix,
                    ref=r.ref,
                    config_dir=config_dir,
                    extension=r.extension and (project_entry is None or project_entry.extension),
                    load=r.load or (project_entry.load if project_entry is not None else None),
                    entry=self._entry_candidates(r.entry or (project_entry.entry if project_entry else None)),
                )
            )
        return result

    def get_extension_repos(self) -> list[StandaloneRepository]:
        """Return every repo eligible to act as an extension.

        This is standalones (`get_standalone_repos()`) plus project repos whose
        `projects/<name>/` root carries a `winter-ext.toml`, projected as
        `StandaloneRepository`-shaped entries rooted at the project repo's
        source checkout (`main_path`) — never at any of its per-env worktree
        copies. Every extension-consuming feature (doctor, lint, graph, the
        capability registry, service-manifest collection, provision handlers,
        hooks, skills/agents projection) already resolves its paths from
        `repo.path`, and `projects/<name>/` is as fixed a path as
        `.winter/ext/<x>/`, so those features need no new logic — only this
        different source list.

        A repo declared as both `[[project_repository]]` (with a root
        `winter-ext.toml`) and `[[standalone_repository]]` dedupes to the
        standalone entry — not the project-repo entry — silently: a double
        declaration is a supported way to pin an extension to a `path`,
        `prefix`, or `ref` the project-repo entry has no field for, so it is
        not something to warn about. Preferring the standalone entry also keeps
        `ExtensionAgentsMdService`'s routing-row fork (which forks on whether
        the resolved path sits under `projects/`) from firing while both
        declarations exist: the repo keeps rendering as its eager `@`-import
        instead of switching to a no-`@` routing row. Drop the standalone
        declaration and the project-repo entry takes over automatically.

        A repo entry declaring `extension = false` is data, not an extension:
        it is dropped here — the one filter every extension consumer shares —
        while `get_standalone_repos()` / `get_project_repos()` still return it
        for the git lifecycle (clone, fetch, pull, `ref` pinning). On a double
        declaration, `extension = false` on either entry excludes the repo
        (`get_standalone_repos()` folds the project-repo entry's value in).
        """
        result: list[StandaloneRepository] = []
        seen: set[str] = set()
        project_entries = self._project_entries()

        for repo in self.get_standalone_repos():
            seen.add(repo.name)
            if repo.extension:
                result.append(repo)

        for project_repo in self.get_project_repos():
            if project_repo.name in seen:
                continue
            project_entry = project_entries[project_repo.name]
            if not project_entry.extension:
                continue
            manifest_path = project_repo.main_path / EXT_MANIFEST
            if not self._fs.is_file(manifest_path):
                continue
            # `ProjectRepositoryConfig` declares no `config_dir`/`prefix`/`ref`
            # override fields, so there is nothing to carry over for those
            # (its `load`/`entry` are carried over below) — but `config_dir` is set explicitly here (matching the same
            # default formula `get_standalone_repos()` uses) rather than left
            # None to fall through to `ServiceOrchestratorResolver`'s
            # synthetic-config-dir fallback, which only happens to compute the
            # same path today because it derives it from `ext_dir.name`.
            result.append(
                StandaloneRepository(
                    name=project_repo.name,
                    path=project_repo.main_path,
                    main_branch=project_repo.main_branch,
                    config_dir=(self._config.workspace_root / f".winter/config/{project_repo.name}").resolve(),
                    load=project_entry.load,
                    entry=self._entry_candidates(project_entry.entry),
                )
            )
            seen.add(project_repo.name)

        return result

    def get_context_only_repos(self) -> list[StandaloneRepository]:
        """Return the manifest-less project repos that opted into context delivery only.

        A `[[project_repository]]` entry with no root `winter-ext.toml` is not
        an extension, so `get_extension_repos()` skips it. When the entry
        explicitly declares `load = "eager"` or `"lazy"`, or an `entry` list, it
        asks for its entry point to reach `AGENTS.winter.md` all the same: this
        returns it, shaped as a `StandaloneRepository` rooted at the source
        checkout, for the context-delivery renderer alone. It is never fed to a
        skill, agent, hook, or any other extension feature — those keep drawing
        from `get_extension_repos()`. A repo that also has a
        `[[standalone_repository]]` declaration is excluded (the standalone
        entry already carries the merged `load`/`entry`), as is one declaring
        `extension = false` or `load = "none"`.
        """
        result: list[StandaloneRepository] = []
        seen = {repo.name for repo in self.get_standalone_repos()}
        project_entries = self._project_entries()
        for project_repo in self.get_project_repos():
            if project_repo.name in seen:
                continue
            project_entry = project_entries[project_repo.name]
            if not project_entry.extension or project_entry.load is ExtensionLoad.none:
                continue
            if project_entry.load is None and project_entry.entry is None:
                continue
            if self._fs.is_file(project_repo.main_path / EXT_MANIFEST):
                continue
            result.append(
                StandaloneRepository(
                    name=project_repo.name,
                    path=project_repo.main_path,
                    main_branch=project_repo.main_branch,
                    load=project_entry.load,
                    entry=self._entry_candidates(project_entry.entry),
                )
            )
        return result

    def get_extension_standalone_repos(self) -> list[StandaloneRepository]:
        """Return the standalones that act as extensions — `get_standalone_repos()` minus `extension = false`.

        For the one extension consumer that is standalone-only by design
        (dashboard plugin discovery), where `get_extension_repos()` would also
        fold in project-repo extensions.
        """
        return [repo for repo in self.get_standalone_repos() if repo.extension]

    def get_non_extension_standalone_repos(self) -> list[StandaloneRepository]:
        """Return the standalones that opted out with `extension = false`.

        They keep a path-only managed exclude block (so the clone stays out of
        the workspace repo's `git status`) but project nothing.
        """
        return [repo for repo in self.get_standalone_repos() if not repo.extension]

    def get_non_extension_repos(self) -> list[StandaloneRepository]:
        """Return every declared repo whose entry opts out with `extension = false`.

        Standalones and project repos alike, each shaped as a
        `StandaloneRepository` rooted where its checkout lives (a project
        repo's `projects/<name>/` source checkout). The complement of
        `get_extension_repos()` over the declared repos: callers use it to
        retract what an earlier `winter ws init` projected for a repo that has
        since stopped being an extension.
        """
        result = self.get_non_extension_standalone_repos()
        seen = {repo.name for repo in self.get_standalone_repos()}
        project_entries = self._project_entries()
        for project_repo in self.get_project_repos():
            if project_repo.name in seen or project_entries[project_repo.name].extension:
                continue
            result.append(
                StandaloneRepository(
                    name=project_repo.name,
                    path=project_repo.main_path,
                    main_branch=project_repo.main_branch,
                    extension=False,
                )
            )
        return result

    def find_standalone(self, name: str) -> StandaloneRepository | None:
        """Resolve a standalone repo by name across both singletons and user-declared repos.

        Singletons and user-declared standalones share one lookup namespace in the
        dashboard (the standalone panel lists both), so callers that resolve a repo
        from a selected row go through here rather than re-stitching the two lists.
        """
        for repo in (*self.get_singleton_repos(), *self.get_standalone_repos()):
            if repo.name == name:
                return repo
        return None

    def _project_entries(self) -> dict[str, ProjectRepositoryConfig]:
        """Index the `[[project_repository]]` entries by resolved name."""
        return {self._resolve_project_name(r): r for r in self._config.project_repos}

    @staticmethod
    def _entry_candidates(entry: list[str] | None) -> tuple[str, ...] | None:
        return tuple(entry) if entry is not None else None

    def _resolve_project_name(self, repo: ProjectRepositoryConfig) -> str:
        if repo.name:
            return repo.name
        if repo.url:
            return self.name_from_url(repo.url)
        raise ValueError("project repo must declare either `name` or `url`")

    def _resolve_standalone_name(self, repo: StandaloneRepositoryConfig) -> str:
        if repo.name:
            return repo.name
        if repo.url:
            return self.name_from_url(repo.url)
        raise ValueError("standalone repo must declare either `name` or `url`")

    @staticmethod
    def name_from_url(url: str) -> str:
        """Derive a repo name from a clone URL.

        Takes everything after the last `/` or `:` and strips a trailing `.git`. Handles
        the SSH, HTTPS, and Azure DevOps URL shapes:
            git@github.com:paul-gross/winter.git → winter
            git@ssh.dev.azure.com:v3/paul0819/Salacia/Salacia → Salacia
            https://github.com/foo/bar.git → bar
        """
        stripped = url.rstrip("/")
        cut = max(stripped.rfind("/"), stripped.rfind(":"))
        candidate = stripped[cut + 1 :] if cut != -1 else stripped
        return candidate.removesuffix(".git")


def _conforms_standalone_repo_provider(x: RepositoryFactory) -> IStandaloneRepoProvider:
    # Typecheck-time sentinel (never called): pins RepositoryFactory as a valid
    # IStandaloneRepoProvider so the git/lifecycle seams that depend on it can't drift.
    return x


def _conforms_extension_repo_provider(x: RepositoryFactory) -> IExtensionRepoProvider:
    # Typecheck-time sentinel (never called): pins RepositoryFactory as a valid
    # IExtensionRepoProvider so the extension-consuming seams that depend on it can't drift.
    return x
