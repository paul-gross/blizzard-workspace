#!/usr/bin/env bash
# Serve one feature env's runner panel (`ng serve runner`) against the LIVE runner, through the
# workspace's live runner proxy, on the env's BZ_RUNNER_LIVE_WEB_PORT. Usage: panel.sh <env>
set -euo pipefail

env_name="${1:?usage: panel.sh <env>}"
workspace="$(cd "$(dirname "$0")/../.." && pwd)"
web="$workspace/$env_name/blizzard/web"
[ -d "$web" ] || { echo "no $env_name/blizzard/web — init and provision the env first" >&2; exit 1; }

source <(cd "$workspace" && winter env "$env_name")
: "${BZ_LIVE_RUNNER_PROXY_PORT:?not in the env band — is .winter/config.toml current?}"
: "${BZ_RUNNER_LIVE_WEB_PORT:?not in the env band — is .winter/config.toml current?}"

if ! curl -fsS -o /dev/null "http://localhost:$BZ_LIVE_RUNNER_PROXY_PORT/__proxy"; then
  echo "the live runner proxy is not answering on :$BZ_LIVE_RUNNER_PROXY_PORT — run: winter service up workspace" >&2
  exit 1
fi

cd "$web"
eval "$(mise env -s bash)"
echo "LIVE runner panel for $env_name: http://localhost:$BZ_RUNNER_LIVE_WEB_PORT/  (actions hit the real fleet)"
# proxy.conf.runner.js forwards /api to 127.0.0.1:$BZ_RUNNER_PORT; aim that at the proxy instead.
BZ_RUNNER_PORT="$BZ_LIVE_RUNNER_PROXY_PORT" exec npx ng serve runner --port "$BZ_RUNNER_LIVE_WEB_PORT"
