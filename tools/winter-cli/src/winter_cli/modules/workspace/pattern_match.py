from __future__ import annotations

import fnmatch
from collections.abc import Iterable


def is_single_literal_pattern(patterns: Iterable[str]) -> bool:
    """Return True only when there is exactly one pattern and it is a literal <env>/<svc>.

    A literal pattern contains a `/` separator and has no glob metacharacters
    (`*`, `?`, `[`). This is the only case where multi-scope output prefixing
    should be suppressed — any other selection (bare env, wildcard, cross-env,
    multiple patterns, or no patterns) may match multiple services.
    """
    seq = list(patterns)
    if len(seq) != 1:
        return False
    p = seq[0]
    return "/" in p and not any(c in p for c in "*?[")


def matches_pattern(env_name: str, repo_name: str, pattern: str) -> bool:
    """Match `<env>/<repo>` against a segment-aware glob.

    Bare patterns (no '/') are treated as `<pattern>/*`. Each segment uses
    fnmatch — `*` matches anything within a segment, `?` matches one char.
    `*` does not cross `/`, so `*/winter` matches every env's winter worktree
    but not `alpha/winter-product`.
    """
    if "/" not in pattern:
        pattern = f"{pattern}/*"
    env_pat, repo_pat = pattern.split("/", 1)
    return fnmatch.fnmatchcase(env_name, env_pat) and fnmatch.fnmatchcase(repo_name, repo_pat)


def matches_any_pattern(env_name: str, repo_name: str, patterns: Iterable[str]) -> bool:
    return any(matches_pattern(env_name, repo_name, p) for p in patterns)
