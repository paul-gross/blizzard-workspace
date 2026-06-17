from __future__ import annotations

from datetime import UTC, datetime

from winter_cli.modules.service.log_stream_processor import LogStreamProcessor
from winter_cli.modules.service.models import LogOptions


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _opts(**kwargs: object) -> LogOptions:
    defaults: dict[str, object] = {
        "patterns": (),
        "follow": False,
        "tail": 200,
        "since_rfc3339": "",
        "until_rfc3339": "",
        "timestamps": False,
    }
    defaults.update(kwargs)
    return LogOptions(**defaults)  # type: ignore[arg-type]


def _process(
    options: LogOptions,
    lines: list[str],
    since_dt: datetime | None = None,
    until_dt: datetime | None = None,
) -> list[str]:
    """Run the processor over `lines` and return all rendered output."""
    proc = LogStreamProcessor(options, since_dt, until_dt)
    result = list(proc.process_lines(lines))
    result.extend(proc.finalize())
    return result


# ── segment glob — star wildcard ──────────────────────────────────────────────


def test_segment_glob_star_keeps_matching_service_names() -> None:
    """patterns=('alpha/worker-*',) keeps worker-a and worker-b, drops api."""
    lines = [
        '{"env":"alpha","svc":"worker-a","msg":"wa"}',
        '{"env":"alpha","svc":"worker-b","msg":"wb"}',
        '{"env":"alpha","svc":"api","msg":"api-msg"}',
    ]
    out = _process(_opts(patterns=("alpha/worker-*",)), lines)
    assert any("wa" in line for line in out)
    assert any("wb" in line for line in out)
    assert not any("api-msg" in line for line in out)


# ── segment glob — ? wildcard ─────────────────────────────────────────────────


def test_segment_glob_question_mark_matches_single_char() -> None:
    """patterns=('alpha/worker-?',) keeps worker-a but drops worker-ab."""
    lines = [
        '{"env":"alpha","svc":"worker-a","msg":"wa"}',
        '{"env":"alpha","svc":"worker-ab","msg":"wab"}',
    ]
    out = _process(_opts(patterns=("alpha/worker-?",)), lines)
    assert any("wa" in line for line in out)
    assert not any("wab" in line for line in out)


# ── segment glob — [...] bracket class ───────────────────────────────────────


def test_segment_glob_bracket_class_keeps_matching_members() -> None:
    """patterns=('alpha/worker-[ab]',) keeps worker-a and worker-b, drops worker-c."""
    lines = [
        '{"env":"alpha","svc":"worker-a","msg":"wa"}',
        '{"env":"alpha","svc":"worker-b","msg":"wb"}',
        '{"env":"alpha","svc":"worker-c","msg":"wc"}',
    ]
    out = _process(_opts(patterns=("alpha/worker-[ab]",)), lines)
    assert any("wa" in line for line in out)
    assert any("wb" in line for line in out)
    assert not any("wc" in line for line in out)


# ── segment non-crossing: * does not cross / ─────────────────────────────────


def test_segment_star_does_not_cross_slash() -> None:
    """alpha/* matches alpha/api but the env segment is matched separately."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"alpha-api"}',
        '{"env":"beta","svc":"api","msg":"beta-api"}',
    ]
    out = _process(_opts(patterns=("alpha/*",)), lines)
    assert any("alpha-api" in line for line in out)
    assert not any("beta-api" in line for line in out)


# ── cross-env: */backend ──────────────────────────────────────────────────────


def test_cross_env_star_env_segment_keeps_matching_env_svc_pairs() -> None:
    """patterns=('*/backend',) keeps alpha/backend and beta/backend, drops alpha/api."""
    lines = [
        '{"env":"alpha","svc":"backend","msg":"ab"}',
        '{"env":"beta","svc":"backend","msg":"bb"}',
        '{"env":"alpha","svc":"api","msg":"api-msg"}',
    ]
    out = _process(_opts(patterns=("*/backend",)), lines)
    assert any("ab" in line for line in out)
    assert any("bb" in line for line in out)
    assert not any("api-msg" in line for line in out)


# ── env scoping ───────────────────────────────────────────────────────────────


def test_env_scoping_drops_same_svc_in_different_env() -> None:
    """patterns=('alpha/api',) keeps alpha/api but drops beta/api."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"alpha-api"}',
        '{"env":"beta","svc":"api","msg":"beta-api"}',
    ]
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert any("alpha-api" in line for line in out)
    assert not any("beta-api" in line for line in out)


