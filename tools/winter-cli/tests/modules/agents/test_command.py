"""CLI-level tests for `winter agents`.

The table-rendering smoke test drives the real DI container through
`click.testing.CliRunner`, mirroring `tests/modules/space/test_command.py`. The
stdout/stderr-separation tests run the real `python -m winter_cli.cli` entry
point in a subprocess — the installed `click.testing.CliRunner` in this repo
has no `mix_stderr` option to capture the streams separately, so a subprocess
(mirroring `tests/test_cli.py::_run_winter`) is the only way to assert stdout
carries *only* the JSON document.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from winter_cli.cli import _cli_group


def _make_workspace(root: Path, extra_config: str = "") -> Path:
    """Materialize a minimal workspace with one standalone extension shipping one agent."""
    (root / ".winter").mkdir(parents=True)
    (root / ".winter" / "config.toml").write_text(
        dedent(
            f"""
            main_branch = "master"

            [[standalone_repository]]
            name = "wf"
            {extra_config}
            """
        ).strip()
        + "\n"
    )
    ext = root / "wf"
    agents_dir = ext / "agents"
    agents_dir.mkdir(parents=True)
    (ext / "winter-ext.toml").write_text('name = "wf"\n')
    (agents_dir / "reviewer.md").write_text(
        dedent(
            """
            ---
            name: reviewer
            description: Reviews code changes
            model: sonnet
            ---
            You are a code reviewer.
            """
        ).lstrip()
    )
    return root


def _run_winter(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "winter_cli.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_agents_table_output_lists_installed_agent_and_legend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path / "ws")
    monkeypatch.chdir(ws)

    result = CliRunner().invoke(_cli_group, ["agents"])

    assert result.exit_code == 0, result.output
    assert "reviewer" in result.output
    assert "Code defaults" in result.output
    assert "Global overrides" in result.output
    assert "Agent overrides" not in result.output
    assert "Effective matrix" in result.output
    assert "Legend" in result.output


def test_agents_json_stdout_is_pure_json_with_three_sections(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "ws")

    result = _run_winter(ws, "agents", "--json")

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    payload = json.loads(stdout)
    assert set(payload) == {"tiers", "agent_overrides", "agents"}
    # Exactly one JSON document on stdout — nothing before or after it.
    assert stdout.strip().startswith("{")
    assert stdout.strip().endswith("}")
    assert stdout.count("\n") == 1

    agent_names = {a["agent"] for a in payload["agents"]}
    assert "reviewer" in agent_names


def test_agents_exits_zero_even_with_an_unresolvable_agent(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "ws")
    (ws / "wf" / "agents" / "broken.md").write_text(
        "---\nname: broken\ndescription: d\nmodel: no-such-tier\n---\n\nBody.\n"
    )

    result = _run_winter(ws, "agents", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    (broken,) = [a for a in payload["agents"] if a["agent"] == "broken" and a["harness"] == "claude"]
    assert broken["error"] is not None
    assert broken["model"] is None
    assert broken["on_disk"] is None


def test_agents_diagnostics_go_to_stderr_not_stdout(tmp_path: Path) -> None:
    """An unresolvable `[agent_model_overrides]` cell logs a warning (stderr under
    --verbose) while stdout stays the pure JSON document."""
    extra_config = dedent(
        """
        [model_tiers.claude-only]
        claude = "claude-only-id"

        [agent_model_overrides]
        reviewer = "claude-only"
        """
    )
    ws = _make_workspace(tmp_path / "ws", extra_config=extra_config)

    result = _run_winter(ws, "--verbose", "agents", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"tiers", "agent_overrides", "agents"}
    assert "claude-only" in result.stderr


def test_agents_config_load_error_exits_one_with_nothing_on_stdout(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "ws")
    (ws / ".winter" / "config.toml").write_text("not valid toml ][[\n")

    result = _run_winter(ws, "agents", "--json")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_agents_is_listed_in_top_level_help(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "ws")

    result = _run_winter(ws, "--help")

    assert result.returncode == 0, result.stderr
    commands = result.stdout.split("Commands:", 1)[1]
    assert re.search(r"^\s+agents\s", commands, re.MULTILINE)
