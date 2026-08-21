from __future__ import annotations

from pathlib import Path

import pytest

from winter_cli.core.internal.local_filesystem import LocalFilesystem
from winter_cli.core.internal.tomllib_config_file_reader import TomllibConfigFileReader
from winter_cli.modules.lint.ignore_service import LintIgnoreService, compile_path_glob
from winter_cli.modules.lint.models import LintFinding, LintScope, LintScopeKind, LintStatus

# The service reads real manifests and walks a real tree (it resolves rules
# against the corpus on disk to tell a stale ignore from a live one), so these
# build a small workspace under `tmp_path` rather than faking the filesystem.


def _service(workspace: Path) -> LintIgnoreService:
    return LintIgnoreService(
        workspace_root=workspace,
        fs=LocalFilesystem(),
        config_file_reader=TomllibConfigFileReader(),
    )


def _repo(workspace: Path, name: str, manifest: str, files: dict[str, str] | None = None) -> Path:
    root = workspace / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "winter-ext.toml").write_text(manifest)
    for rel, body in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return root


def _finding(file: str, check: str = "link-anchors", status: LintStatus = LintStatus.fail) -> LintFinding:
    return LintFinding(source="wx", check=check, status=status, message="dead link", file=file)


def _scope(*paths: Path) -> LintScope:
    return LintScope(kind=LintScopeKind.env, label="env: alpha", paths=list(paths))


# ── glob semantics ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # `**` descends, and `<dir>/**` covers the directory itself.
        ("templates/**", "templates/a.md", True),
        ("templates/**", "templates/deep/nested/a.md", True),
        ("templates/**", "templates", True),
        ("templates/**", "templates-other/a.md", False),
        # `*` stays inside one segment — the whole reason this isn't fnmatch.
        ("results/*", "results/a.md", True),
        ("results/*", "results/run/a.md", False),
        ("results/**", "results/run/a.md", True),
        # `**` in the middle expands to zero or more segments.
        ("a/**/z.md", "a/z.md", True),
        ("a/**/z.md", "a/b/c/z.md", True),
        ("a/**/z.md", "a/b/c/y.md", False),
        # Leading `**`, bare `**`, `?`, and character classes.
        ("**/z.md", "z.md", True),
        ("**/z.md", "a/b/z.md", True),
        ("**", "any/thing.md", True),
        ("a?.md", "ab.md", True),
        ("a?.md", "abc.md", False),
        ("[ab].md", "a.md", True),
        ("[!ab].md", "a.md", False),
        # A literal pattern is anchored at both ends — no accidental prefixes.
        ("docs/a.md", "docs/a.md", True),
        ("docs/a.md", "other/docs/a.md", False),
        ("docs/a.md", "docs/a.md.bak", False),
    ],
)
def test_glob_matching(pattern: str, path: str, expected: bool) -> None:
    assert bool(compile_path_glob(pattern).match(path)) is expected


# ── suppression ──────────────────────────────────────────────────────────────


def test_check_scoped_rule_suppresses_only_its_own_check(tmp_path: Path) -> None:
    """The per-check dimension is the point: other checks stay live on the same file."""
    root = _repo(
        tmp_path,
        "app",
        '[lint.ignore.checks]\nlink-anchors = ["templates/**"]\n',
        {"templates/t.md": "x"},
    )
    dead_link = _finding("app/templates/t.md", check="link-anchors")
    other = _finding("app/templates/t.md", check="path-notation")

    outcome = _service(tmp_path).apply([dead_link, other], _scope(root))

    assert outcome.kept == [other]
    assert [i.finding for i in outcome.ignored] == [dead_link]
    assert outcome.ignored[0].rule.check == "link-anchors"