# ── no match ─────────────────────────────────────────────────────────────────


def test_no_match_yields_empty_result() -> None:
    """A pattern matching nothing yields an empty result list."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"m1"}',
        '{"env":"alpha","svc":"db","msg":"m2"}',
    ]
    out = _process(_opts(patterns=("alpha/nonexistent-*",)), lines)
    assert out == []


# ── missing env or svc dropped when filter active ────────────────────────────


def test_line_missing_env_is_dropped_when_filter_active() -> None:
    """A line without an `env` field is dropped when a filter is active."""
    lines = ['{"svc":"api","msg":"no-env-field"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == []


def test_line_missing_svc_is_dropped_when_filter_active() -> None:
    """A line without a `svc` field is dropped when a filter is active."""
    lines = ['{"env":"alpha","msg":"no-svc-field"}']
    out = _process(_opts(patterns=("alpha/*",)), lines)
    assert out == []


def test_line_missing_both_env_and_svc_is_dropped_when_filter_active() -> None:
    """A line without env or svc is dropped when a filter is active."""
    lines = ['{"msg":"no-env-no-svc"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == []


# ── multi-pattern union ───────────────────────────────────────────────────────


def test_multi_pattern_union_keeps_all_matching() -> None:
    """patterns=('alpha/api','beta/worker-*') keeps both matches, drops others."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"alpha-api"}',
        '{"env":"beta","svc":"worker-a","msg":"beta-worker"}',
        '{"env":"alpha","svc":"db","msg":"alpha-db"}',
        '{"env":"beta","svc":"api","msg":"beta-api"}',
    ]
    out = _process(_opts(patterns=("alpha/api", "beta/worker-*")), lines)
    assert any("alpha-api" in line for line in out)
    assert any("beta-worker" in line for line in out)
    assert not any("alpha-db" in line for line in out)
    assert not any("beta-api" in line for line in out)


# ── render prefix: <env>/<svc> | in multi-scope; none in single pattern ───────


def test_multi_scope_prefix_is_env_slash_svc() -> None:
    """Multi-scope (≥2 patterns or wildcard) prefixes with <env>/<svc> | ."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"alpha-api-msg"}',
        '{"env":"beta","svc":"backend","msg":"beta-backend-msg"}',
    ]
    out = _process(_opts(patterns=("alpha/api", "beta/backend")), lines)
    assert any("alpha/api |" in line for line in out)
    assert any("beta/backend |" in line for line in out)


def test_single_pattern_no_prefix() -> None:
    """Single pattern → no prefix."""
    lines = ['{"env":"alpha","svc":"api","msg":"msg"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == ["msg"]


def test_wildcard_pattern_is_multi_scope_adds_prefix() -> None:
    """A single wildcard pattern is multi-scope → prefix IS added.

    A single literal `<env>/<svc>` (no metacharacters) is the only case that
    suppresses the prefix. A wildcard like `alpha/*` may match multiple services,
    so the prefix is required to keep merged output attributable.
    """
    lines = ['{"env":"alpha","svc":"api","msg":"msg"}']
    out = _process(_opts(patterns=("alpha/*",)), lines)
    # Single wildcard → multi-scope → prefix applied.
    assert "alpha/api |" in out[0]
    assert "msg" in out[0]


def test_two_patterns_multi_scope_adds_env_svc_prefix() -> None:
    """Two patterns → multi-scope → <env>/<svc> | prefix."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"hi"}',
    ]
    out = _process(_opts(patterns=("alpha/api", "alpha/db")), lines)
    assert "alpha/api |" in out[0]
    assert "hi" in out[0]


