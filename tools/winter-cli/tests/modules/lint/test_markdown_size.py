"""Tests for the effective-size normalization behind the file-size lint check."""

from __future__ import annotations

from winter_cli.modules.lint.markdown_size import compact, effective_bytes


def test_plain_prose_measures_as_its_raw_length() -> None:
    """Text with no runs to collapse measures exactly its UTF-8 byte length."""
    text = "# Title\nA sentence of prose.\n"
    assert effective_bytes(text) == len(text.encode())


def test_long_space_run_collapses_to_one_byte() -> None:
    """A run of 349 spaces costs the same as a single space."""
    assert effective_bytes("a" + " " * 349 + "b") == len("a b")


def test_blank_lines_and_newlines_collapse() -> None:
    """Consecutive newlines are one whitespace run, not one byte each."""
    assert effective_bytes("a\n\n\n\nb") == len("a b")


def test_table_rule_collapses_to_three_dashes() -> None:
    """A separator row measures as its shortest valid form."""
    assert compact("| ------- | ---------- |") == "| --- | --- |"


def test_short_runs_are_left_alone() -> None:
    """A run already at or below three characters is not touched."""
    assert compact("| --- |") == "| --- |"
    assert compact("a -- b") == "a -- b"


def test_repeated_letters_are_not_collapsed() -> None:
    """Only non-alphanumeric fill collapses — repeated letters are content."""
    assert compact("aaaaaaaa") == "aaaaaaaa"
    assert compact("11111111") == "11111111"


def test_underscore_and_equals_rules_collapse() -> None:
    """Other markdown rule characters collapse the same way dashes do."""
    assert compact("________") == "___"
    assert compact("========") == "==="


def test_padded_table_measures_by_its_content() -> None:
    """A dprint-padded table costs about what its unpadded equivalent costs."""
    padded = "| Column          | When to read                     |\n| --------------- | -------------------------------- |\n"
    tight = "| Column | When to read |\n| --- | --- |\n"
    assert effective_bytes(padded) == effective_bytes(tight)
