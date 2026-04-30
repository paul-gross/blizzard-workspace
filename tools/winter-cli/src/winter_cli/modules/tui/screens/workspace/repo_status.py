from __future__ import annotations

from rich.text import Text

from winter_cli.modules.workspace.models import WorktreeRepoStatus

def render_repo_cell(repo_status: WorktreeRepoStatus) -> Text:
    parts: list[tuple[str, str]] = []

    if repo_status.ahead > 0:
        parts.append((f"+{repo_status.ahead}", "green"))
    if repo_status.behind > 0:
        parts.append((f"-{repo_status.behind}", "yellow"))

    if repo_status.dirty_count == 1:
        parts.append(("1 file", "red"))
    elif repo_status.dirty_count > 1:
        parts.append((f"{repo_status.dirty_count} files", "red"))

    if len(parts) == 0:
        return Text("·", style="dim")

    text = Text()
    for i, (label, style) in enumerate(parts):
        if i > 0:
            text.append(" ")
        text.append(label, style=style)

    if repo_status.tracking_ahead > 0:
        text.append(f" [+{repo_status.tracking_ahead}]", style="cyan")

    for key, value in repo_status.extensions.items():
        if key.startswith("_"):
            continue
        text.append(" ")
        if isinstance(value, Text):
            text.append(value)
        else:
            badge = str(value) if value else key
            text.append(badge, style="cyan")

    return text
