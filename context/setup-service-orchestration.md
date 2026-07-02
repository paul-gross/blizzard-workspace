# Guide: Setting up service orchestration

## What this guide does

This guide is entered from the **Configure service orchestration** step in `/ws-setup` after the user has selected one or more orchestrators via the multi-select in that step. The chosen orchestrator set is known before this guide begins — it does not ask about orchestrator selection.

This guide owns four things in order:

1. **Install** — add any newly-chosen orchestrator as a `[[standalone_repository]]` entry in `workspace:/.winter/config.toml`, bind it to the `service` capability slot, and clone it via `winter ws init`
2. **Discover** — resolve the workspace's services (workspace-level and feature-environment): check this session's project-settings research first, then the manifests already on disk, and only ask the user (or research fresh) if neither source has them
3. **Map** — assign each service to an orchestrator and confirm the mapping
4. **Apply** — hand each orchestrator its assigned services and delegate their declaration and wiring to that extension's own setup guide

This guide does NOT own how a service is declared or wired — that belongs to each extension's guide. This guide owns which orchestrator runs which service.

## Steps

### 1. Install chosen orchestrators

**Explain first:** "Each service orchestrator is a winter extension — a standalone git repo that winter clones into the workspace and wires into the `service` capability slot. I'll install any orchestrator you chose that isn't already present. Already-installed ones are shown and skipped."

First, check the current binding:

```bash
winter capabilities
```

This command is read-only and always exits 0. It prints the current `service` slot state — explicit, implicit, invalid, or `(no provider installed)`.

Also read `workspace:/.winter/config.toml` for `[[standalone_repository]]` entries and the `[capabilities].service` value.

**For each chosen orchestrator not already in the binding**, install it as follows. If an orchestrator is already bound, tell the user:

> "`<orchestrator>` is already installed and bound — skipping."

Then move on to the next one (if any), or continue to Step 2.

#### Install each chosen orchestrator

The procedure is identical for every orchestrator — register it and let winter clone it. **Don't explain here what a given orchestrator does or how it manages services** — that belongs to the extension itself and is surfaced in Step 4 from its own setup guide. At this step you only install.

Resolve each chosen orchestrator's install coordinates:

| Orchestrator | Repository URL | `path` |
|--------------|----------------|--------|
| `winter-service-tmux` | `git@github.com:paul-gross/winter-service-tmux.git` | `.winter/ext/service-tmux` |
| `winter-service-docker` | `git@github.com:paul-gross/winter-service-docker.git` | `.winter/ext/service-docker` |
| custom (user-supplied URL) | the git URL the user gave in the parent step | `.winter/ext/<name>` — `<name>` = last URL segment, `.git` stripped; confirm it with the user |

For each chosen orchestrator, tell the user what's about to happen, then append its entry to `workspace:/.winter/config.toml`:

```toml
[[standalone_repository]]
name = "<name>"
url  = "<url>"
path = ".winter/ext/<dir>"
```

#### Set the `[capabilities].service` binding

After writing the `[[standalone_repository]]` entries, set the `service` binding in `workspace:/.winter/config.toml` to reflect the final set of bound orchestrators (union of already-bound + newly added):

- **One orchestrator only** → `service = "<name>"` (single string)
- **Multiple orchestrators** → `service = ["<name-1>", "<name-2>"]` (list — write this once; do not repeat the `service` key)

```toml
[capabilities]
service = "winter-service-tmux"                                     # single orchestrator
# or:
service = ["winter-service-tmux", "winter-service-docker"]          # both
```

If a `[capabilities].service` entry already exists and needs updating (e.g. adding docker alongside an already-bound tmux entry), update the existing line in place — do not duplicate the key.

#### Clone the new extension(s)

Tell the user: "Running `winter ws init` to clone the new extension(s)..."

```bash
winter ws init
```

Report what was cloned vs. what already existed. Then confirm:

> "`winter capabilities` now shows: `service → <binding>`. Orchestrator(s) ready."

**Post-clone note:** cloning registers each new extension in `workspace:/AGENTS.winter.md` (via the auto-managed `# Winter Extensions` block). Re-read that file — each newly-installed orchestrator now appears there with a link to its own `index.md`. Those links are how Step 4 reaches each orchestrator's setup guide, which is where what the orchestrator does and how its services are wired is explained. Don't restate those per-orchestrator details here.

No additional installation questions. Continue to the next step.

### 2. Discover services

**Explain first:** "Winter organises services into two scopes. **Workspace-level** services (`scope = \"workspace\"`) run once for the entire workspace — shared infrastructure like a database or message broker that all feature environments share. **Feature-environment** services (the default `scope = \"project\"`) run per-env — the application's own processes: API server, frontend dev server, background worker. Both kinds are declared in an orchestrator's manifest (in whatever form that orchestrator uses). I need to know which services your application needs, in both scopes, before mapping them."

#### Check what's already known, in priority order (idempotency)

**1. This session's project-settings research.** If the `ws-setup` project-settings step already researched services earlier in this session — the user chose "environment + services" there — you already have the service list and wiring facts in the [`service-discovery.md`](./service-discovery.md) schema, already confirmed with the user at the end of that step. Tell the user:

> "I already have your service list from the project-settings research: `<name>` (`<scope>`), `<name>` (`<scope>`). I'll use that here."

It was already confirmed once; skip straight to **Step 3: Map services to orchestrators** — don't re-ask for confirmation.

