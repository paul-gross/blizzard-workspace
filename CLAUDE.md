# CLAUDE.md - Workspace Management

We are working in a **multi-worktree, multi-repository** development workspace, optimized for agentic development. Multiple project repositories are cloned here, and all feature development happens in feature environments comprised of multiple project-specific worktrees — not in the source checkouts. Multiple agents can work in parallel across different feature environments without interfering with each other.

This workspace is powered by **winter**, a framework that manages the worktrees, service orchestration, and agent tooling. The project repos know nothing about winter — all workspace configuration lives here in the workspace itself.

IMPORTANT: This workspace has fundamental pieces declared in @context/project/index.md that are pertinent to every task.

## Winter CLI

The `winter` command manages feature environments and repositories across the workspace. Use it instead of manual multi-repo git operations. Use raw git for single-repo work (staging, committing, conflict resolution).

IMPORTANT: This workspace has fundamental pieces declared in @context/winter-cli/index.md that are pertinent to every task.

## Key References

| Topic | Location |
|-------|----------|
| Directory layout, feature envs, path notation, and rules | [context/workspace-layout.md](./context/workspace-layout.md) |
| Winter CLI command reference | [context/winter-cli/index.md](./context/winter-cli/index.md) |
| Worktree git operations (create, pull, destroy) | [context/worktree-ops.md](./context/worktree-ops.md) |
| Contributing conventions (merge, push, delivery) | [context/project/contributing.md](./context/project/contributing.md) |
| GitHub forge, issue labels, and `/wg-issue` skill | [context/github.md](./context/github.md) |
| Installed winter extensions | `CLAUDE.winter.md` |

# Winter Extensions

IMPORTANT: This workspace has fundamental pieces declared in @CLAUDE.winter.md that are pertinent to every task.

# Local Settings

IMPORTANT: This workspace has fundamental pieces declared in @CLAUDE.local.md that are pertinent to every task.