# ── since / until with ts ─────────────────────────────────────────────────────


def test_since_drops_lines_before_threshold() -> None:
    lines = [
        '{"env":"alpha","ts":"2026-06-13T10:00:00Z","svc":"api","msg":"too-old"}',
        '{"env":"alpha","ts":"2026-06-13T10:00:05Z","svc":"api","msg":"fresh"}',
    ]
    since = _dt("2026-06-13T10:00:03Z")
    out = _process(_opts(patterns=("alpha/api",)), lines, since_dt=since)
    assert out == ["fresh"]


def test_since_boundary_is_inclusive() -> None:
    """A line whose ts exactly equals the since threshold is kept (inclusive boundary)."""
    threshold = _dt("2026-06-13T10:00:03Z")
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:03Z","svc":"api","msg":"at-boundary"}']
    out = _process(_opts(patterns=("alpha/api",)), lines, since_dt=threshold)
    assert out == ["at-boundary"]


def test_until_drops_lines_after_threshold() -> None:
    lines = [
        '{"env":"alpha","ts":"2026-06-13T09:59:58Z","svc":"api","msg":"old"}',
        '{"env":"alpha","ts":"2026-06-13T10:00:10Z","svc":"api","msg":"future"}',
    ]
    until = _dt("2026-06-13T10:00:00Z")
    out = _process(_opts(patterns=("alpha/api",)), lines, until_dt=until)
    assert out == ["old"]


def test_until_boundary_is_inclusive() -> None:
    """A line whose ts exactly equals the until threshold is kept (inclusive boundary)."""
    threshold = _dt("2026-06-13T10:00:00Z")
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:00Z","svc":"api","msg":"at-boundary"}']
    out = _process(_opts(patterns=("alpha/api",)), lines, until_dt=threshold)
    assert out == ["at-boundary"]


def test_since_until_combined() -> None:
    lines = [
        '{"env":"alpha","ts":"2026-06-13T09:00:00Z","svc":"api","msg":"before"}',
        '{"env":"alpha","ts":"2026-06-13T10:00:00Z","svc":"api","msg":"in-window"}',
        '{"env":"alpha","ts":"2026-06-13T11:00:00Z","svc":"api","msg":"after"}',
    ]
    since = _dt("2026-06-13T09:30:00Z")
    until = _dt("2026-06-13T10:30:00Z")
    out = _process(_opts(patterns=("alpha/api",)), lines, since_dt=since, until_dt=until)
    assert out == ["in-window"]


# ── since / until with lines that have no ts ─────────────────────────────────


def test_tsless_lines_kept_when_time_filter_active() -> None:
    lines = [
        '{"env":"alpha","svc":"api","msg":"no-timestamp"}',
        '{"env":"alpha","ts":"2026-06-13T10:00:00Z","svc":"api","msg":"in-window"}',
    ]
    since = _dt("2026-06-13T09:00:00Z")
    proc = LogStreamProcessor(_opts(patterns=("alpha/api",)), since, None)
    result = list(proc.process_lines(lines))
    result.extend(proc.finalize())
    # Both lines kept; tsless line cannot be time-filtered.
    assert "no-timestamp" in result
    assert "in-window" in result


def test_tsless_line_sets_time_filter_warning() -> None:
    lines = ['{"env":"alpha","svc":"api","msg":"no-ts"}']
    since = _dt("2026-06-13T09:00:00Z")
    proc = LogStreamProcessor(_opts(patterns=("alpha/api",)), since, None)
    list(proc.process_lines(lines))
    assert proc.time_filter_warning is True


