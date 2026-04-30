from __future__ import annotations

import dataclasses
import enum
import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from winter_cli.modules.workspace.models import (
    DiffMode,
    FeatureEnvironmentOverview,
    FeatureEnvironmentStatus,
    Workspace,
    WorktreeDiffResult,
    WorktreeRepoStatus,
    WorktreeSyncReport,
)
from winter_cli.modules.workspace.drift import DriftWarningService
from winter_cli.modules.workspace.internal.read_workspace_repository import resolve_worktree_index
from winter_cli.modules.workspace.prune_service import PruneOrphan, PruneService
from winter_cli.modules.workspace.reporter_factory import ReporterFactory
from winter_cli.modules.workspace.workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.repo_repository import ReadRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_service import WorkspaceService


@dataclasses.dataclass
class WorktreeListParams:
    output_json: bool


@dataclasses.dataclass
class WorktreeStatusParams:
    worktree: str | None
    output_json: bool


@dataclasses.dataclass
class WorktreeSyncParams:
    worktree: str
    output_json: bool


@dataclasses.dataclass
class WorktreeConnectParams:
    worktree: str
    feature_branch: str
    output_json: bool


@dataclasses.dataclass
class WorktreeDisconnectParams:
    worktree: str
    output_json: bool


@dataclasses.dataclass
class WorktreePushParams:
    worktree: str
    repo_names: list[str] | None
    output_json: bool


@dataclasses.dataclass
class WorktreeDiffParams:
    worktree: str
    mode: DiffMode
    repo_filter: str | None
    no_headers: bool
    output_json: bool


@dataclasses.dataclass
class WorktreeIndexParams:
    name: str
    output_json: bool


@dataclasses.dataclass
class WorkspacePruneParams:
    dry_run: bool
    force: bool
    output_json: bool


