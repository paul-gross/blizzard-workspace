from __future__ import annotations

import hashlib
import json

from winter_cli.modules.workspace.git_repository import IGitRepository
from winter_cli.modules.workspace.models import RepoError, RepoFingerprint, StandaloneRepository, WorkspaceFingerprint
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

# Bumped whenever the digest's preimage changes, so a digest from one serialization
# can never collide with a digest from another.
_DIGEST_VERSION = 1


class WorkspaceFingerprintService:
    """Computes the digest that identifies a workspace *definition*.

    The definition is the workspace repo's tracked content plus each standalone
    repo's name and tracked content. Project repositories, untracked files,
    generated projections, and feature environments are not part of it, so a
    workspace in use fingerprints the same as a fresh clone of the same
    definition.

    A repo's tracked content is its `HEAD^{tree}` when clean — the tree rather
    than the commit, so copies that differ only in history match — and the tree
    of its tracked content (the index brought up to the working tree) when it has
    staged or uncommitted changes to tracked files, so different edits (or a second edit to an already-dirty repo) never
    share a digest. Because the digest is over trees, moving a standalone to a
    different commit changes it exactly when that commit's content differs.

    The digest is the SHA-256 of a canonical JSON document: keys sorted, no
    insignificant whitespace, standalones ordered by name.
    """

    def __init__(self, repo_factory: RepositoryFactory, git_repo: IGitRepository) -> None:
        self._repo_factory = repo_factory
        self._git_repo = git_repo

    def compute(self) -> WorkspaceFingerprint:
        workspace_repo = self._repo_factory.get_workspace_repo()
        if workspace_repo is None:
            raise RepoError("workspace fingerprint requires the implicit workspace repository, which is not configured")
        workspace = self._fingerprint(workspace_repo)
        standalones = [
            self._fingerprint(repo) for repo in sorted(self._repo_factory.get_standalone_repos(), key=lambda r: r.name)
        ]
        return WorkspaceFingerprint(
            digest=self._digest(workspace, standalones),
            workspace=workspace,
            standalones=standalones,
        )

    def _fingerprint(self, repo: StandaloneRepository) -> RepoFingerprint:
        if not repo.path.is_dir():
            raise RepoError(f"cannot fingerprint {repo.name}: {repo.path} does not exist", cwd=str(repo.path))
        commit = self._git_repo.get_head_commit(repo.path, repo_name=repo.name, env=None)
        content_tree = self._git_repo.get_tracked_content_tree(repo.path, repo_name=repo.name, env=None)
        dirty = self._git_repo.has_tracked_changes(repo.path, repo_name=repo.name, env=None)
        return RepoFingerprint(name=repo.name, commit=commit, tree=content_tree, dirty=dirty)

    @staticmethod
    def _digest(workspace: RepoFingerprint, standalones: list[RepoFingerprint]) -> str:
        preimage = {
            "version": _DIGEST_VERSION,
            "workspace": workspace.tree,
            "standalones": [{"name": s.name, "tree": s.tree} for s in standalones],
        }
        encoded = json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
