from __future__ import annotations

import git

from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory, unwrap_gitpython_stream
from winter_cli.modules.workspace.models import RepoError


def test_unwrap_gitpython_stream_extracts_decorated_multiline_stream():
    """Real git stderr is multi-line and newline-terminated; the regex needs DOTALL to match it."""
    decorated = "\n  stderr: 'remote: Permission denied\nfatal: Could not read from remote repository.\n'"
    assert (
        unwrap_gitpython_stream(decorated)
        == "remote: Permission denied\nfatal: Could not read from remote repository.\n"
    )


def test_unwrap_gitpython_stream_passes_through_plain_string():
    assert unwrap_gitpython_stream("already plain, no decoration") == "already plain, no decoration"


def test_repo_error_str_includes_structured_fields():
    err = RepoError(
        "fetch failed",
        subcommand="fetch",
        cmd_args=("origin",),
        cwd="/tmp/repo",
        exit_code=128,
        stderr="Could not read from remote repository.",
    )
    rendered = str(err)
    assert "fetch failed" in rendered
    assert "git fetch origin" in rendered
    assert "/tmp/repo" in rendered
    assert "128" in rendered
    assert "Could not read from remote repository." in rendered


def test_repo_error_str_minimal_when_only_message():
    assert str(RepoError("boom")) == "boom"


def test_factory_from_git_extracts_fields():
    factory = RepoErrorFactory()
    exc = git.GitCommandError(
        command=["git", "fetch", "origin"],
        status=128,
        stderr="connection closed",
    )
    err = factory.from_git(exc, message="fetch failed for X", cwd="/tmp/r")
    assert isinstance(err, RepoError)
    assert err.subcommand == "fetch"
    assert err.cmd_args == ("origin",)
    assert err.cwd == "/tmp/r"
    assert err.exit_code == 128
    assert err.stderr == "connection closed"
    assert err.message == "fetch failed for X"


def test_factory_from_git_unwraps_gitpython_stderr_label():
    """GitPython decorates `.stderr` as `"\\n  stderr: '<text>'"`; RepoError.__str__ adds its
    own `stderr:` label, so an unwrapped value would render doubled as `stderr: stderr: '...'`.
    """
    factory = RepoErrorFactory()
    exc = git.GitCommandError(
        command=["git", "push", "origin"],
        status=1,
        stderr="rejected: non-fast-forward",
    )
    err = factory.from_git(exc, message="push failed", cwd="/tmp/r")
    assert err.stderr == "rejected: non-fast-forward"
    assert str(err).count("stderr:") == 1


def test_repo_error_str_renders_legible_message_for_negative_exit_code():
    """A signal-killed git op (exit=-9) must render a legible hint, not a bare negative number."""
    err = RepoError(
        "git push timed out",
        subcommand="push",
        cmd_args=("origin",),
        cwd="/tmp/repo",
        exit_code=-9,
        stderr="",
    )
    rendered = str(err)
    assert "killed by signal 9" in rendered
    # The raw exit code is still present for traceability.
    assert "-9" in rendered
