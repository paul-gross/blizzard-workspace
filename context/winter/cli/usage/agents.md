# `winter agents` — agent model/effort resolution matrix

For the hub and the rest of the command surface, see [../index.md](../index.md).

```bash
winter agents          # three colored tables + legend
winter agents --json   # JSON document: tiers, agent_overrides, agents
```

Read-only introspection of the same model/effort resolution `winter ws init` bakes into every rendered agent file
(`.claude/agents`, `.codex/agents`, `.opencode/agent`). Shows, per installed agent x harness (`claude`, `codex`,
`opencode`), which of the four layers — built-in default, `[model_tiers]` remap, the agent's own per-harness `model:`
block, or an `[agent_model_overrides]` entry — won, and the tables it was resolved from. Exits `0` even when a cell
fails to resolve (the failure is reported per cell, not raised); only a config load error exits `1`. `winter doctor`'s
agent probes are what flag drift or an unresolvable tier. Under `adopt_extensions = "none"` no agent is installed: the
effective matrix prints `(no installed agents)` and the JSON `agents` section is empty. For the config tables this
reads, see [../configuration/agents.md](../configuration/agents.md).

## Human-readable output

Three tables, each ending in one column per harness (`claude`, `codex`, `opencode`), followed by the legend:

1. **Code defaults** — columns `tier` + harnesses. The built-in `MODEL_TIER_IDS` table (`fable`/`opus`/`sonnet`/`haiku`
   x harness), unaffected by workspace config.
2. **Global overrides** — columns `tier` + harnesses. The *effective* tier table (built-ins plus every `[model_tiers]`
   label, including custom labels), sorted by label. Each cell is colored by its layer; a cell remapped by
   `[model_tiers]` also shows its source file (`config.toml`/`config.local.toml`) in parentheses. An unmapped cell (a
   custom label missing a vendor entry) prints `-`.
3. **Effective matrix** — columns `agent`, `extension` + harnesses. One row per installed agent, ordered by extension
   then agent, with one `model·effort` cell per harness; an inherited effort prints the model alone. A stale or missing
   on-disk copy appends `[stale]`/`[missing]`; a cell whose resolution raised an error prints `error` in place of a
   value.

`[agent_model_overrides]` gets no table of its own: every override that applies already shows in the effective matrix in
the Agent Override color. An override key matching no installed agent (likely a typo) prints one yellow line to stderr —
`warning: overrides matching no installed agent: <names>` — and nothing when every key matches. The full per-entry
breakdown is the `agent_overrides` section of `--json`.

### Legend

A cell's color names the layer that produced it. In an effective-matrix cell the model half takes the model layer's
color and the `·effort` half the effort layer's. The legend at the bottom of the human output prints every layer's label
in its color:

| Layer (JSON name) | Legend label     | Color | Applies to                                |
| ----------------- | ---------------- | ----- | ----------------------------------------- |
| `code_default`    | Code Default     | dim   | model                                     |
| `tier_override`   | Global Override  | cyan  | model                                     |
| `harness_block`   | Agent Definition | white | model, effort                             |
| `agent_override`  | Agent Override   | green | model, effort                             |
| `inherited`       | Inherited        | dim   | effort (no effort half is printed for it) |

Colors drop when stdout is not a terminal; read `--json` for the layer names in that case.

## JSON contract

`--json` emits a single JSON document with exactly three top-level sections. Only this document goes to stdout;
diagnostics (e.g. a warning logged while resolving an `[agent_model_overrides]` entry, or a canonical agent file that
could not be read and was skipped) go through the logger to stderr — warnings print by default, and `--verbose` adds
debug detail.

```json
{
  "tiers": [ /* ... */ ],
  "agent_overrides": [ /* ... */ ],
  "agents": [ /* ... */ ]
}
```

A layer without a config source (`code_default`, `harness_block`) is a bare string or `null`. A layer with a source is a
`{value, source}` object, except a model `agent_override`, which is `{value, tier, source}`. A layer that does not apply
to a cell is `null`, never omitted.

### `tiers`

One entry per effective tier label x harness (built-in labels plus every `[model_tiers]` label, custom or not):

```json
{
  "label": "haiku",
  "harness": "opencode",
  "code_default": "anthropic/claude-haiku-4-5",
  "tier_override": null,
  "effective": "anthropic/claude-haiku-4-5",
  "effective_layer": "code_default"
}
```

A custom label with no mapping for a harness is all-`null` except `label`/`harness`.

### `agent_overrides`

One entry per configured `[agent_model_overrides]` key, independent of whether it names an installed agent:

```json
{
  "agent": "reviewer",
  "source": "config.toml",
  "matches_installed": true,
  "tier": "haiku",
  "harnesses": {
    "claude": { "model": "haiku", "effort": null, "error": null },
    "codex": { "model": "gpt-6-luna", "effort": null, "error": null },
    "opencode": { "model": "anthropic/claude-haiku-4-5", "effort": null, "error": null }
  }
}
```

`tier` is the label for the bare-string override form, `null` for the per-vendor dict form. `harnesses` always carries
all three keys; a harness the per-vendor dict form does not list is `null` — indistinguishable, deliberately, from "this
override doesn't apply here". A harness the entry does list but cannot resolve for (a bare-string tier with no mapping
for it) is not `null` — it's `{ "model": null, "effort": null, "error": "<message>" }`, and that case also logs a
warning to stderr. Every non-null harness object always carries all three keys, including a resolved one, which carries
`"error": null`. `matches_installed` is `false` when no installed agent carries this name — the entry is still listed,
since the mismatch (usually a typo) is exactly what you want surfaced.

### `agents`

One entry per installed agent x harness:

```json
{
  "agent": "ice-carver",
  "extension": "winter-workflow",
  "installed_name": "wf-ice-carver",
  "harness": "opencode",
  "model": {
    "declared_tier": "sonnet",
    "code_default": "anthropic/claude-sonnet-5-5",
    "tier_override": null,
    "harness_block": null,
    "agent_override": { "value": "openai/gpt-6-luna", "tier": null, "source": "config.toml" },
    "effective": "openai/gpt-6-luna",
    "effective_layer": "agent_override"
  },
  "effort": {
    "harness_block": null,
    "agent_override": { "value": "max", "source": "config.toml" },
    "effective": "max",
    "effective_layer": "agent_override"
  },
  "on_disk": "in_sync",
  "error": null
}
```

`on_disk` is `in_sync | stale | missing`, reusing the same comparison `winter doctor`'s agent probes make against the
rendered copy. When resolution itself fails (e.g. an unknown `model:` tier label), the entry is still listed:
`model`/`effort` are both `null`, `on_disk` is `null`, and `error` carries the failure message — the same message
`winter doctor`'s `agent tier: <vendor>` probe reports.
