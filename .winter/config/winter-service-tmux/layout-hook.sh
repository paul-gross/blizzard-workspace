#!/usr/bin/env bash
# Layout hook for a blizzard PER-ENV tmux session (bws-<env>). The shared
# workspace session (bws-workspace) has its own hook, workspace-layout-hook.sh.
#
# Contract: LAYOUT ONLY. Do not use tmux send-keys, source env files, or start
# services. The orchestrator handles all of that after this hook exits.
# See winter-service-tmux:/workflow/layout-hook.sh.example for the full contract.

set -euo pipefail

: "${WINTER_TMUX_SESSION:?WINTER_TMUX_SESSION not set}"
: "${WINTER_TMUX_WORKTREE_DIR:?WINTER_TMUX_WORKTREE_DIR not set}"

# ---------------------------------------------------------------------------
# Window 0 — the blizzard per-env verification stack (from-source services).
#
# LIVE as of P6: three panes in window 0 — the mock GitHub forge (0.0, the initial
# `tmux new-session` pane), the blizzard hub (0.1), and the blizzard runner (0.2).
# The orchestrator sends each service's command into its pane after this hook lays
# them out. Postgres still runs under winter-service-docker; the ad-hoc `shell` is
# workspace-scoped (see workspace-layout-hook.sh).
#
# The Angular dev servers are deliberately NOT here — they get window 1 (below), so
# this window stays the daemon stack and nothing else.
# ---------------------------------------------------------------------------

# P4 forge — title the initial pane (0.0).
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.0" -T forge

# P5/P6 hub (0.1) and P6 runner (0.2) — split from the forge pane and title them.
tmux split-window -h -t "${WINTER_TMUX_SESSION}:0.0" -c "${WINTER_TMUX_WORKTREE_DIR}"  # 0.1 hub
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.1" -T hub
tmux split-window -v -t "${WINTER_TMUX_SESSION}:0.1" -c "${WINTER_TMUX_WORKTREE_DIR}"  # 0.2 runner
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.2" -T runner

# ---------------------------------------------------------------------------
# Window 1 — the Angular dev servers (`ng serve`), the web apps' HMR surface.
#
# Two panes, one per app: the hub board (1.0) and the runner local panel (1.1).
# They are split off into their own window rather than added to the stack above
# for two reasons: five panes in one window leaves each too short to read a compile
# error in, and the daemon/dev-server line is the natural seam — window 0 answers
# "is the fleet up?", window 1 answers "did my component compile?".
#
# Named `web` so an attached session can jump straight to it.
# ---------------------------------------------------------------------------

tmux new-window -t "${WINTER_TMUX_SESSION}" -n web -c "${WINTER_TMUX_WORKTREE_DIR}"  # 1.0 web-hub
tmux select-pane -t "${WINTER_TMUX_SESSION}:1.0" -T web-hub
tmux split-window -v -t "${WINTER_TMUX_SESSION}:1.0" -c "${WINTER_TMUX_WORKTREE_DIR}"  # 1.1 web-runner
tmux select-pane -t "${WINTER_TMUX_SESSION}:1.1" -T web-runner

# Name window 0 to match, now that the session has more than one.
tmux rename-window -t "${WINTER_TMUX_SESSION}:0" stack

# ---------------------------------------------------------------------------
# Status bar — session name only. Drops tmux's hostname/clock default so an
# attached session identifies its env (` bws-alpha `) without the machine name.
# ---------------------------------------------------------------------------
tmux set-option -t "${WINTER_TMUX_SESSION}" status-left " #S "
tmux set-option -t "${WINTER_TMUX_SESSION}" status-right ""

# ---------------------------------------------------------------------------
# Focus — land on pane 0.0 on attach.
# ---------------------------------------------------------------------------
tmux select-window -t "${WINTER_TMUX_SESSION}:0"
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.0"