def test_path_rule_suppresses_every_check_on_the_path(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["templates/**"]\n', {"templates/t.md": "x"})
    findings = [_finding("app/templates/t.md", check="link-anchors"), _finding("app/templates/t.md", check="file-size")]

    outcome = _service(tmp_path).apply(findings, _scope(root))

    assert outcome.kept == []
    assert len(outcome.ignored) == 2


def test_rule_never_reaches_outside_its_own_repo(tmp_path: Path) -> None:
    """Globs are repo-relative, so one repo's ignore cannot silence another's findings."""
    declaring = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["templates/**"]\n', {"templates/t.md": "x"})
    other = _repo(tmp_path, "vendor", 'name = "vendor"\n', {"templates/t.md": "x"})
    foreign = _finding("vendor/templates/t.md")

    outcome = _service(tmp_path).apply([foreign], _scope(declaring, other))

    assert outcome.kept == [foreign]
    assert outcome.ignored == []


def test_finding_without_a_file_is_never_ignorable(tmp_path: Path) -> None:
    """Module-level failures are configuration errors, not content judgments."""
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["**"]\n', {"a.md": "x"})
    module_level = LintFinding(source="core", check="extractability", status=LintStatus.fail, message="cycle")

    outcome = _service(tmp_path).apply([module_level], _scope(root))

    assert outcome.kept == [module_level]
    assert outcome.ignored == []


def test_no_rules_anywhere_passes_every_finding_through(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", 'name = "app"\n', {"a.md": "x"})
    finding = _finding("app/a.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == [finding]
    assert (outcome.ignored, outcome.diagnostics) == ([], [])


def test_scripts_table_form_coexists_with_ignore(tmp_path: Path) -> None:
    """`[lint] scripts` is how a repo that contributes checks also declares ignores."""
    root = _repo(
        tmp_path,
        "app",
        '[lint]\nscripts = ["bin/check.py"]\n\n[lint.ignore]\npaths = ["templates/**"]\n',
        {"templates/t.md": "x"},
    )

    outcome = _service(tmp_path).apply([_finding("app/templates/t.md")], _scope(root))

    assert len(outcome.ignored) == 1


# ── staleness ────────────────────────────────────────────────────────────────


def test_rule_matching_nothing_on_disk_is_reported_stale(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["gone/**"]\n', {"a.md": "x"})

    outcome = _service(tmp_path).apply([], _scope(root))

    assert [f.status for f in outcome.diagnostics] == [LintStatus.warn]
    assert "matches no file in the repo" in outcome.diagnostics[0].message
    assert outcome.diagnostics[0].file == "app/winter-ext.toml"


def test_rule_that_suppressed_nothing_in_a_linted_tree_is_reported_stale(tmp_path: Path) -> None:
    """The decay case: whatever the rule was hiding got fixed, and the rule outlived it."""
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["templates/**"]\n', {"templates/t.md": "x"})

    outcome = _service(tmp_path).apply([], _scope(root))

    assert len(outcome.diagnostics) == 1
    assert "suppressed no finding" in outcome.diagnostics[0].message


def test_a_rule_this_run_never_linted_is_not_called_stale(tmp_path: Path) -> None:
    """A `--changed` run must not nag about ignores it had no chance to exercise."""
    root = _repo(
        tmp_path,
        "app",
        '[lint.ignore]\npaths = ["templates/**"]\n',
        {"templates/t.md": "x", "src/a.md": "y"},
    )
    changed = LintScope(kind=LintScopeKind.changed, label="changed (app)", paths=[root / "src" / "a.md"])

    outcome = _service(tmp_path).apply([], changed)

    assert outcome.diagnostics == []


def test_a_rule_that_did_its_job_is_not_stale(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["templates/**"]\n', {"templates/t.md": "x"})

    outcome = _service(tmp_path).apply([_finding("app/templates/t.md")], _scope(root))

    assert outcome.diagnostics == []


# ── rule discovery across scope kinds ────────────────────────────────────────


def test_changed_scope_maps_a_file_back_to_its_repo(tmp_path: Path) -> None:
    """Rules load by walking up from the path, so a changed *file* still finds its repo."""
    root = _repo(
        tmp_path,
        "app",
        '[lint.ignore.checks]\nlink-anchors = ["templates/**"]\n',
        {"templates/t.md": "x"},
    )
    changed = LintScope(kind=LintScopeKind.changed, label="changed (app)", paths=[root / "templates" / "t.md"])

    outcome = _service(tmp_path).apply([_finding("app/templates/t.md")], changed)

    assert len(outcome.ignored) == 1


def test_content_owned_by_no_repo_is_not_ignorable(tmp_path: Path) -> None:
    """The walk up stops at the workspace root, which owns no manifest of its own."""
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "a.md").write_text("x")
    loose = _finding("context/a.md")

    outcome = _service(tmp_path).apply([loose], _scope(tmp_path / "context"))

    assert outcome.kept == [loose]


def test_unreadable_manifest_is_reported_rather_than_raised_or_logged(tmp_path: Path) -> None:
    """The filter never fails a run — but a broken manifest must not vanish either.

    `ExtensionLintService` reads `projects/<name>/winter-ext.toml`; this service
    reads the checkout in scope. Under an env or `--changed` run a manifest
    broken in the *worktree* is a file nothing else looks at, and a log line
    would reach nobody (winter installs no handler without `--verbose`).
    """
    root = _repo(tmp_path, "app", "this is not = = toml\n", {"a.md": "x"})
    finding = _finding("app/a.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == [finding]
    assert outcome.ignored == []
    assert [d.status for d in outcome.diagnostics] == [LintStatus.fail]
    assert "cannot read winter-ext.toml" in outcome.diagnostics[0].message
    assert outcome.diagnostics[0].file == "app/winter-ext.toml"


# ── malformed globs never abort the run ──────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    ["[!]", "[]", "x[]y", "a[\\]", "[", "a[b", "[[]", "[a-", "**[", "[!"],
)
def test_every_glob_compiles(pattern: str) -> None:
    """`compile_path_glob` is total: a typo in a hand-written manifest degrades to
    literal text rather than raising `re.error` out of the middle of a lint run."""
    assert compile_path_glob(pattern).pattern


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        # A degenerate class is literal text, exactly as fnmatch treats it.
        ("[]", "[]"),
        ("x[]y", "x[]y"),
        ("[", "["),
        ("a[b", "a[b"),
        # A leading `^` is a literal member, not a negation — fnmatch again.
        ("[^ab].md", "^.md"),
    ],
)
def test_degenerate_class_is_literal(pattern: str, path: str) -> None:
    assert compile_path_glob(pattern).match(path)


def test_leading_caret_does_not_negate(tmp_path: Path) -> None:
    """`[^ab]` is a literal `^`/`a`/`b` class, so it must not match an unrelated char."""
    assert compile_path_glob("[^ab].md").match("z.md") is None


def test_a_malformed_glob_suppresses_nothing_and_does_not_raise(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["[!]"]\n', {"a.md": "x"})
    finding = _finding("app/a.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == [finding]
    assert outcome.ignored == []


# ── unusable configuration is reported, never dropped ────────────────────────


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        # A typo'd key is the worst case of all: it never becomes a rule, so
        # without this it is invisible in a way even a stale rule is not.
        ('[lint.ignore]\npath = ["templates/**"]\n', "unknown key `lint.ignore.path`"),
        ('[lint.ignore.check]\nlink-anchors = ["templates/**"]\n', "unknown key `lint.ignore.check`"),
        ('[lint.ignores]\npaths = ["templates/**"]\n', "unknown key `lint.ignores`"),
        # Wrong-typed values.
        ('[lint]\nignore = "templates/**"\n', "`lint.ignore` must be a table"),
        ("[lint.ignore]\npaths = 7\n", "`lint.ignore.paths` must be a glob or a list of globs"),
        ('[lint.ignore]\nchecks = ["a"]\n', "`lint.ignore.checks` must be a table"),
        ("[lint.ignore]\npaths = [7]\n", "contains a non-glob entry"),
        ('[lint.ignore]\npaths = ""\n', "contains an empty glob"),
    ],
)
def test_unusable_ignore_config_is_reported(tmp_path: Path, manifest: str, expected: str) -> None:
    root = _repo(tmp_path, "app", manifest, {"templates/t.md": "x"})
    finding = _finding("app/templates/t.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    # Fail-safe in the one direction that matters: config the filter cannot act
    # on suppresses nothing, so a broken ignore reveals findings, never hides them.
    assert outcome.kept == [finding]
    assert any(expected in d.message for d in outcome.diagnostics), [d.message for d in outcome.diagnostics]


def test_unknown_key_alongside_a_working_rule_reports_without_disarming_it(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        "app",
        '[lint.ignore]\npaths = ["templates/**"]\nextra = 1\n',
        {"templates/t.md": "x"},
    )

    outcome = _service(tmp_path).apply([_finding("app/templates/t.md")], _scope(root))

    assert len(outcome.ignored) == 1
    assert any("unknown key `lint.ignore.extra`" in d.message for d in outcome.diagnostics)


# ── remaining shapes ─────────────────────────────────────────────────────────


def test_a_bare_string_is_accepted_as_a_single_glob(tmp_path: Path) -> None:
    """The documented single-glob spelling, alongside the list form."""
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = "templates/**"\n', {"templates/t.md": "x"})

    outcome = _service(tmp_path).apply([_finding("app/templates/t.md")], _scope(root))

    assert len(outcome.ignored) == 1


def test_an_absolute_finding_file_still_resolves(tmp_path: Path) -> None:
    """A check reporting a file outside the workspace falls back to an absolute path."""
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["templates/**"]\n', {"templates/t.md": "x"})
    absolute = _finding(str(root / "templates" / "t.md"))

    outcome = _service(tmp_path).apply([absolute], _scope(root))

    assert len(outcome.ignored) == 1


def test_two_repos_declaring_the_same_check_each_suppress_only_their_own(tmp_path: Path) -> None:
    rule = '[lint.ignore.checks]\nlink-anchors = ["templates/**"]\n'
    first = _repo(tmp_path, "app", 'name = "app"\n' + rule, {"templates/t.md": "x"})
    second = _repo(tmp_path, "vendor", 'name = "vendor"\n' + rule, {"templates/t.md": "x"})
    findings = [_finding("app/templates/t.md"), _finding("vendor/templates/t.md")]

    outcome = _service(tmp_path).apply(findings, _scope(first, second))

    assert outcome.kept == []
    assert sorted(i.rule.repo for i in outcome.ignored) == ["app", "vendor"]


def test_a_symlink_loop_does_not_hang_the_corpus_walk(tmp_path: Path) -> None:
    root = _repo(tmp_path, "app", '[lint.ignore]\npaths = ["gone/**"]\n', {"src/a.md": "x"})
    (root / "src" / "loop").symlink_to(root)

    outcome = _service(tmp_path).apply([], _scope(root))

    assert "matches no file in the repo" in outcome.diagnostics[0].message


# ── the workspace surface ────────────────────────────────────────────────────
#
# `.winter/config.toml` speaks for repos the workspace cannot fix from here.
# These build the two-deep layout the real workspace uses (`<env>/<repo>`) so
# the name-resolution below is exercised the way it runs in practice.


def _workspace_config(workspace: Path, body: str) -> None:
    config = workspace / ".winter" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body)


def _env_repo(workspace: Path, env: str, name: str, files: dict[str, str]) -> Path:
    root = workspace / env / name
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return root


def test_workspace_rule_suppresses_a_finding_in_a_repo_with_no_manifest(tmp_path: Path) -> None:
    # The case the surface exists for: core `winter` carries no `winter-ext.toml`,
    # so it has nowhere of its own to declare an exemption.
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "winter"\n\n'
        '[lint.ignore.repo."winter".checks]\ndoc-references = ["context/setup.md"]\n',
    )
    finding = _finding("alpha/winter/context/setup.md", check="doc-references")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == []
    assert [ignored.finding for ignored in outcome.ignored] == [finding]


def test_workspace_rule_reports_itself_as_the_workspaces_own(tmp_path: Path) -> None:
    # A workspace rule must not read as if the repo declared it — the label is
    # how a reader tells which owner made the judgment.
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "winter"\n\n'
        '[lint.ignore.repo."winter".checks]\ndoc-references = ["context/setup.md"]\n',
    )

    outcome = _service(tmp_path).apply(
        [_finding("alpha/winter/context/setup.md", check="doc-references")], _scope(root)
    )

    assert outcome.ignored[0].rule.label == (
        'workspace [lint.ignore.repo."winter".checks] doc-references = "context/setup.md"'
    )


