# Agent configuration

The `[agent_model_overrides]` table lets you retarget which model a workspace uses for any installed extension agent without modifying the agent's committed source. The `[model_tiers]` table lets you remap tier labels to concrete model ids — either overriding built-in tier values or defining entirely new tiers. Changes to either table take effect on the next `winter ws init`.

## When to use these

The agent transform pipeline bakes each agent's model into the rendered per-vendor files during `winter ws init`. By default it uses the agent file's own `model:` tier (or per-harness `model:` override block), resolved through the built-in `MODEL_TIER_IDS` table. The two workspace configuration tables insert additional layers, letting you:

- Temporarily downgrade a set of agents to a cheaper tier for a cost experiment.
- Point specific agents at a concrete model id (e.g. a newly released version) workspace-wide.
- Override a built-in tier's vendor id across all agents (e.g. pin the whole workspace to a specific Sonnet release).
- Define custom tier labels (e.g. `"big-thinker"`, `"smol"`) and reference them in frontmatter or overrides.

## The `[model_tiers]` table

`[model_tiers]` maps tier label strings to per-vendor model id strings. It layers over the built-in `MODEL_TIER_IDS` table — built-in entries (`opus`, `sonnet`, `haiku`) are merged per-vendor, new labels are added whole.

```toml
# .winter/config.toml  (committed, shared with the team)

[model_tiers.big-thinker]
claude = "claude-opus-4-20250514"
codex = "gpt-5.4"
opencode = "anthropic/claude-opus-4-20250514"

# Override only the opencode id for the built-in haiku tier:
[model_tiers.haiku]
opencode = "anthropic/claude-haiku-4-20251201"
```

```toml
# .winter/config.local.toml  (gitignored, personal experiments)

# Local override wins for this tier label:
[model_tiers.big-thinker]
claude = "claude-sonnet-4-5-20250514"
codex = "gpt-5.4"
opencode = "anthropic/claude-sonnet-4-5-20250514"
```

### Merge rules

- Built-in tier labels (`opus`, `sonnet`, `haiku`) are merged **per-vendor**: only the vendor keys listed in your config entry are overridden; unlisted vendors keep their built-in values. This means a `[model_tiers.haiku]` block that only sets `opencode` leaves `claude` and `codex` at their defaults.
- New custom labels are added wholesale from your config entry.
- `config.local.toml` wins over `config.toml` for the same label (per-label replacement, not per-vendor merge across files).

### Validation

| Problem | Reported as |
|---------|-------------|
| Unknown vendor label (not `claude`, `codex`, or `opencode`) | `ConfigError` at config load time |
| Empty vendor dict for a label | `ConfigError` at config load time |
| Non-string or empty model id value | `ConfigError` at config load time |

### Using custom tiers in agent frontmatter

Once a tier is defined in `[model_tiers]`, agents can reference it in their `model:` frontmatter:

```markdown
---
name: power-agent
description: A powerful reasoning agent
model: big-thinker
---
```

If the tier is unknown or lacks a mapping for a vendor (e.g. `codex` is absent from a custom tier entry), `winter ws init` emits a warning and skips that agent; `winter doctor` reports a WARN. This is a render-time non-fatal signal, not a hard abort — other agents still install. Fix the tier reference in the agent's frontmatter or add the missing vendor mapping in `[model_tiers]`.

### Using custom tiers in `[agent_model_overrides]`

A custom tier label can also be used as a tier string value in `[agent_model_overrides]`:

```toml
[agent_model_overrides]
reviewer = "big-thinker"
```

## The `[agent_model_overrides]` table

`[agent_model_overrides]` retargets specific agents by canonical name, without modifying the extension source.

```toml
# .winter/config.toml  (committed, shared with the team)

[agent_model_overrides]
# Tier override — applies to all vendors:
reviewer = "haiku"

# Custom tier override — references a label defined in [model_tiers]:
planner = "big-thinker"

# Per-vendor override — only the listed vendor is affected:
developer = { claude = "claude-opus-4-20250514" }

# Concrete model id scoped per-vendor (use inline-table form for vendor-specific ids):
coder = { codex = "gpt-5.4-experimental", opencode = "anthropic/claude-opus-4-20250514" }
```

```toml
# .winter/config.local.toml  (gitignored, personal experiments)

[agent_model_overrides]
# Locally override a shared entry — this value wins over config.toml:
reviewer = "opus"
```

Keys are **canonical agent names** (the `name:` field in the agent's frontmatter, or the file stem when `name:` is absent). Values are either:

| Form | Example | Applies to |
|------|---------|------------|
| Tier string | `"haiku"` | All vendors |
| Inline table (per-vendor) | `{ claude = "haiku" }` | Only the listed vendor labels |

**Tier string values must be valid tier labels** — either built-in (`"opus"`, `"sonnet"`, `"haiku"`) or defined in `[model_tiers]`. A bare string that does not match any tier label raises `ConfigError` at config load time. Use the per-vendor inline-table form to specify a concrete model id directly.

**Per-vendor values are not tier-validated.** An inline-table value such as `{ claude = "some-id" }` is accepted whether `"some-id"` is a tier label or a concrete model id — a non-tier string is treated as a concrete model id for that vendor and is passed through without validation.

## Precedence

When `winter ws init` resolves an agent's model for a vendor, it applies (highest to lowest):

1. **Workspace override** — the `[agent_model_overrides]` entry for that agent (+ vendor if scoped).
2. **Per-harness override block** — the agent's own `claude:`/`codex:`/`opencode:` `model:` key.
3. **Effective tier table** — the built-in `MODEL_TIER_IDS` values overlaid by any `[model_tiers]` entries.

## Local overlay behaviour

The merge model for `[agent_model_overrides]` is **per-key** (one level deep):

- Entries in `config.local.toml` win over the same-named entries in `config.toml`.
- Entries in `config.toml` that are absent from `config.local.toml` are kept unchanged.

The merge model for `[model_tiers]` is **per-label** (whole-label replacement between files):

- A label present in `config.local.toml` completely replaces the same label from `config.toml`.
- Labels in `config.toml` absent from `config.local.toml` are kept unchanged.

## Validation

`winter ws init` and `winter doctor` validate override entries and agent frontmatter:

| Problem | Reported as |
|---------|-------------|
| Bare-string tier in `[agent_model_overrides]` not in effective tier table | `ConfigError` at **config load** time — aborts the running `winter` command |
| Unknown vendor label in a per-vendor dict value | `ConfigError` at **config load** time |
| Empty string value in `[agent_model_overrides]` | `ConfigError` at **config load** time |
| Wrong value type (e.g. integer) in `[agent_model_overrides]` | `ConfigError` at **config load** time |
| Unknown or incomplete tier in agent `model:` frontmatter | `winter ws init` warns + skips that agent; `winter doctor` WARNs — other agents still install |
| Agent name in `[agent_model_overrides]` that matches no installed agent | `winter ws init` + `winter doctor` WARN |

`winter doctor` also reports a WARN when an on-disk agent copy no longer matches the expected output of the current configuration — including changes to `[model_tiers]`. If you change either table, the doctor probe detects the existing copies as stale until you re-run `winter ws init`.
