from __future__ import annotations

from winter_cli.modules.workspace.pattern_match import is_single_literal_pattern

# ── is_single_literal_pattern ─────────────────────────────────────────────────


def test_single_literal_env_svc_returns_true() -> None:
    """Single literal <env>/<svc> with no metacharacters → True."""
    assert is_single_literal_pattern(["alpha/api"]) is True


def test_bare_env_no_slash_returns_false() -> None:
    """Bare <env> (no slash) → False; it expands to all services."""
    assert is_single_literal_pattern(["alpha"]) is False


def test_wildcard_star_returns_false() -> None:
    """Single pattern with a `*` metacharacter → False."""
    assert is_single_literal_pattern(["alpha/worker-*"]) is False


def test_wildcard_question_mark_returns_false() -> None:
    """Single pattern with a `?` metacharacter → False."""
    assert is_single_literal_pattern(["alpha/worker-?"]) is False


def test_wildcard_bracket_returns_false() -> None:
    """Single pattern with a `[` metacharacter → False."""
    assert is_single_literal_pattern(["alpha/worker-[ab]"]) is False


def test_two_patterns_returns_false() -> None:
    """Two patterns → False (multi-scope regardless of content)."""
    assert is_single_literal_pattern(["alpha/api", "beta/api"]) is False


def test_empty_list_returns_false() -> None:
    """Empty pattern list → False (no patterns = all services)."""
    assert is_single_literal_pattern([]) is False


def test_cross_env_wildcard_returns_false() -> None:
    """Cross-env pattern `*/backend` contains `*` → False."""
    assert is_single_literal_pattern(["*/backend"]) is False