def test_workspace_repo_glob_is_repo_relative_so_it_covers_every_env(tmp_path: Path) -> None:
    # The reason `repo` exists rather than a workspace-relative path: one rule
    # has to cover the same repo in every feature env.
    alpha = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    beta = _env_repo(tmp_path, "beta", "winter", {"context/setup.md": "x"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "winter"\n\n[lint.ignore.repo."winter"]\npaths = ["context/**"]\n',
    )
    findings = [_finding("alpha/winter/context/setup.md"), _finding("beta/winter/context/setup.md")]

    outcome = _service(tmp_path).apply(findings, _scope(alpha, beta))

    assert outcome.kept == []
    assert len(outcome.ignored) == 2


def test_workspace_paths_are_workspace_relative(tmp_path: Path) -> None:
    # Content at the workspace root belongs to no repo, so nothing else can
    # speak for it — these globs resolve against the workspace itself.
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "context" / "scratch.md").write_text("x")
    _workspace_config(tmp_path, '[lint.ignore]\npaths = ["context/scratch.md"]\n')

    outcome = _service(tmp_path).apply([_finding("context/scratch.md")], _scope(tmp_path / "context"))

    assert outcome.kept == []


def test_repos_shorthand_covers_the_whole_repo(tmp_path: Path) -> None:
    root = _env_repo(tmp_path, "alpha", "vendored", {"docs/a.md": "x", "src/b.md": "y"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "vendored"\n\n[lint.ignore]\nrepos = ["vendored"]\n',
    )
    findings = [_finding("alpha/vendored/docs/a.md"), _finding("alpha/vendored/src/b.md")]

    outcome = _service(tmp_path).apply(findings, _scope(root))

    assert outcome.kept == []
    assert outcome.ignored[0].rule.label == 'workspace [lint.ignore] repos = "**"'


def test_a_workspace_rule_naming_an_undeclared_repo_is_reported(tmp_path: Path) -> None:
    # The failure the plan calls worst: a typo that silently matches nothing and
    # is invisible otherwise.
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "winter"\n\n[lint.ignore.repo."wintr"]\npaths = ["context/**"]\n',
    )
    finding = _finding("alpha/winter/context/setup.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == [finding]
    assert any("not a declared project repository" in d.message for d in outcome.diagnostics)


def test_an_unknown_workspace_ignore_key_is_reported(tmp_path: Path) -> None:
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    _workspace_config(tmp_path, '[lint.ignore]\nnonsense = ["x"]\n')

    outcome = _service(tmp_path).apply([_finding("alpha/winter/context/setup.md")], _scope(root))

    assert any("unknown key `lint.ignore.nonsense`" in d.message for d in outcome.diagnostics)


def test_a_stale_workspace_rule_is_reported_against_the_workspace_config(tmp_path: Path) -> None:
    # It must point at the file that can actually be edited to remove it.
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "winter"\n\n[lint.ignore.repo."winter"]\npaths = ["gone/**"]\n',
    )

    outcome = _service(tmp_path).apply([], _scope(root))

    stale = [d for d in outcome.diagnostics if "stale ignore rule" in d.message]
    assert len(stale) == 1
    assert stale[0].file == ".winter/config.toml"


