from __future__ import annotations

import hashlib

GREEK_LETTERS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
]

_GREEK_INDEX = {name: i + 1 for i, name in enumerate(GREEK_LETTERS)}
_NON_GREEK_OFFSET = 26


def resolve_env_index(name: str) -> int:
    """Map a worktree name to a port-offset index.

    Greek letters get fixed indices 1..24 so port assignments stay consistent
    across workspaces. Anything else is hashed deterministically into 26..281
    via SHA-1, leaving index 25 unused as a buffer between the two ranges.

    The 256-slot bucket size is bounded by the available port range — at 100
    ports per worktree and a typical usable range of ~28K ports, a higher
    ceiling would overflow what the OS can hand out. Collisions among
    non-Greek names exist but are negligible at the 1-3 concurrent ad-hoc
    worktrees a workspace typically runs.
    """
    if name in _GREEK_INDEX:
        return _GREEK_INDEX[name]
    digest = hashlib.sha1(name.encode()).digest()
    return _NON_GREEK_OFFSET + int.from_bytes(digest[:2], "big") % 256
