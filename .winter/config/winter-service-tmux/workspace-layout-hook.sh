#!/usr/bin/env bash
# Workspace-session layout hook for the blizzard workspace tmux session
# (bws-workspace). Invoked once after `winter service up workspace` creates the
# session, with WINTER_TMUX_WORKTREE_DIR set to the workspace root.
#
# Contract: LAYOUT ONLY. Do not use tmux send-keys, source env files, or start
# services. The orchestrator handles all of that after this hook exits.
# See winter-service-tmux:/workflow/layout-hook.sh.example for the full contract.

set -euo pipefail

: "${WINTER_TMUX_SESSION:?WINTER_TMUX_SESSION not set}"
: "${WINTER_TMUX_WORKTREE_DIR:?WINTER_TMUX_WORKTREE_DIR not set}"

# ---------------------------------------------------------------------------
# The workspace session hosts a single service: the ad-hoc `shell` pane. It is
# pane 0.0, the initial pane created by `tmux new-session`, so nothing to split.
# Title it after its service so pane-border-status configs show `shell` rather
# than the machine hostname.
# ---------------------------------------------------------------------------
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.0" -T shell

# ---------------------------------------------------------------------------
# Status bar — session name only. Drops tmux's hostname/clock default so an
# attached session identifies itself (` bws-workspace `) without the machine name.
# ---------------------------------------------------------------------------
tmux set-option -t "${WINTER_TMUX_SESSION}" status-left " #S "
tmux set-option -t "${WINTER_TMUX_SESSION}" status-right ""

# ---------------------------------------------------------------------------
# Focus — land on pane 0.0 on attach.
# ---------------------------------------------------------------------------
tmux select-window -t "${WINTER_TMUX_SESSION}:0"
tmux select-pane -t "${WINTER_TMUX_SESSION}:0.0"