def test_the_two_surfaces_union_rather_than_override(tmp_path: Path) -> None:
    # Neither owner can un-ignore what the other ignored.
    root = tmp_path / "alpha" / "vendored"
    root.mkdir(parents=True)
    (root / "winter-ext.toml").write_text('name = "vendored"\n\n[lint.ignore.checks]\nlink-anchors = ["own.md"]\n')
    (root / "own.md").write_text("x")
    (root / "theirs.md").write_text("x")
    _workspace_config(
        tmp_path,
        '[[project_repository]]\nname = "vendored"\n\n'
        '[lint.ignore.repo."vendored".checks]\nlink-anchors = ["theirs.md"]\n',
    )
    findings = [_finding("alpha/vendored/own.md"), _finding("alpha/vendored/theirs.md")]

    outcome = _service(tmp_path).apply(findings, _scope(root))

    assert outcome.kept == []
    owners = sorted(ignored.rule.owner or ignored.rule.repo for ignored in outcome.ignored)
    assert owners == ["vendored", "workspace"]


def test_no_workspace_config_is_not_an_error(tmp_path: Path) -> None:
    root = _env_repo(tmp_path, "alpha", "winter", {"context/setup.md": "x"})
    finding = _finding("alpha/winter/context/setup.md")

    outcome = _service(tmp_path).apply([finding], _scope(root))

    assert outcome.kept == [finding]
    assert outcome.diagnostics == []
