"""Shared on-disk env discovery for doctor probes."""

from __future__ import annotations

from pathlib import Path

from winter_cli.core.filesystem import IFilesystemReader


class EnvDiscoveryService:
    """Answers "which feature envs exist on disk?" for the doctor probes.

    Several probes need this question answered and must answer it identically —
    two probes that disagree about what an env is report contradictory findings
    about the same workspace. This service owns the definition so they share one
    rather than each carrying a copy that can drift apart.

    The env-index registry (``.winter/state.toml``) is the other half of
    "exists"; it is read through ``IEnvIndexRegistry`` and deliberately not
    folded in here, because the gap between the registry and the disk is itself
    a finding (see ``PortProbeService``'s registry-drift checks).
    """

    def __init__(self, fs: IFilesystemReader) -> None:
        self._fs = fs

    def discover_env_dirs(self, root: Path) -> list[str]:
        """Return the names of env directories discovered under *root*.

        An env directory is an immediate subdirectory of the workspace root
        holding at least one git-worktree child (see :meth:`has_worktree_child`).
        This marker does not depend on any env file being on disk.
        """
        env_names: list[str] = []
        if not self._fs.is_dir(root):
            return env_names
        for entry in self._fs.iterdir(root):
            if not self._fs.is_dir(entry):
                continue
            if self.has_worktree_child(entry):
                env_names.append(entry.name)
        return env_names

    def has_worktree_child(self, env_dir: Path) -> bool:
        """Return True when *env_dir* contains at least one git-worktree child.

        A git *worktree* carries a ``.git`` **file** (a ``gitdir:`` pointer back
        to the main clone), whereas a source checkout or extension directory
        carries a ``.git`` **directory**. That distinction is what separates a
        feature env from the other directories at the workspace root. Iterates
        immediate children only.
        """
        try:
            for child in self._fs.iterdir(env_dir):
                if self._fs.is_dir(child) and self._fs.is_file(child / ".git"):
                    return True
        except OSError:
            pass
        return False
