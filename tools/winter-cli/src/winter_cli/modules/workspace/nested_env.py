"""The environment a process inside a nested workspace runs with: this process's own, minus the outer workspace's."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

NESTED_CHAIN_VAR = "WINTER_NESTED_CHAIN"
"""The workspace roots whose winter started this process through a nested call, outermost first, `os.pathsep`-joined.

Every nested `winter` call extends it with the calling process's own
workspace root. A winter that finds its own root already on it was started
by a nested call yet resolved an enclosing workspace instead of the nested
one, so it refuses every nested call of its own rather than fanning out again.
"""

_KEPT_WINTER_VARS = frozenset({"WINTER_LOG_LEVEL"})
_KEPT_WINTER_PREFIXES = ("WINTER_OTEL_",)
_STRIPPED_VARS = frozenset({"VIRTUAL_ENV"})


def nested_chain(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The roots on *environ*'s `WINTER_NESTED_CHAIN`, outermost first."""
    return tuple(entry for entry in environ.get(NESTED_CHAIN_VAR, "").split(os.pathsep) if entry)


def scrubbed_env(environ: Mapping[str, str]) -> dict[str, str]:
    """*environ* minus the outer workspace's own variables, for any process run inside a nested workspace.

    Every `WINTER_*` variable is dropped except `WINTER_LOG_LEVEL` and the
    `WINTER_OTEL_*` tracing settings, so nothing the outer CLI or its shim set
    (an invocation cwd, an env's ports or prefix, the nested chain) leaks into
    the nested workspace. `VIRTUAL_ENV` is dropped too, along with its `bin`
    directory on `PATH`: the outer CLI runs inside its own virtualenv, whose
    `winter` entry point would otherwise shadow the shim and run the outer
    CLI's code against the nested root, and whose tools would otherwise come
    first for anything a nested repo's `cmd` runs.
    """
    child = {
        key: value
        for key, value in environ.items()
        if key not in _STRIPPED_VARS
        and (not key.startswith("WINTER_") or key in _KEPT_WINTER_VARS or key.startswith(_KEPT_WINTER_PREFIXES))
    }
    venv = environ.get("VIRTUAL_ENV")
    if venv and "PATH" in child:
        venv_bin = os.path.normpath(os.path.join(venv, "bin"))
        child["PATH"] = os.pathsep.join(
            entry for entry in child["PATH"].split(os.pathsep) if os.path.normpath(entry) != venv_bin
        )
    return child


def nested_child_env(environ: Mapping[str, str], caller_root: Path) -> dict[str, str]:
    """The environment a nested `winter` call runs with: `scrubbed_env`, with the chain extended by *caller_root*.

    *caller_root* is the workspace root of the process making the call — the
    chain records who called, so a child that resolves back to its caller's
    workspace finds that root on the chain and refuses to recurse.
    """
    child = scrubbed_env(environ)
    child[NESTED_CHAIN_VAR] = os.pathsep.join([*nested_chain(environ), str(caller_root.resolve())])
    return child