**2. Existing orchestrator manifests.** If services weren't already researched this session, check whether the installed orchestrators' manifests already declare services. Read the manifest for each bound orchestrator and note any services already declared.

If **any manifest already declares services**, tell the user what you found and use those as the starting point:

> "`<orchestrator>` already declares `<n>` service(s): `<name>` (`<scope>`), `<name>` (`<scope>`). I'll start from these."

Ask **one** question:

**"Your manifests already declare: `<list>`. Want to add more services, or proceed to mapping with this set?"**

- "proceed": skip to Step 3.
- "add more": fall through to the discovery question below.

**3. Fresh discovery.** If neither of the above produced a service list, proceed to the discovery question.

#### Discovery question

Offer the user two approaches: *"I can research your project repos and figure out the services automatically, or you can walk me through them. Which do you prefer?"*

**If researching automatically:** Spawn an Opus-class subagent (from the workspace root, per workspace rules) to explore the project repos. Give it the schema from [`service-discovery.md`](./service-discovery.md) — `name`, `scope`, `start_command`, `port`, `container` — as its brief; that doc also lists the typical evidence for each field, so don't restate it here. This is the same schema `setup-project-setup.md` §6 gathers, so this fallback-path pass (used when project settings were already configured without services) produces results indistinguishable from the primary path.

Have the subagent end with a synthesis: the services to declare, each with a proposed name, scope, and whatever wiring was resolved. Cap the report under 600 words. When it reports back, present the findings to the user for confirmation before proceeding.

**If the user prefers a guided approach:** ask one question per turn.

1. Ask **one** question: **"What services need to run for the application to work? List them by name."**
2. For each service in the list, ask **one** at a time: **"Is `<name>` a workspace-level service (shared once across all feature envs — e.g. a database or message broker) or a feature-environment service (runs per-env — e.g. an API server or frontend)?"**
3. Once all services in the list have been assigned a scope, ask: **"Add another service, or move on?"** Loop until the user moves on.

#### Confirm the service list

After discovery (from any of the three sources above), present the full list — name, scope, and whatever wiring was resolved — and ask **one** question:

**"Here are the services I'll work with: `<name>` (`<scope>`) — `<wiring summary if any>`, ... Does this look right, or anything to add or change?"**

Wait for confirmation before continuing.

### 3. Map services to orchestrators

**Explain first:** "Now I'll assign each service to an orchestrator. The assignment determines which manifest owns the service's `[[service]]` entry and which orchestrator's lifecycle commands (`up`/`down`/`status`/`restart`) manage it."

#### Only one orchestrator installed

If only **one orchestrator** is bound, all services run under it. Tell the user:

> "Only `<orchestrator>` is installed, so all `<n>` service(s) will run under it: `<list>`."

Ask **one** question:

**"Continue with this mapping, or is there something to change?"**

- "continue": proceed to Step 4.
- "change": ask **"What would you like to change?"** Handle it, then confirm the updated mapping before proceeding.

#### Multiple orchestrators installed

If **multiple** orchestrators are bound, present a recommended service→orchestrator mapping as a two-column table. Base the recommendation on each orchestrator's documented strengths (from its own `index.md`, reached via `AGENTS.winter.md`) together with each service's scope — matching shared, workspace-level services to the orchestrator suited to shared infrastructure, and per-env application services to the orchestrator suited to local processes.

Display the table — this is a display, not a question; present it and then ask one question:

```
Service     Scope                 Recommended Orchestrator
──────────────────────────────────────────────────────────
db          workspace             winter-service-sample
rabbitmq    workspace             winter-service-sample
api         feature-environment   winter-service-example
web         feature-environment   winter-service-example
worker      feature-environment   winter-service-example
```

*(`winter-service-sample` / `winter-service-example` are placeholder names — substitute the actual services from Step 2 and the orchestrators installed in Step 1.)*

Ask **one** question:

**"Does this mapping look right, or would you like to reassign a service?"**

- "looks right" / "finalize": proceed to Step 4.
- "reassign": ask **"Which service?"** Wait for the answer. Then ask **"Which orchestrator should it run under?"** Re-display the updated table after each change and ask **"Anything else to reassign, or finalize?"** Loop one-question-per-turn until the user finalizes.

### 4. Apply — delegate per-orchestrator wiring

**Explain first:** "Now I'll hand each orchestrator the services assigned to it and follow that orchestrator's own setup guide to wire them. Declaring and wiring a service — its manifest format, ports, panes or containers, layout — is the extension's job, not this guide's."

For each orchestrator in the finalized mapping, **one at a time**: read the extension's own `index.md` (find it via `workspace:/AGENTS.winter.md`) and follow its **"Feature environment setup steps"** section, passing the services assigned to it, their scopes, **and whatever wiring facts were already discovered for them** in the [`service-discovery.md`](./service-discovery.md) schema — that section owns how this orchestrator's services are declared and wired (its manifest location, schema, and any layout/compose companions), and should use the supplied facts directly instead of re-deriving them, only asking about or inferring a field that's genuinely missing. That schema does not include a health/readiness signal — every orchestrator derives or asks about that itself. If the extension declares no such section, tell the user it provides no setup guide and point them at its `index.md`/`README.md` to wire it manually.

#### Confirm

After every orchestrator's wiring is complete, summarise in a single message:

> "Service orchestration configured:
>   - `<service>` → `<orchestrator>` (`<scope>`)
>   - ...
> Each orchestrator's manifest was written through its own setup guide."

Return to the parent skill — it continues to the next step.
