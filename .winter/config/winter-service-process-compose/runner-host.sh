#!/bin/sh
# Runs a feature env's runner host over the env's blizzard-mock fixture workspace.
#
# `runner init` scaffolds the host file and joins the runner to the env's hub. This script
# then owns the file's workspace itself rather than leaving it to init: it drops any
# `[[workspace]]` init wrote and declares the env's own, from the fixture's root, env pool,
# and base branch (BZ_WORKSPACE_ROOT, BZ_WORKSPACE_ENVS, BZ_BASE_BRANCH in the feature band).
# The id stays `workspace-local`, the workspace init's scaffolded runner binds its project to.
# So the env keeps its workspace whether or not init scaffolds one.
#
# The scaffolded `anthropic` subscription is re-declared under provider "none", which binds no
# sampler: left as `anthropic`, it reads the developer's real ~/.claude/.credentials.json and
# polls Anthropic's usage endpoint with it every five minutes, sharing that endpoint's
# per-account rate limit with the live instance's runners — enough feature envs up at once get
# the instance's samples refused (429), and a stale sample brakes its throttle.
#
# The claude_code health probe reads the binding as authenticated only when its credentials file
# holds an OAuth access token, and defaults that file to the developer's real
# ~/.claude/.credentials.json. The runner is pointed instead at a placeholder in its own runtime
# dir, rewritten each `up`: the mock needs no credential, and the real one is never read.
#
# The stale tomls are cleared first so each `up` re-derives config from the current env — the
# host file too, which a host build writes beside it and a pre-host build never reads. The
# runtime dir outlives a reset of the env's hub data, so init runs with --allow-readd: a hub
# that no longer knows the token in the runtime dir's .env adds the runner afresh.
#
# Runs with cwd at the env's blizzard worktree (the runner service's `cwd`).
set -eu

host_file="${BZ_RUNNER_RUNTIME}/blizzard-host.toml"

# Must be absolute: the runner spawns the worker with the acquired env dir as cwd.
BZ_HARNESS_BINARY="$(cd ../blizzard-mock && pwd)/.venv/bin/mock-claude-code"
export BZ_HARNESS_BINARY

rm -f "${BZ_RUNNER_RUNTIME}/blizzard-runner.toml" "$host_file"
uv run blizzard runner init --allow-readd "$BZ_RUNNER_RUNTIME"

sed -i 's/^provider = "anthropic"$/provider = "none"/' "$host_file"

credentials_file="${BZ_RUNNER_RUNTIME}/claude-credentials.json"
(umask 077 && printf '%s\n' '{"claudeAiOauth": {"accessToken": "feature-env-placeholder"}}' > "$credentials_file")
sed -i "s|^# claude_code_credentials_path = .*|claude_code_credentials_path = \"${credentials_file}\"|" "$host_file"
grep -q "^claude_code_credentials_path = \"${credentials_file}\"$" "$host_file" || {
  echo "runner-host.sh: init wrote no claude_code_credentials_path line to point at the placeholder" >&2
  exit 1
}

# Drop init's [[workspace]] table, up to the next table header.
awk '
  /^\[\[workspace\]\][[:space:]]*$/ { skip = 1; next }
  skip && /^\[/                     { skip = 0 }
  !skip
' "$host_file" > "${host_file}.tmp"
mv "${host_file}.tmp" "$host_file"

envs="$(printf '%s' "$BZ_WORKSPACE_ENVS" | awk -F, '{ for (i = 1; i <= NF; i++) printf "%s\"%s\"", (i > 1 ? ", " : ""), $i }')"
cat >> "$host_file" <<EOF

# The env's workspace, declared by runner-host.sh rather than init's scaffold.
[[workspace]]
id = "workspace-local"
root = "${BZ_WORKSPACE_ROOT}"
provider = "winter"
envs = [${envs}]
base_branch = "${BZ_BASE_BRANCH}"
EOF

exec uv run blizzard runner host --dir "$BZ_RUNNER_RUNTIME" --host 127.0.0.1 --port "$BZ_RUNNER_PORT"