def test_no_tsless_no_warning() -> None:
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:00Z","svc":"api","msg":"ok"}']
    since = _dt("2026-06-13T09:00:00Z")
    proc = LogStreamProcessor(_opts(patterns=("alpha/api",)), since, None)
    list(proc.process_lines(lines))
    assert proc.time_filter_warning is False


# ── tail ring-buffer (non-follow) ─────────────────────────────────────────────


def test_tail_limits_output_to_last_n_lines() -> None:
    lines = [f'{{"env":"alpha","svc":"api","msg":"line-{i}"}}' for i in range(10)]
    # Two patterns → multi-scope prefix. Use single pattern to get bare msgs.
    out = _process(_opts(patterns=("alpha/api",), tail=3), lines)
    assert len(out) == 3
    assert "line-7" in out[0]
    assert "line-8" in out[1]
    assert "line-9" in out[2]


def test_tail_all_returns_all_lines() -> None:
    lines = [f'{{"env":"alpha","svc":"api","msg":"line-{i}"}}' for i in range(5)]
    out = _process(_opts(patterns=("alpha/api",), tail="all"), lines)
    assert len(out) == 5


def test_tail_zero_lines_returns_zero_in_non_follow() -> None:
    lines = [f'{{"env":"alpha","svc":"api","msg":"line-{i}"}}' for i in range(5)]
    # tail=0 would be invalid via CLI (positive int required), but
    # processor handles deque(maxlen=0) gracefully — no output.
    proc = LogStreamProcessor(_opts(tail=0, follow=False, patterns=("alpha/api",)), None, None)
    result = list(proc.process_lines(lines))
    result.extend(proc.finalize())
    assert result == []


# ── follow skips tail ─────────────────────────────────────────────────────────


def test_follow_mode_emits_lines_immediately_without_tail() -> None:
    """In follow mode the ring buffer is None and lines are yielded in process_lines."""
    lines = [f'{{"env":"alpha","svc":"api","msg":"line-{i}"}}' for i in range(10)]
    proc = LogStreamProcessor(_opts(follow=True, tail=3, patterns=("alpha/api",)), None, None)
    from_process = list(proc.process_lines(lines))
    from_finalize = list(proc.finalize())
    # All 10 lines come out of process_lines; finalize is a no-op.
    assert len(from_process) == 10
    assert from_finalize == []


# ── timestamps rendering ──────────────────────────────────────────────────────


def test_timestamps_flag_prepends_ts() -> None:
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:01Z","svc":"api","msg":"hi"}']
    out = _process(_opts(patterns=("alpha/api",), timestamps=True), lines)
    assert out[0].startswith("2026-06-13T10:00:01Z")
    assert "hi" in out[0]


def test_timestamps_with_tsless_line_sets_warning() -> None:
    lines = ['{"env":"alpha","svc":"api","msg":"no-ts"}']
    proc = LogStreamProcessor(_opts(patterns=("alpha/api",), timestamps=True), None, None)
    result = list(proc.process_lines(lines))
    result.extend(proc.finalize())
    # Message still rendered even without ts prefix.
    assert "no-ts" in result[0]
    assert proc.timestamps_warning is True


def test_timestamps_flag_false_no_warning() -> None:
    lines = ['{"env":"alpha","svc":"api","msg":"no-ts"}']
    proc = LogStreamProcessor(_opts(patterns=("alpha/api",), timestamps=False), None, None)
    list(proc.process_lines(lines))
    assert proc.timestamps_warning is False


# ── malformed / non-JSON lines (lenient handling) ─────────────────────────────


def test_malformed_json_not_dropped_by_empty_pattern_list() -> None:
    """With empty patterns (no filter active), non-JSON line is kept as plain msg."""
    lines = ["not-json at all"]
    out = _process(_opts(patterns=()), lines)
    assert out == ["not-json at all"]


