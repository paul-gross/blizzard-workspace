from __future__ import annotations

from winter_cli.modules.lint.finding_parser import parse_lint_output
from winter_cli.modules.lint.models import LintStatus

SOURCE = "wln"


def test_parses_each_ndjson_line_with_location() -> None:
    stdout = (
        '{"check": "path-notation", "status": "fail", "message": "bad ref", "file": "ai/x.md", "line": 12}\n'
        '{"check": "frontmatter", "status": "warn", "message": "missing model"}\n'
    )
    findings = parse_lint_output(SOURCE, stdout, "", 0)

    assert [f.check for f in findings] == ["path-notation", "frontmatter"]
    assert findings[0].status == LintStatus.fail
    assert findings[0].file == "ai/x.md"
    assert findings[0].line == 12
    assert findings[1].status == LintStatus.warn
    assert findings[1].file is None
    assert findings[1].line is None
    assert all(f.source == SOURCE for f in findings)


def test_name_is_accepted_as_alias_for_check() -> None:
    # The doctor probe contract uses `name`; lint scripts ported from a probe
    # keep working because `name` is honored as a fallback for `check`.
    findings = parse_lint_output(SOURCE, '{"name": "legacy", "status": "pass"}\n', "", 0)
    assert findings[0].check == "legacy"


def test_non_zero_exit_appends_synthetic_fail_with_stderr() -> None:
    findings = parse_lint_output(SOURCE, "", "boom", 2)
    assert len(findings) == 1
    assert findings[0].status == LintStatus.fail
    assert findings[0].message == "boom"
    assert findings[0].check == "lint"


def test_non_zero_exit_with_findings_keeps_both() -> None:
    stdout = '{"check": "c", "status": "fail", "message": "nope"}\n'
    findings = parse_lint_output(SOURCE, stdout, "", 1)
    assert [f.check for f in findings] == ["c", "lint"]


def test_unparseable_line_becomes_warn() -> None:
    findings = parse_lint_output(SOURCE, "not json\n", "", 0)
    assert len(findings) == 1
    assert findings[0].status == LintStatus.warn
    assert "unparseable" in findings[0].message


def test_missing_check_becomes_warn() -> None:
    findings = parse_lint_output(SOURCE, '{"status": "fail"}\n', "", 0)
    assert findings[0].status == LintStatus.warn
    assert "missing `check`" in findings[0].message


def test_unknown_status_becomes_warn() -> None:
    findings = parse_lint_output(SOURCE, '{"check": "c", "status": "bogus"}\n', "", 0)
    assert findings[0].status == LintStatus.warn
    assert "unknown status" in findings[0].message


def test_line_coercion_accepts_int_strings_and_ignores_bools() -> None:
    findings = parse_lint_output(
        SOURCE,
        '{"check": "a", "status": "pass", "line": "7"}\n'
        '{"check": "b", "status": "pass", "line": true}\n'
        '{"check": "c", "status": "pass", "line": 3}\n',
        "",
        0,
    )
    assert findings[0].line == 7
    assert findings[1].line is None
    assert findings[2].line == 3


def test_blank_lines_are_skipped() -> None:
    findings = parse_lint_output(SOURCE, "\n   \n", "", 0)
    assert findings == []