class WorkspaceHandler:

    def __init__(
        self,
        workspace_svc: WorkspaceService,
        workspace_repo: ReadWorkspaceRepository,
        repo_repo: ReadRepoRepository,
        repo_factory: RepositoryFactory,
        drift_warning_svc: DriftWarningService,
        prune_svc: PruneService,
        reporter_factory: ReporterFactory,
        workspace: Workspace,
    ) -> None:
        self._workspace_svc = workspace_svc
        self._workspace_repo = workspace_repo
        self._repo_repo = repo_repo
        self._repo_factory = repo_factory
        self._drift_warning_svc = drift_warning_svc
        self._prune_svc = prune_svc
        self._reporter_factory = reporter_factory
        self._workspace = workspace

    def list(self, params: WorktreeListParams) -> None:
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        environments = self._workspace_repo.get_environments(self._workspace, project_repos)
        statuses = [self._workspace_repo.get_environment_status(env, project_repos) for env in environments]

        if params.output_json:
            items = [_to_dict(s) for s in statuses]
            _echo_json(items)
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("WORKTREE")
        table.add_column("FEATURE BRANCH")
        table.add_column("STATUS")

        for s in statuses:
            feature_branch = s.feature_branch or "-"
            status_text = " ".join(v for v in s.extensions.values() if v) or "-"
            table.add_row(s.environment.name, feature_branch, status_text)

        Console().print(table)

    def status(self, params: WorktreeStatusParams) -> None:
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        if params.worktree:
            env = self._workspace_repo.get_environment(self._workspace, params.worktree)
            env_status = self._workspace_repo.get_environment_status(env, project_repos)
            env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
            repo_statuses = self._workspace_svc.get_worktree_repo_statuses(env_worktrees)
            self._render_single(env_status, repo_statuses, params.output_json)
        else:
            environments = self._workspace_repo.get_environments(self._workspace, project_repos)
            overviews = []
            for env in environments:
                env_status = self._workspace_repo.get_environment_status(env, project_repos)
                env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
                repo_statuses = self._workspace_svc.get_worktree_repo_statuses(env_worktrees)
                overviews.append(FeatureEnvironmentOverview(status=env_status, repo_statuses=repo_statuses))
            self._render_grid(overviews, params.output_json)

    def sync(self, params: WorktreeSyncParams) -> None:
        env = self._workspace_repo.get_environment(self._workspace, params.worktree)
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
        report = self._workspace_svc.sync_worktree(env_worktrees)

        if params.output_json:
            _echo_json(_to_dict(report))
            if not report.success:
                sys.exit(1)
            return

        console = Console()
        table = Table(show_header=True, header_style="bold")
        table.add_column("REPO")
        table.add_column("RESULT")
        table.add_column("NOTES")

        for outcome in report.repos:
            result_val = outcome.sync_result.value

            if result_val == "fast_forwarded":
                style = "green"
                notes = ""
            elif result_val == "up_to_date":
                style = "dim"
                notes = ""
            elif result_val == "merged":
                style = "cyan"
                notes = "merge commit created"
            else:
                style = "yellow"
                notes = f"+{outcome.ahead} / -{outcome.behind}"

            table.add_row(outcome.repo_name, result_val, notes, style=style)

        console.print(table)

        if report.success:
            console.print(f"\n[green]✓[/green] {report.worktree} synced successfully")
        else:
            console.print(f"\n[yellow]![/yellow] {report.worktree} has diverged repos")
            sys.exit(1)

    def connect(self, params: WorktreeConnectParams) -> None:
        env = self._workspace_repo.get_environment(self._workspace, params.worktree)
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
        count = self._workspace_svc.connect_worktree(env_worktrees, params.feature_branch)

        if params.output_json:
            _echo_json({"worktree": params.worktree, "feature_branch": params.feature_branch, "repos_configured": count})
            return

        Console().print(f"[green]✓[/green] Connected [bold]{params.worktree}[/bold] → [bold]{params.feature_branch}[/bold] ({count} repos)")

    def disconnect(self, params: WorktreeDisconnectParams) -> None:
        env = self._workspace_repo.get_environment(self._workspace, params.worktree)
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
        count = self._workspace_svc.disconnect_worktree(env_worktrees)

        if params.output_json:
            _echo_json({"worktree": params.worktree, "repos_configured": count})
            return

        Console().print(f"[green]✓[/green] Disconnected [bold]{params.worktree}[/bold] ({count} repos)")

    def push(self, params: WorktreePushParams) -> None:
        env = self._workspace_repo.get_environment(self._workspace, params.worktree)
        project_repos = self._repo_factory.get_project_repos()
        env_status = self._workspace_repo.get_environment_status(env, project_repos)
        if not env_status.feature_branch:
            raise click.ClickException(
                f"Environment '{params.worktree}' has no feature branch set. "
                f"Run 'winter ws connect {params.worktree} <branch>' first."
            )
        self._drift_warning_svc.raise_warning()
        env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
        results = self._workspace_svc.push_worktree(env_worktrees, env_status.feature_branch, params.repo_names)

        if params.output_json:
            _echo_json(results)
            return

        console = Console()
        if not results:
            console.print("[dim]No repos with commits to push[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("REPO")
        table.add_column("PUSHED")
        table.add_column("COMMITS", justify="right")

        for r in results:
            pushed = "[green]yes[/green]" if r.get("pushed") else "[red]failed[/red]"
            commits = str(r.get("commits", 0)) if r.get("pushed") else r.get("error", "")
            table.add_row(r["repo_name"], pushed, commits)

        console.print(table)

    def index(self, params: WorktreeIndexParams) -> None:
        idx = resolve_worktree_index(params.name)
        if params.output_json:
            _echo_json({"name": params.name, "index": idx})
            return
        click.echo(idx)

    def prune(self, params: WorkspacePruneParams) -> None:
        orphans = self._prune_svc.find_orphans()

        if params.output_json:
            self._prune_json(params, orphans)
            return

        if not orphans:
            click.echo("Nothing to prune. Workspace is clean.")
            self._maybe_reaggregate_excludes(params, removed_any=False)
            return

        for o in orphans:
            click.echo(self._format_orphan_line(o))

        if params.dry_run:
            return

        if not params.force:
            removable = sum(1 for o in orphans if o.safe_to_remove)
            if removable == 0:
                click.echo("\nNothing to remove (all orphans are blocked). Resolve the notes above and re-run.")
                return
            click.confirm(f"\nRemove {removable} orphan(s)?", abort=True)

        removed_any = False
        for o in orphans:
            if not o.safe_to_remove:
                click.echo(f"  skip   {self._relative(o.path)} ({o.notes})")
                continue
            try:
                self._prune_svc.remove_orphan(o)
                click.echo(f"  remove {self._relative(o.path)}")
                removed_any = True
            except Exception as exc:
                click.echo(f"  error  {self._relative(o.path)} ({exc})")

        self._maybe_reaggregate_excludes(params, removed_any=removed_any)

    def _prune_json(self, params: WorkspacePruneParams, orphans: list[PruneOrphan]) -> None:
        results = []
        if params.dry_run:
            for o in orphans:
                results.append({
                    "kind": o.kind,
                    "path": str(o.path),
                    "safe_to_remove": o.safe_to_remove,
                    "notes": o.notes,
                    "action": "would_remove" if o.safe_to_remove else "skipped",
                })
            _echo_json({"dry_run": True, "orphans": results})
            return

        removed_any = False
        for o in orphans:
            entry = {
                "kind": o.kind,
                "path": str(o.path),
                "safe_to_remove": o.safe_to_remove,
                "notes": o.notes,
            }
            if not o.safe_to_remove:
                entry["action"] = "skipped"
            else:
                try:
                    self._prune_svc.remove_orphan(o)
                    entry["action"] = "removed"
                    removed_any = True
                except Exception as exc:
                    entry["action"] = "error"
                    entry["error"] = str(exc)
            results.append(entry)

        excludes_updated = False
        if removed_any:
            excludes_updated = self._prune_svc.reaggregate_excludes(self._reporter_factory.get_init_reporter(True))
        _echo_json({"dry_run": False, "orphans": results, "excludes_updated": excludes_updated})

    def _maybe_reaggregate_excludes(self, params: WorkspacePruneParams, removed_any: bool) -> None:
        if params.dry_run:
            return
        if not removed_any:
            return
        reporter = self._reporter_factory.get_init_reporter(False)
        self._prune_svc.reaggregate_excludes(reporter)

    @staticmethod
    def _format_orphan_line(o: PruneOrphan) -> str:
        marker = " " if o.safe_to_remove else "!"
        suffix = f"  ({o.notes})" if o.notes else ""
        return f"{marker} {o.kind:<18} {o.path}{suffix}"

    def _relative(self, path) -> str:
        try:
            return str(path.relative_to(self._workspace.root_path))
        except ValueError:
            return str(path)

    def diff(self, params: WorktreeDiffParams) -> None:
        env = self._workspace_repo.get_environment(self._workspace, params.worktree)
        project_repos = self._repo_factory.get_project_repos()
        self._drift_warning_svc.raise_warning()
        env_worktrees = self._workspace_svc.get_feature_environment_worktrees(env, project_repos)
        result = self._workspace_svc.get_worktree_diff(env_worktrees, params.mode, repo_filter=params.repo_filter)

        if params.output_json:
            data = {
                "worktree": result.worktree,
                "mode": result.mode.value,
                "repos": [
                    {
                        "name": r.repo_name,
                        "files_changed": r.files_changed,
                        "insertions": r.insertions,
                        "deletions": r.deletions,
                    }
                    for r in result.repos
                ],
            }
            _echo_json(data)
            return

        if not result.repos:
            return

        for i, repo in enumerate(result.repos):
            if not params.no_headers:
                if result.mode == DiffMode.branch and repo.ahead:
                    commit_word = "commit" if repo.ahead == 1 else "commits"
                    click.echo(f"=== {repo.repo_name} (+{repo.ahead} {commit_word}) ===")
                else:
                    click.echo(f"=== {repo.repo_name} ===")
            click.echo(repo.diff_text)
            if i < len(result.repos) - 1:
                click.echo()

    def _render_single(
        self,
        env_status: FeatureEnvironmentStatus,
        repo_statuses: list[WorktreeRepoStatus],
        output_json: bool,
    ) -> None:
        if output_json:
            _echo_json({"environment": _to_dict(env_status), "repos": _to_dict(repo_statuses)})
            return

        console = Console()
        console.print(f"[bold]Worktree:[/bold] {env_status.environment.name}")
        if env_status.feature_branch:
            console.print(f"[bold]Branch:[/bold]   {env_status.feature_branch}")
        for key, value in env_status.extensions.items():
            if value:
                console.print(f"[bold]{key}:[/bold] {value}")
        console.print()

        if not repo_statuses:
            console.print("[dim]No repos[/dim]")
            return

        extension_keys: list[str] = []
        for repo_status in repo_statuses:
            for k in repo_status.extensions:
                if k not in extension_keys:
                    extension_keys.append(k)

        table = Table(show_header=True, header_style="bold")
        table.add_column("REPO")
        table.add_column("SYNC", justify="right")
        table.add_column("DIRTY", justify="right")
        for key in extension_keys:
            table.add_column(key.upper())

        for repo_status in repo_statuses:
            sync_parts = []
            if repo_status.ahead:
                sync_parts.append(f"+{repo_status.ahead}")
            if repo_status.behind:
                sync_parts.append(f"-{repo_status.behind}")
            sync_str = ", ".join(sync_parts) if sync_parts else ""

            if repo_status.dirty_count == 0:
                dirty_str = ""
            elif repo_status.dirty_count == 1:
                dirty_str = "1 file"
            else:
                dirty_str = f"{repo_status.dirty_count} files"

            if repo_status.dirty_count:
                row_style = "red"
            elif repo_status.ahead and repo_status.behind:
                row_style = "dark_orange"
            elif repo_status.ahead:
                row_style = "green"
            elif repo_status.behind:
                row_style = "yellow"
            else:
                row_style = ""

            row = [repo_status.worktree.repository.name, sync_str, dirty_str]
            for key in extension_keys:
                ext = repo_status.extensions.get(key, {})
                row.append(str(ext) if ext else "-")
            table.add_row(*row, style=row_style)

        console.print(table)

    def _render_grid(
        self,
        overviews: list[FeatureEnvironmentOverview],
        output_json: bool,
    ) -> None:
        if output_json:
            _echo_json([{"environment": _to_dict(o.status), "repos": _to_dict(o.repo_statuses)} for o in overviews])
            return

        console = Console()
        repo_names: list[str] = []
        if overviews:
            repo_names = [rs.worktree.repository.name for rs in overviews[0].repo_statuses]

        table = Table(show_header=True, header_style="bold")
        table.add_column("REPO", no_wrap=True)

        for overview in overviews:
            badges = " ".join(v for v in overview.status.extensions.values() if v)

            has_ahead = any(rs.ahead for rs in overview.repo_statuses)
            has_behind = any(rs.behind for rs in overview.repo_statuses)

            if has_ahead and has_behind:
                header_color = "dark_orange"
            elif has_ahead:
                header_color = "green"
            elif has_behind:
                header_color = "yellow"
            else:
                header_color = ""

            name_label = overview.status.environment.name.capitalize()
            if header_color:
                name_label = f"[{header_color}]{name_label}[/{header_color}]"
            branch = overview.status.feature_branch or "—"
            header_label = f"{name_label} {badges}".rstrip()
            table.add_column(f"{header_label}\n[dim]{branch}[/dim]", justify="center", no_wrap=True)

        repo_lookup: dict[str, dict[str, Any]] = {}
        for overview in overviews:
            repo_lookup[overview.status.environment.name] = {
                rs.worktree.repository.name: rs for rs in overview.repo_statuses
            }

        for repo_name in repo_names:
            row = [repo_name]
            for overview in overviews:
                repo_status = repo_lookup[overview.status.environment.name].get(repo_name)
                if repo_status is None:
                    row.append("[dim]-[/dim]")
                    continue
                cell = self._format_cell(repo_status)
                row.append(cell if cell else "[dim]·[/dim]")
            table.add_row(*row)

        console.print(table)

    @staticmethod
    def _format_cell(repo_status: Any) -> str:
        parts = []
        if repo_status.ahead:
            parts.append(f"[green]+{repo_status.ahead}[/green]")
        if repo_status.behind:
            parts.append(f"[yellow]-{repo_status.behind}[/yellow]")
        if repo_status.dirty_count == 1:
            parts.append("[red]1 file[/red]")
        elif repo_status.dirty_count > 1:
            parts.append(f"[red]{repo_status.dirty_count} files[/red]")
        return " ".join(parts)


def _to_dict(obj: Any) -> Any:
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _echo_json(data: Any) -> None:
    click.echo(json.dumps(data, default=str, indent=2))