def test_malformed_json_dropped_when_filter_active() -> None:
    """With a filter active, non-JSON line has no env/svc and is dropped."""
    lines = ["not-json at all"]
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == []


def test_partial_json_no_env_no_svc_kept_when_no_pattern_filter() -> None:
    lines = ['{"msg":"partial"}']
    out = _process(_opts(patterns=()), lines)
    assert "partial" in out[0]


def test_partial_json_no_env_dropped_when_filter_active() -> None:
    """A line without an `env` field is dropped when a pattern filter is active."""
    lines = ['{"svc":"api","msg":"no-env"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == []


def test_partial_json_no_svc_dropped_when_filter_active() -> None:
    """A line without a `svc` field is dropped when a pattern filter is active."""
    lines = ['{"env":"alpha","msg":"no-svc"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == []


def test_empty_line_does_not_crash() -> None:
    lines = [""]
    out = _process(_opts(patterns=()), lines)
    # Empty line produces an empty-ish output or is lenient — no crash.
    assert isinstance(out, list)


# ── multi-service prefix rule ─────────────────────────────────────────────────


def test_svc_prefix_only_when_multiple_patterns_in_options() -> None:
    """Single literal pattern → no prefix. Multi patterns → <env>/<svc> | prefix."""
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:01Z","svc":"api","msg":"msg"}']
    # Single explicit pattern → no prefix.
    out_single = _process(_opts(patterns=("alpha/api",)), lines)
    assert out_single == ["msg"]

    # Two patterns → multi-service scope → prefix applied.
    out_multi = _process(_opts(patterns=("alpha/api", "alpha/db")), lines)
    assert "alpha/api |" in out_multi[0]


def test_empty_patterns_no_filter_multi_scope_prefix() -> None:
    """Empty patterns = no filter, but multi-scope (not a single literal) → prefix applied."""
    lines = ['{"env":"alpha","ts":"2026-06-13T10:00:01Z","svc":"api","msg":"msg"}']
    out = _process(_opts(patterns=()), lines)
    # zero patterns → not a single literal → multi-scope → prefix.
    assert "alpha/api |" in out[0]


# ── prefix matrix: locked to the correct heuristic ───────────────────────────


def test_prefix_matrix_single_literal_no_prefix() -> None:
    """Single literal `<env>/<svc>` → no prefix (the only suppression case)."""
    lines = ['{"env":"alpha","svc":"api","msg":"msg"}']
    out = _process(_opts(patterns=("alpha/api",)), lines)
    assert out == ["msg"]


def test_prefix_matrix_bare_env_gets_prefix() -> None:
    """Bare `<env>` (no slash) expands to all services → multi-scope → prefix."""
    lines = ['{"env":"alpha","svc":"api","msg":"msg"}']
    out = _process(_opts(patterns=("alpha",)), lines)
    assert "alpha/api |" in out[0]
    assert "msg" in out[0]


def test_prefix_matrix_single_wildcard_gets_prefix() -> None:
    """Single wildcard pattern → may match multiple services → multi-scope → prefix."""
    lines = [
        '{"env":"alpha","svc":"worker-a","msg":"wa"}',
        '{"env":"alpha","svc":"worker-b","msg":"wb"}',
    ]
    out = _process(_opts(patterns=("alpha/worker-*",)), lines)
    assert any("alpha/worker-a |" in line for line in out)
    assert any("alpha/worker-b |" in line for line in out)


def test_prefix_matrix_two_patterns_get_prefix() -> None:
    """Two patterns → multi-scope → prefix on every matching line."""
    lines = [
        '{"env":"alpha","svc":"api","msg":"alpha-msg"}',
        '{"env":"beta","svc":"api","msg":"beta-msg"}',
    ]
    out = _process(_opts(patterns=("alpha/api", "beta/api")), lines)
    assert any("alpha/api |" in line for line in out)
    assert any("beta/api |" in line for line in out)
