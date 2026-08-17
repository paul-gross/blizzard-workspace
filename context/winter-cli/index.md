# Winter CLI

This is the top-level hub of the `context/winter-cli/` documentation tree. Enter here first, then open only the single per-topic file that matches your task:

| File | When to read |
|------|--------------|
| [usage/index.md](./usage/index.md) | You need to run any `winter` command — the per-topic routing index covering the full command surface. |
| [workflows.md](./workflows.md) | You want a ready-made command sequence for a routine multi-step operation: bootstrapping, starting a feature, merging main, pushing, tearing down. |
| [root-flags.md](./root-flags.md) | You need a global flag that applies to every command, or the `WINTER_LOG_LEVEL` environment variable. |
| [configuration/index.md](./configuration/index.md) | You are editing `.winter/config.toml` — the per-concept hub for the entire configuration surface. |
| [contracts/service-orchestrator.md](./contracts/service-orchestrator.md) | You are writing or conforming an extension winter dispatches to — the implementer-facing provider protocols. |
| [resilience.md](./resilience.md) | You hit a flaky-network retry, a hung remote git call, or a config↔filesystem drift warning and want the cross-cutting behavior behind it. |
| [setup.md](./setup.md) | You are installing the `winter` CLI into a workspace. |
| [maintaining.md](./maintaining.md) | You are adding, moving, rewriting, or reviewing a file in this tree — it owns the placement, routing, and freshness rules that govern it. |
