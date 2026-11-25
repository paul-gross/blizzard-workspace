# Our machines

The instance spans several of the operator's own machines, joined by one tailnet. This page names them, says what each
holds, and says how one machine operates the others. It records our local setup on purpose: an operator session on any
of these machines should be able to act on all of them without asking where anything lives.

There is no fleet script. This page and [instance.md](./instance.md) describe each procedure closely enough that an
agent composes the commands a move needs, against the machines below, at the time it runs them. An agent that wants a
script for a long move writes a throwaway one in its own scratch space; it is never committed here.

## The machines

This table is the registry: the record of which machine holds what. A move onto a build moves every runner host in it.

| Machine (tailnet name) | What it is          | Runner host: instance dir, unit, port                                      | Also holds                                                              |
| ---------------------- | ------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `xps`                  | Dell XPS 15         | `~/projects/blizzard-blizzard`, `blizzard-blizzard-runner.service`, `8431` | the `blizzard-infra` worktree and the `blizzard-prod-admin` AWS profile |
| `amd-9950x-fedora`     | Ryzen 9950X desktop | `~/blizzard`, `blizzard-builder-runner.service`, `8431`                    | —                                                                       |
| `fedora`               | Fedora laptop       | none — development and orchestration only                                  | —                                                                       |

The tailnet is `pufferfish-lungfish.ts.net`; a machine's MagicDNS name is `<name>.pufferfish-lungfish.ts.net`. Each
host's identities, runners, workspace roots, and env pools are in
[instance.md](./instance.md#the-hosts-and-their-runners). Each machine's own checkout of this workspace says which
machine it is in its `AGENTS.local.md`; `tailscale status --json`'s `Self.DNSName` says so too.

A runner-host machine keeps two kinds of checkout apart. The runner host's workspace roots belong to the fleet: they sit
inside its instance directory (or beside it, on `xps`), track `master`, and are never edited by hand. Interactive
development happens in a separate checkout under `~/projects`.

## Operating one machine from another

Any machine can **orchestrate**: drive every runner host from one operator session. On its own machine it runs commands
directly; every other machine it reaches as `ssh <name>`. Tailscale SSH is on for every machine, so no keys are
exchanged; a first contact only asks to accept the host key.

These hold for every command sent to another machine:

- **Send a script to `bash -s` on stdin**, from a quoted heredoc (`ssh <name> bash -s <<'EOF' … EOF`), rather than a
  command line for the remote login shell. The login shell is `zsh`, which re-reads what it is handed — a bare `====` is
  an `=`-expansion error there, and globs and quoting shift under it. Pass values into the script as assignments on its
  first lines.
- **Use the target's paths, from the table above**, never the orchestrating machine's. `~` belongs inside the remote
  script, so it expands on the target.
- **The remote shell is non-login.** It has no user bus: before any `systemctl --user` or `journalctl --user`, set
  `XDG_RUNTIME_DIR="/run/user/$(id -u)"` and `DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"`. Its `PATH` is
  not the unit's; call the host's CLI as `<instance dir>/.venv/bin/blizzard`, and put `~/.local/bin` first for `uv`.
- **Move files with `scp`** to `<name>:<path under home>`. A build is fetched once on the orchestrating machine and
  copied to every host, so every host installs the same bytes.
- **Secrets stay where they are.** Read a remote `.env` or token store only for its key names (`cut -d= -f1`), never its
  values, and never print a token. A credential file a harness login wrote is never copied between machines (see below).
- **Inspection is free; change is not.** Health, readiness, status, settings reads, and journals may be read on any host
  at any time. Stopping, starting, installing, or changing settings on a host happens only within a move the user asked
  for, or a change they asked for by name.

### Asking every host at once

The shape every multi-host step takes: for each runner host in the table, run the same script on its machine with that
row's instance dir, unit, and port, and label its output with the machine. A status pass, for example, asks each host's
`systemctl --user is-active <unit>`, `curl -s http://127.0.0.1:<port>/api/health` (its build), and
`curl -s http://127.0.0.1:<port>/api/ready` (its runners), beside the hub's own
`curl -s https://blizzard.grosscode.net/api/health`. A host that does not answer is reported as unreachable, never
skipped silently.

Moves that change hosts are sequenced in phases across all of them — every host prepared, then the graphs, then every
host started — as [instance.md § The runner hosts](./instance.md#the-runner-hosts) sets out. A phase that fails on one
host stops there; it does not go on to the next host.

### What an orchestrating machine needs

| For                               | Needs                                                                                    |
| --------------------------------- | ---------------------------------------------------------------------------------------- |
| Anything                          | the tailnet, and a checkout of this workspace for these docs                             |
| Fetching a build                  | `gh`, signed in with read access to `paul-gross/blizzard`                                |
| Fleet calls and re-minting graphs | an operator `blizzard` CLI and a hub session ([binding](./blizzard-binding.md))          |
| Moving the hub                    | a `blizzard-infra` worktree and the `blizzard-prod-admin` AWS session — today only `xps` |

A machine missing the last row can still move every runner host; the hub half then runs on `xps`, over `ssh xps`.

## Every machine with a runner host

These hold on each runner-host machine, whichever it is:

- **The unit is a systemd user unit, kept running by linger** (`loginctl enable-linger`), so it outlives a logout.
- **Never stop a runner by a process-name pattern.** It takes that host and every fleet worker it holds with it. Use the
  host's unit, or `winter service restart <env>/runner` for a feature env.
- **Its venv is installed from the build's own wheel** and pinned requirements, never a local build or an editable
  install, and every host runs the same build as the others.
- **Its trace collector** runs from `<instance dir>/otel-collector` as on `xps`
  ([instance.md](./instance.md#the-runners-trace-collector)), with its own copy of `collector.env`.
- **Its harness logins are its own.** Each machine signs in to Claude Code, OpenCode and the Codex CLI itself, and a
  credential file is never copied between machines: two machines renewing one login rotate each other's refresh token
  away. The Codex CLI is the standalone binary from `openai/codex`'s GitHub release, in `~/.local/bin`.

## Adding a machine

1. Join it to the tailnet and turn on Tailscale SSH. On SELinux-enforcing Fedora, Docker must not run with
   `--selinux-enabled`, or bind-mounted files under `~` are unreadable to containers (Fedora's `moby-engine` passes the
   flag; Docker's own `docker-ce` does not).
2. For a runner host: an instance directory with the workspace roots, venv, host directory and collector; `runner init`
   against the hub; a unit with the drop-ins `xps`'s carries, and node `22.22.3` on its `PATH`, as on `xps`. Pause each
   new runner at the hub before the host's first start, and resume it only after inspection
   ([instance.md](./instance.md#the-hosts-and-their-runners)).
3. Add it to the table above, and, for a runner host, to [instance.md](./instance.md).
