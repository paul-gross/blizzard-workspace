from __future__ import annotations

import re
from pathlib import Path

from winter_cli.core.config_file import ConfigFileReadError, IConfigFileReader
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.lint.models import (
    IgnoredFinding,
    LintFinding,
    LintIgnoreOutcome,
    LintIgnoreRule,
    LintScope,
    LintStatus,
)
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST

# Source label for the dispatcher's own findings about ignore rules. Shares
# `core`'s group because this check ships with winter-cli and always runs, the
# same tier as the built-in checks.
IGNORE_SOURCE = "core"

# Check name for every finding this filter raises about the ignore config itself.
IGNORE_CHECK = "lint-ignore"

# The keys each level of the manifest's lint config accepts. Anything else is a
# typo the author expects to be doing something, so it is reported rather than
# dropped — a rule that never becomes a rule is worse than a stale one, because
# nothing about it is visible at all.
LINT_KEYS = frozenset({"scripts", "ignore"})
IGNORE_KEYS = frozenset({"paths", "checks"})

# Directories never worth walking when resolving a rule against the corpus.
# Mirrors the core file-size check's prune list.
_PRUNE_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"})


def compile_path_glob(pattern: str) -> re.Pattern[str]:
    """Compile a repo-relative path glob into an anchored regex.

    `**` descends recursively; `*` and `?` stay inside one path segment, so
    `results/*` covers `results/a.md` but not `results/a/b.md` — write
    `results/**` for the whole tree. `<dir>/**` also matches `<dir>` itself, so
    a rule can be resolved against directories as well as files. Character
    classes work as in `fnmatch`: `[abc]`, `[a-z]`, and `[!abc]` to negate. A
    leading `^` is a literal member, and a `]` directly after `[` or `[!`
    closes nothing — again matching `fnmatch`.

    **Total over arbitrary input.** Every string compiles; a malformed pattern
    degrades to literal text rather than raising. A glob comes from a
    hand-written manifest, and a typo there must not abort a lint run.

    This is segment-aware where `lint_doc_references.py`'s `--allow` uses plain
    `fnmatch`, and the two are *not* equivalent — but every divergence is this
    one matching **more narrowly**, which is the point: `fnmatch`'s `*` crosses
    `/`, and an accidentally over-broad glob in a *suppression* rule hides
    findings nobody asked to hide. The `**` forms also differ at their edges —
    here `a/**` covers `a`, `**/a` covers `a`, and `a/**/b` covers `a/b`,
    because `**` stands for "zero or more segments" rather than "one or more".
    """
    segments = pattern.split("/")
    parts: list[str] = []
    for index, segment in enumerate(segments):
        if segment == "**":
            if index == 0:
                parts.append(".*" if len(segments) == 1 else "(?:.*/)?")
            else:
                # Absorbs the separator before it, so `a/**` matches `a` too.
                parts.append("(?:/.*)?")
            continue
        if index > 0 and (segments[index - 1] != "**" or index > 1):
            parts.append("/")
        parts.append(_segment_regex(segment))
    return re.compile(f"(?s:{''.join(parts)})\\Z")


def _segment_regex(segment: str) -> str:
    """Translate one glob path segment — wildcards never cross `/`."""
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            cls, index = _class_regex(segment, index)
            out.append(cls)
            continue
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


def _class_regex(segment: str, start: int) -> tuple[str, int]:
    """Translate the character class opening at `start`; returns it and the next index.

    A `]` directly after `[` or `[!` is a literal member, so the scan for the
    real close begins past it. An unterminated class is not a class at all: the
    `[` degrades to a literal, exactly as `fnmatch` treats it — which is what
    keeps this function, and so `compile_path_glob`, total.
    """
    cursor = start + 1
    if cursor < len(segment) and segment[cursor] == "!":
        cursor += 1
    if cursor < len(segment) and segment[cursor] == "]":
        cursor += 1
    close = segment.find("]", cursor)
    if close == -1:
        return re.escape(segment[start]), start + 1

    # No `]` can appear in the body (it is the delimiter). Backslash needs
    # escaping to stay a literal member, and `[` needs it to keep Python from
    # warning about a possible nested set. `-` is left alone — it is how ranges
    # are spelled. `!` negates; a leading `^` is a literal member, matching
    # fnmatch rather than regex.
    body = segment[start + 1 : close].replace("\\", "\\\\").replace("[", "\\[")
    if body.startswith("!"):
        body = "^" + body[1:]
    elif body.startswith("^"):
        body = "\\^" + body[1:]
    return f"[{body}]", close + 1


