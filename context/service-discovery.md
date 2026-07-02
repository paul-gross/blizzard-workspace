# Service discovery contract

Canonical definition of what `ws-setup`'s research discovers about an application's services, and what every downstream consumer can rely on receiving. [`setup-project-setup.md`](./setup-project-setup.md), [`setup-service-orchestration.md`](./setup-service-orchestration.md), and each service-orchestrator extension's own `workflow-setup.md` all point here for the schema instead of restating it — one source of truth for the discovered-fields list, so the guides that produce it and the guides that consume it can never drift apart.

## Discovery modes

`ws-setup`'s project-settings step ([`../skills/setup/SKILL.md`](../skills/setup/SKILL.md)) asks the user to choose one of three modes. The first two run discovery; the third doesn't:

- **Environment only** — discovers the five settings facets (dependencies, env files/ports, databases, seed data, verification) documented in [`setup-project-setup.md`](./setup-project-setup.md). No services are discovered.
- **Environment + services** — discovers the five settings facets *and* the services facet below, in the same research pass.
- **Skip** — no discovery runs.

## The services facet

When service discovery is in scope, the following fields are gathered per service. Every field below `name` and `scope` is **optional** — a field with no evidence in the project is omitted, never fabricated:

| Field | What it is | Typical evidence |
|-------|-----------|-------------------|
| `name` | Service identifier (required — everything else is keyed off it) | user-provided, or derived from the process/container name |
| `scope` | `workspace` (shared once across all feature envs) or `project` (per-env) (required) | shared infra (db, broker) → workspace; application processes (api, web, worker) → project |
| `start_command` | The bare-process start command | `package.json` scripts, `Procfile`, framework entry points (`manage.py`, `mix phx.server`, `rails server`) |
| `port` | The port the service uses or exposes | README setup sections, env templates (`.env.example`, `.env.sample`), framework defaults |
| `container` | Container/image wiring, if the project already uses one: image (or build context), internal port, declared env vars, declared volumes | `Dockerfile`, `docker-compose.yml`/`compose.yaml`, or a matching compose service block |

**Not discovered: a per-service health/readiness signal.** Verifying that setup worked is a project-wide question ([`setup-project-setup.md`](./setup-project-setup.md) §5, "Verification") — not a per-service fact carried in this handoff. Every consumer of this facet derives or asks about health/readiness itself; none may assume a caller-supplied health signal.

The app may or may not already use containers — a service can report a `start_command` and no `container`, a `container` and no bare `start_command`, or both. Consumers use whichever field is relevant to them; the *absence* of a `container` field is signal in itself (the project runs this service as a bare process), not a gap to fill by guessing one into existence.

## Where it happens, and the hand-off

Discovery runs once, in whichever of two places reaches it first:

- **Primary path** — [`setup-project-setup.md`](./setup-project-setup.md) §6, when the user picked "environment + services" in the project-settings step.
- **Fallback path** — [`setup-service-orchestration.md`](./setup-service-orchestration.md)'s own discovery step, when service orchestration is configured later, after project settings were already set up without services.

Either path produces the same schema above. The result is never written to a file — it's carried forward in conversation context to the service-orchestration step, and from there to whichever orchestrator's setup guide wires each assigned service. Every consumer treats a supplied field as given and only derives or asks the user about a field that's genuinely missing; no consumer re-scans the project for a field this contract already promises.
