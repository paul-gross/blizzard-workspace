# Workspace Management

We are working in a **multi-worktree, multi-repository** development workspace, optimized for agentic development. Multiple project repositories are cloned here, and all feature development happens in feature environments comprised of multiple project-specific worktrees — not in the source checkouts. Multiple agents can work in parallel across different feature environments without interfering with each other.

This workspace is powered by **winter**, a framework that manages the worktrees, service orchestration, and agent tooling. The project repos know nothing about winter — all workspace configuration lives here in the workspace itself.

IMPORTANT: This workspace has fundamental pieces declared in @context/project/index.md that are pertinent to every task.

## Winter CLI

The `winter` command manages feature environments and repositories across the workspace. Use it instead of manual multi-repo git operations. Use raw git for single-repo work (staging, committing, conflict resolution).

IMPORTANT: This workspace has fundamental pieces declared in @context/winter-cli/index.md that are pertinent to every task.

IMPORTANT: A feature environment's lifecycle is `winter ws init <env>` → `winter provision <env>` → `winter service up <env>`. `ws init` is structural only — it does not install dependencies, migrate databases, or seed data, and `service up` starts services against the env as-is. **Never run or exercise an env you have not provisioned**: provisioning is a baseline op, not optional. See [context/environment-lifecycle.md](./context/environment-lifecycle.md).

## Key References

| Location | Topic |
|----------|-------|
| [context/workspace-layout.md](./context/workspace-layout.md) | Directory layout, feature envs, path notation, and rules |
| [context/environment-lifecycle.md](./context/environment-lifecycle.md) | Env lifecycle phases (init → provision → service up → destroy) and the provision-before-run baseline rule |
| [context/worktree-ops.md](./context/worktree-ops.md) | Worktree git operations (create, pull, destroy) |
| [context/project/contributing.md](./context/project/contributing.md) | Contributing conventions (merge, push, delivery) |
| [context/github.md](./context/github.md) | GitHub forge, issue labels, and `/wg-issue` skill |

# Winter Extensions

IMPORTANT: This workspace has fundamental pieces declared in @AGENTS.winter.md that are pertinent to every task.

# Local Settings

IMPORTANT: This workspace has fundamental pieces declared in @AGENTS.local.md that are pertinent to every task.
