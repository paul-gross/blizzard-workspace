from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class LintScopeError(Exception):
    """A scope argument couldn't be resolved (unknown name, bad flags, no git repo).

    Raised by `LintScopeResolver`; the command layer translates it into a
    `click.ClickException` so the user sees a clean message and a non-zero exit.
    """


class LintStatus(enum.Enum):
    """Outcome of a single lint check finding."""

    pass_ = "pass"
    warn = "warn"
    fail = "fail"


@dataclass(frozen=True)
class LintFinding:
    """One result emitted by a contributed lint check.

    Parallel to `doctor`'s `ProbeResult`, with `file`/`line` added so a check
    can point at the exact source location of a violation. `source` identifies
    the contributing group — the workspace (`"project"`) or an extension's
    symlink prefix. `check` names the individual check within that source.
    `remediation` is an optional one-line fix hint shown under failures.
    """

    source: str
    check: str
    status: LintStatus
    message: str = ""
    file: str | None = None
    line: int | None = None
    remediation: str | None = None


@dataclass(frozen=True)
class LintIgnoreOrigin:
    """Where a rule was declared, for a rule not declared by the repo it governs.

    Carried whole rather than as loose optional fields so that rendering a rule
    never has to re-derive which surface produced it. `spelled` is the value the
    author actually typed, which is not always the glob: `repos = ["vendored"]`
    is a repo name that becomes a `**` glob, and a label showing `**` would name
    neither the repo nor anything its author wrote — and would be identical for
    every entry in the list.
    """

    declared_in: Path
    owner: str
    table: str
    key: str
    spelled: str


@dataclass(frozen=True)
class LintIgnoreRule:
    """One `[lint.ignore]` declaration, from a repo's `winter-ext.toml` or the workspace.

    `glob` is **relative to `repo_root`**, always — that is what makes a
    repo-owned declaration portable: the same manifest suppresses the same
    findings in a source checkout, in a feature-env worktree, in `.winter/ext/`,
    and in a stranger's workspace. `repo_root` is the absolute root the glob
    resolves against for this run, and `repo` is that module's name, carried for
    reporting.

    `check` is `None` for a whole-path rule (`[lint.ignore] paths`) and the
    check name for a narrowed one (`[lint.ignore.checks] <check>`). The narrowed
    form is the one worth reaching for: a blanket path ignore turns every check
    off for that file permanently, so the day someone genuinely breaks a link in
    it, lint stays silent.

    `origin` describes *where the rule was written* rather than what it matches,
    and is `None` for the repo-owned case — the rule came from `repo_root`'s own
    `winter-ext.toml`, under the table its `check` already implies. A workspace
    rule carries one so it reports itself honestly instead of impersonating one
    of the repo's own.
    """

    repo: str
    repo_root: Path
    glob: str
    check: str | None = None
    origin: LintIgnoreOrigin | None = None

    @property
    def label(self) -> str:
        """The rule as its author wrote it — shown beside a suppressed finding.

        This is the *only* rule information either reporter renders, for a
        suppressed finding and for a stale one alike, so it has to carry enough
        to tell two rules apart. `spelled` is what makes that true of a shorthand
        whose glob is synthesized rather than typed.
        """
        if self.origin is None:
            table = "lint.ignore" if self.check is None else "lint.ignore.checks"
            key = "paths" if self.check is None else self.check
            return f'{self.repo} [{table}] {key} = "{self.glob}"'
        return f'{self.origin.owner} [{self.origin.table}] {self.origin.key} = "{self.origin.spelled}"'


@dataclass(frozen=True)
class IgnoredFinding:
    """A finding a `[lint.ignore]` rule suppressed, paired with the rule that did it.

    Suppression is only safe if it stays visible, so the pair travels together:
    the summary always counts these, and `--show-ignored` re-prints each one
    under the rule that silenced it.
    """

    finding: LintFinding
    rule: LintIgnoreRule


@dataclass(frozen=True)
class LintIgnoreOutcome:
    """The result of applying every applicable ignore rule to one run's findings.

    `diagnostics` is not a partition of the input — those are findings the
    filter *emits*, about the ignore configuration itself: a `warn` per rule
    that suppressed nothing, plus anything it could not use (an unreadable
    manifest, an unknown key, a wrong-typed value, a malformed glob). A stale or
    typo'd ignore is a lie about the corpus and is exactly how this feature
    decays, so the filter that hides findings also reports on itself — through
    the ordinary reporter channel, which is the only one a default run shows.
    """

    kept: list[LintFinding]
    ignored: list[IgnoredFinding]
    diagnostics: list[LintFinding]


@dataclass(frozen=True)
class LintCheckOutcome:
    """Everything one contributing lint script produced in a single run.

    Tracked per-source (not flattened) so the dispatcher can tell "no checks
    were contributed" apart from "checks ran and found nothing" — a script that
    exits clean with no findings still appears here with an empty `findings`.
    """

    source: str
    findings: list[LintFinding]


class LintScopeKind(enum.Enum):
    """Which slice of workspace content a lint run targets.

    `all` is every feature env's project worktrees; `env` is one env's project
    worktrees (named, or the one containing the invocation dir by default);
    `repo` is one project repo's source checkout; `changed` is the dirty /
    un-pushed file set of the repo at the invocation dir.
    """

    all = "all"
    repo = "repo"
    env = "env"
    changed = "changed"


@dataclass(frozen=True)
class LintScopeRequest:
    """The raw scope selection parsed from the CLI, before resolution.

    At most one of `names` (non-empty) / `all` / `changed` is honored; the
    resolver rejects combinations and, when none is set, resolves the default
    scope (the env containing `cwd`, or every env). Each entry in `names` may
    be a literal project-repo/env name or a bare glob (no `<env>/<repo>`
    segment) — a glob is expanded against both repo and env names, and every
    resolved name (literal or matched) becomes its own `LintScope` in
    `resolve()`'s returned list. `cwd` is the caller's real invocation
    directory (from `WINTER_INVOCATION_CWD`) — used to detect the current env
    for the default scope and to locate the git repo for the `--changed` set.
    """

    names: list[str] = field(default_factory=list)
    all: bool = False
    changed: bool = False
    cwd: Path | None = None


@dataclass(frozen=True)
class LintScope:
    """A resolved scope — the concrete content a lint run will cover.

    `paths` are absolute roots (a repo dir, an env's worktree dirs, the
    workspace root) or, for the changed set, the individual changed files.
    Checks receive these paths and decide which ones they recognize.
    """

    kind: LintScopeKind
    label: str
    paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class LintSummary:
    """Aggregated counts for a completed lint run.

    `contributors` is the number of lint scripts that ran — zero means the
    workspace contributed no checks, which the reporter surfaces explicitly.

    `total`, `fails`, and `warns` count only the findings that survived
    `[lint.ignore]` filtering; `ignored` counts the ones it suppressed. A
    suppressed finding never reaches `fails`, so it cannot fail the run — but it
    is always counted here, so a run can never quietly hide how much it hid.
    """

    contributors: int
    total: int
    fails: int
    warns: int
    ignored: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.fails else 0
