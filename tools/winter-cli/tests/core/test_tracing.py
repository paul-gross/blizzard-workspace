from __future__ import annotations

import pytest

from winter_cli.core.tracing import TracingSettings, non_blank, parse_launch_time_ns, parse_sdk_disabled

_NOW_NS = 1_700_000_100 * 1_000_000_000


def test_settings_default_to_no_launch_time() -> None:
    assert TracingSettings().launch_time_ns is None


@pytest.mark.parametrize(
    ("raw", "expected_ns"),
    [
        ("1700000000.123456", 1_700_000_000_123_456_000),
        ("1700000000,123456", 1_700_000_000_123_456_000),
        ("1700000000", 1_700_000_000_000_000_000),
        ("1700000000.5", 1_700_000_000_500_000_000),
        ("1700000000.1234567891234", 1_700_000_000_123_456_789),
        ("  1700000000.25\n", 1_700_000_000_250_000_000),
        (f"{_NOW_NS // 1_000_000_000}.000000", _NOW_NS),
    ],
)
def test_parse_launch_time_accepts_either_decimal_separator(raw: str, expected_ns: int) -> None:
    assert parse_launch_time_ns(raw, _NOW_NS) == expected_ns


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "garbage",
        "1700000000.12.34",
        "17000000a0.1",
        "-1700000000.1",
        "1.7e9",
        "nan",
        "0",
        "0.000000",
        "1700000000.",
        ".5",
    ],
)
def test_parse_launch_time_falls_back_to_dispatch_for_absent_or_unparseable(raw: str | None) -> None:
    assert parse_launch_time_ns(raw, _NOW_NS) is None


def test_parse_launch_time_falls_back_to_dispatch_for_a_future_instant() -> None:
    assert parse_launch_time_ns("1700000101.000000", _NOW_NS) is None
    assert parse_launch_time_ns("1700000100.000001", _NOW_NS) is None


def test_tracing_is_on_only_with_an_endpoint_and_no_kill_switch() -> None:
    assert not TracingSettings().enabled
    assert TracingSettings(otlp_endpoint="http://localhost:4318").enabled
    assert not TracingSettings(otlp_endpoint="http://localhost:4318", sdk_disabled=True).enabled
    assert not TracingSettings(sdk_disabled=True).enabled


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("TRUE", True), (" True ", True)])
def test_sdk_disabled_accepts_true_in_any_case(raw: str, expected: bool) -> None:
    assert parse_sdk_disabled(raw) is expected


@pytest.mark.parametrize("raw", [None, "", "false", "0", "yes", "tru"])
def test_sdk_disabled_is_false_for_anything_but_true(raw: str | None) -> None:
    assert parse_sdk_disabled(raw) is False


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("", None), ("  ", None), (" a b ", "a b")])
def test_non_blank_strips_and_drops_blank_values(raw: str | None, expected: str | None) -> None:
    assert non_blank(raw) == expected
