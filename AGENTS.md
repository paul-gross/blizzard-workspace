# Winter workspace

We are working in a multi-repository, multi-worktree development workspace: several project repositories are cloned into it, and the **winter** framework manages its worktrees, service orchestration, and agent tooling. All feature development happens in feature environments, each composed of per-project worktrees — never in the source checkouts — so multiple agents can work in parallel in different feature environments without interfering with one another. The project repositories carry no knowledge of winter; every piece of workspace configuration lives in the workspace itself.

Each of the following files declares fundamental pieces pertinent to every task and is imported eagerly:

- IMPORTANT: @context/project/index.md — workspace-owned project context
- IMPORTANT: @context/winter-cli/index.md — the winter CLI context hub
- IMPORTANT: @AGENTS.winter.md — the winter-generated declaration of installed winter extensions
- IMPORTANT: @AGENTS.local.md — the workspace's local settings

## Feature-environment lifecycle

Bring a feature environment up in order: `winter ws init <env>`, then `winter provision <env>`, then `winter service up <env>`. Never run or exercise an environment that has not been provisioned — provisioning is a required baseline operation, not an optional step. [context/environment-lifecycle.md](./context/environment-lifecycle.md) owns the lifecycle phases and the provision-before-run baseline rule.

## Destructive commands

Never run or exercise a destructive `winter` command against a live environment you do not intend to mutate. Which commands are destructive, and how to verify one safely, is owned by [Verifying destructive commands safely](./context/worktree-ops.md#verifying-destructive-commands-safely).

## Further reference

| File | When to read |
|------|--------------|
| [context/workspace-layout.md](./context/workspace-layout.md) | You need the workspace directory layout, feature environments, path notation, or layout rules. |
| [context/worktree-ops.md](./context/worktree-ops.md) | You are performing worktree git operations — creating, pulling, or destroying worktrees. |
| [context/project/contributing.md](./context/project/contributing.md) | You are merging, pushing, or delivering work — the contributing conventions. |
| [context/github.md](./context/github.md) | You are performing a GitHub operation for this project, such as raising an issue. |