class LintIgnoreService:
    """Applies each linted repo's `[lint.ignore]` declarations to a run's findings.

    One filter for every check. `LintService` flattens core, workspace, and
    extension outcomes into a single finding list before reporting, so a filter
    at that seam covers all of them — no lint script parses ignore config, every
    script stays stateless and independently runnable, and every future
    contributed check inherits ignore support for free. The cost is that
    suppression is post-hoc: the work is still done, so this buys correctness,
    not speed. A check that *crashes* on a file therefore cannot be silenced
    here — suppressing a finding and suppressing a failure are different
    problems, and this solves only the first.

    Only the repo-owned surface exists today. A repo installable in any winter
    workspace must lint clean in any winter workspace, so a repo that
    legitimately carries unresolvable content — templates, fixtures, recorded
    results — declares that itself, in its own manifest, and ships clean
    everywhere. The workspace-level mirror (a `[lint.ignore]` in
    `.winter/config.toml`, for findings in a repo the workspace does not
    control and has no standing to fix) is deliberately unbuilt until a real
    third-party case turns up.

    **This service never raises and never fails a run.** Everything that can go
    wrong with an ignore declaration — an unreadable manifest, an unknown key, a
    wrong-typed value, a malformed glob, a rule that has outlived its purpose —
    comes back as a `LintFinding` in `LintIgnoreOutcome.diagnostics` and travels
    the same reporter channel as any other finding. That is the only channel a
    default run shows: `winter` installs no log handler unless `--verbose` or
    `WINTER_LOG_LEVEL` is set, so a `logger.warning` here would reach nobody.
    Every failure mode is fail-safe in the same direction, too — configuration
    this service cannot act on suppresses nothing, so a broken ignore reveals
    findings rather than hiding them.
    """

    def __init__(
        self,
        workspace_root: Path,
        fs: IFilesystemReader,
        config_file_reader: IConfigFileReader,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._fs = fs
        self._config_file_reader = config_file_reader
        # One walk per repo, shared by every rule that repo declares.
        self._repo_file_cache: dict[Path, list[tuple[str, Path]]] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def apply(self, findings: list[LintFinding], scope: LintScope) -> LintIgnoreOutcome:
        """Partition `findings` against the ignore rules in scope, and audit the rules.

        Returns the surviving findings, the suppressed ones paired with the rule
        that matched, and the filter's own findings about the ignore
        configuration — see `_stale_findings` and the class docstring.
        """
        scope_paths = [path.resolve() for path in scope.paths]
        rules, diagnostics = self._load(scope_paths)
        if not rules:
            return LintIgnoreOutcome(kept=list(findings), ignored=[], diagnostics=diagnostics)

        matchers: dict[LintIgnoreRule, re.Pattern[str]] = {}
        for rule in rules:
            try:
                matchers[rule] = compile_path_glob(rule.glob)
            except re.error as exc:
                # `compile_path_glob` is total, so this is unreachable today. It
                # stays because the alternative failure mode — a traceback out
                # of `winter lint` — is one this service promises never to have,
                # and that promise should not rest on one function's totality
                # holding forever.
                diagnostics.append(self._config_finding(rule.repo_root, f"unusable glob in {rule.label} — {exc}"))
        live = [rule for rule in rules if rule in matchers]

        kept: list[LintFinding] = []
        ignored: list[IgnoredFinding] = []
        used: set[LintIgnoreRule] = set()

        for finding in findings:
            rule = self._first_match(finding, live, matchers)
            if rule is None:
                kept.append(finding)
                continue
            used.add(rule)
            ignored.append(IgnoredFinding(finding=finding, rule=rule))

        diagnostics.extend(self._stale_findings(live, matchers, used, scope_paths))
        return LintIgnoreOutcome(kept=kept, ignored=ignored, diagnostics=diagnostics)

    # ── rule loading ─────────────────────────────────────────────────────────

    def _load(self, scope_paths: list[Path]) -> tuple[list[LintIgnoreRule], list[LintFinding]]:
        """Every `[lint.ignore]` rule declared by a repo this scope reaches into."""
        rules: list[LintIgnoreRule] = []
        diagnostics: list[LintFinding] = []
        for root in self._ignore_roots(scope_paths):
            found, problems = self._rules_for(root)
            rules.extend(found)
            diagnostics.extend(problems)
        return rules, diagnostics

    def _ignore_roots(self, scope_paths: list[Path]) -> list[Path]:
        """The distinct manifest-bearing repo roots the scope's paths belong to.

        Derived by walking up from each scope path rather than from the repo
        list, so every scope kind resolves the same way: a `repo` scope lands on
        a source checkout, `env` / `all` on per-env worktrees, and `--changed`
        on whatever repo the individual changed *files* sit in — the case a
        repo-list lookup would miss, since a changed file carries no repo of its
        own. It also means a repo's rules are read from the checkout being
        linted, matching how the extractability check reads that checkout's
        `requires`.

        This diverges deliberately from `RepositoryFactory.get_extension_repos()`,
        which resolves every *other* field of the same manifest from the source
        checkout and never from a worktree copy. So `winter lint alpha` applies
        alpha's ignore rules while running the source checkout's lint scripts.
        The split is intended — an ignore declares something about the content
        being linted, and under an env scope that content is the worktree's —
        but it does mean the two halves of one file can disagree while a branch
        is in flight.
        """
        roots: list[Path] = []
        seen: set[Path] = set()
        for path in scope_paths:
            root = self._owning_repo_root(path)
            if root is None or root in seen:
                continue
            seen.add(root)
            roots.append(root)
        return roots

    def _owning_repo_root(self, path: Path) -> Path | None:
        """Nearest ancestor of `path` (or `path` itself) carrying a `winter-ext.toml`.

        Bounded by the workspace root, which is the core `workspace` module and
        owns no manifest of its own — content that reaches it belongs to no repo
        and is not ignorable by a repo-owned rule.
        """
        current = (path if self._fs.is_dir(path) else path.parent).resolve()
        while True:
            if self._fs.is_file(current / EXT_MANIFEST):
                return current
            if current == self._workspace_root or current.parent == current:
                return None
            current = current.parent

    def _rules_for(self, root: Path) -> tuple[list[LintIgnoreRule], list[LintFinding]]:
        """Parse one repo's `[lint.ignore]` table into rules, reporting what it cannot use."""
        manifest_path = root / EXT_MANIFEST
        try:
            data = self._config_file_reader.load(manifest_path)
        except ConfigFileReadError as exc:
            # Reported here rather than left to `ExtensionLintService`: that
            # service reads `projects/<name>/winter-ext.toml`, while this one
            # reads the checkout in scope, so under an `env` / `all` /
            # `--changed` run a manifest broken *in the worktree* is a file
            # nothing else looks at.
            return [], [self._config_finding(root, f"cannot read {EXT_MANIFEST} — {exc}", LintStatus.fail)]

        lint_table = data.get("lint")
        if not isinstance(lint_table, dict):
            # `lint` is also the scalar/list script field; only the table form
            # can carry `ignore`.
            return [], []

        diagnostics = [
            self._config_finding(root, f"unknown key `lint.{key}` — expected one of {_names(LINT_KEYS)}")
            for key in sorted(lint_table)
            if key not in LINT_KEYS
        ]

        ignore = lint_table.get("ignore")
        if ignore is None:
            return [], diagnostics
        if not isinstance(ignore, dict):
            diagnostics.append(self._config_finding(root, "`lint.ignore` must be a table"))
            return [], diagnostics

        diagnostics.extend(
            self._config_finding(root, f"unknown key `lint.ignore.{key}` — expected one of {_names(IGNORE_KEYS)}")
            for key in sorted(ignore)
            if key not in IGNORE_KEYS
        )

        repo = str(data["name"]) if isinstance(data.get("name"), str) and data.get("name") else root.name
        rules: list[LintIgnoreRule] = [
            LintIgnoreRule(repo=repo, repo_root=root, glob=glob, check=None)
            for glob in self._globs(ignore.get("paths"), root, "lint.ignore.paths", diagnostics)
        ]

        checks = ignore.get("checks")
        if checks is not None and not isinstance(checks, dict):
            diagnostics.append(self._config_finding(root, "`lint.ignore.checks` must be a table of check names"))
        elif isinstance(checks, dict):
            for check in sorted(checks):
                globs = self._globs(checks[check], root, f"lint.ignore.checks.{check}", diagnostics)
                rules.extend(LintIgnoreRule(repo=repo, repo_root=root, glob=glob, check=check) for glob in globs)
        return rules, diagnostics

    def _globs(self, raw: object, root: Path, key: str, diagnostics: list[LintFinding]) -> list[str]:
        """Coerce a `str | list[str]` ignore entry, reporting anything it has to drop."""
        if raw is None:
            return []
        if isinstance(raw, str):
            if raw:
                return [raw]
            diagnostics.append(self._config_finding(root, f"`{key}` contains an empty glob"))
            return []
        if not isinstance(raw, list):
            diagnostics.append(self._config_finding(root, f"`{key}` must be a glob or a list of globs"))
            return []
        globs: list[str] = []
        for item in raw:
            if isinstance(item, str) and item:
                globs.append(item)
            else:
                diagnostics.append(self._config_finding(root, f"`{key}` contains a non-glob entry {item!r}"))
        return globs

    def _config_finding(self, root: Path, message: str, status: LintStatus = LintStatus.warn) -> LintFinding:
        return LintFinding(
            source=IGNORE_SOURCE,
            check=IGNORE_CHECK,
            status=status,
            message=message,
            file=self._manifest_relpath(root),
            remediation="Fix the ignore declaration — as written it suppresses nothing.",
        )

    # ── matching ─────────────────────────────────────────────────────────────

    def _first_match(
        self,
        finding: LintFinding,
        rules: list[LintIgnoreRule],
        matchers: dict[LintIgnoreRule, re.Pattern[str]],
    ) -> LintIgnoreRule | None:
        """The first rule that suppresses `finding`, or None.

        A finding with no `file` is never ignorable: module-level failures — an
        unset `WINTER_CLI`, a `requires` cycle — are configuration errors, not
        content judgments, and silencing them is out of scope.
        """
        if not finding.file:
            return None
        absolute = self._absolute(finding.file)
        for rule in rules:
            if rule.check is not None and rule.check != finding.check:
                continue
            relative = _relative_to(absolute, rule.repo_root)
            if relative is not None and matchers[rule].match(relative):
                return rule
        return None

    def _absolute(self, file: str) -> Path:
        """Resolve a finding's `file` — reported workspace-relative — to an absolute path.

        A check that reports a file outside the workspace falls back to an
        absolute path (`FileSizeLintCheck._relpath` does), so both forms arrive
        here.
        """
        path = Path(file)
        return (path if path.is_absolute() else self._workspace_root / path).resolve()

    # ── staleness ────────────────────────────────────────────────────────────

    def _stale_findings(
        self,
        rules: list[LintIgnoreRule],
        matchers: dict[LintIgnoreRule, re.Pattern[str]],
        used: set[LintIgnoreRule],
        scope_paths: list[Path],
    ) -> list[LintFinding]:
        """One `warn` per rule that is no longer telling the truth about the corpus.

        Two ways a rule goes stale, and both are reported:

        * **It matches no path in the repo at all.** The tree it named was
          renamed or deleted. Scope-independent, so this is always reported.
        * **It matched paths this run actually linted, and suppressed nothing.**
          Whatever it was hiding got fixed, and the rule outlived it.

        A rule whose paths were simply *not linted this run* — the common case
        under `--changed` — is judged neither way and stays silent, so a
        narrow run never nags about ignores it had no chance to exercise.
        """
        findings: list[LintFinding] = []
        for rule in rules:
            if rule in used:
                continue
            covered = [entry for entry in self._repo_files(rule.repo_root) if matchers[rule].match(entry[0])]
            if not covered:
                findings.append(self._stale_finding(rule, "matches no file in the repo"))
                continue
            if any(_in_scope(absolute, scope_paths) for _, absolute in covered):
                findings.append(self._stale_finding(rule, "suppressed no finding"))
        return findings

    def _stale_finding(self, rule: LintIgnoreRule, reason: str) -> LintFinding:
        return LintFinding(
            source=IGNORE_SOURCE,
            check=IGNORE_CHECK,
            status=LintStatus.warn,
            message=f"stale ignore rule — {rule.label} {reason}",
            file=self._manifest_relpath(rule.repo_root),
            remediation="Delete the rule. An ignore that suppresses nothing is a false claim about the repo.",
        )

    def _manifest_relpath(self, root: Path) -> str:
        return _relative_to(root / EXT_MANIFEST, self._workspace_root) or str(root / EXT_MANIFEST)

    def _repo_files(self, root: Path) -> list[tuple[str, Path]]:
        """Every path under `root` as `(repo-relative, absolute)` — files and directories.

        Directories are included so `<dir>/**` still resolves for a tree that is
        present but empty of matching files. Symlinked directories are recorded
        but not descended into, so a link pointing back up the tree cannot make
        this walk unbounded. Walked only for repos that declare ignore rules —
        and once each — so a workspace using none of this pays nothing.
        """
        cached = self._repo_file_cache.get(root)
        if cached is not None:
            return cached
        out: list[tuple[str, Path]] = []
        queue: list[Path] = [root]
        while queue:
            try:
                entries = self._fs.iterdir(queue.pop())
            except OSError:
                continue
            for entry in entries:
                relative = _relative_to(entry, root)
                if relative is None:
                    continue
                out.append((relative, entry))
                if entry.name not in _PRUNE_DIRS and not self._fs.is_symlink(entry) and self._fs.is_dir(entry):
                    queue.append(entry)
        self._repo_file_cache[root] = out
        return out


def _names(keys: frozenset[str]) -> str:
    return ", ".join(f"`{key}`" for key in sorted(keys))


def _in_scope(path: Path, scope_paths: list[Path]) -> bool:
    """Whether `path` was covered by this run — under a scope directory, or the scoped file itself."""
    return any(_relative_to(path, scope_path) is not None for scope_path in scope_paths)


def _relative_to(path: Path, base: Path) -> str | None:
    """`path` expressed under `base` with forward slashes, or None when it escapes."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return None
