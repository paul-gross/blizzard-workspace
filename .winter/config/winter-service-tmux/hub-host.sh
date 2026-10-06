#!/bin/sh
# Runs a feature env's hub and points it at the env's mock forge.
#
# The hub holds its forge as store records, not environment variables, and refuses to
# start while BZ_FORGE_URL or a [[work_source]] block is set. So once the daemon
# answers, this declares the forge for it: a `mock-forge` secret (the mock accepts any
# token), plus a work source and a repository record per fixture repo, aimed at the
# mock forge on BZ_FORGE_PORT. `config apply` is idempotent, so every `up` re-asserts
# the records and a restart rewrites nothing.
#
# Runs with cwd at the env's blizzard worktree (the hub service's `cwd`).
set -eu

forge_url="http://127.0.0.1:${BZ_FORGE_PORT}"
records="${BZ_HUB_RUNTIME}/forge-records.yaml"

uv run blizzard hub init "$BZ_HUB_RUNTIME"

cat > "$records" <<EOF
version: 1
secrets: [mock-forge]
work_sources:
  - {name: toy-api, provider: github, locator: blizzard/toy-api, api_base: "${forge_url}", secret: mock-forge, annotate: false}
  - {name: toy-web, provider: github, locator: blizzard/toy-web, api_base: "${forge_url}", secret: mock-forge, annotate: false}
repositories:
  - {name: toy-api, forge_api_url: "${forge_url}", owner: blizzard, repo: toy-api, base_branch: "${BZ_BASE_BRANCH}", secret_name: mock-forge}
  - {name: toy-web, forge_api_url: "${forge_url}", owner: blizzard, repo: toy-web, base_branch: "${BZ_BASE_BRANCH}", secret_name: mock-forge}
EOF

uv run blizzard hub host --dir "$BZ_HUB_RUNTIME" --host 127.0.0.1 --port "$BZ_HUB_PORT" &
hub=$!
trap 'kill "$hub" 2>/dev/null' INT TERM HUP

tries=0
until curl -sf "${BZ_HUB_URL}/api/health" >/dev/null 2>&1; do
    kill -0 "$hub" 2>/dev/null || { wait "$hub"; exit $?; }
    tries=$((tries + 1))
    if [ "$tries" -ge 240 ]; then
        echo "hub-host: hub never answered ${BZ_HUB_URL}/api/health; forge records not seeded" >&2
        break
    fi
    sleep 0.5
done

# A failed seed leaves the hub serving without a forge rather than taking it down.
seed() {
    uv run blizzard hub secret show mock-forge --hub-url "$BZ_HUB_URL" >/dev/null 2>&1 \
        || printf 'mock-forge-token' | uv run blizzard hub secret set mock-forge --hub-url "$BZ_HUB_URL" >/dev/null \
        || return 1
    uv run blizzard hub config apply "$records" --hub-url "$BZ_HUB_URL"
}
if [ "$tries" -lt 240 ]; then
    if seed; then
        echo "hub-host: forge records seeded against ${forge_url}"
    else
        echo "hub-host: seeding forge records failed; the hub has no forge" >&2
    fi
fi

wait "$hub"
