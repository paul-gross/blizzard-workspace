from __future__ import annotations

# Reserved, universal service target accepted by all `winter service` actions
# (up / down / status / restart / logs).  When passed as the env argument it is
# dispatched to the orchestrator as-is rather than looked up in the workspace's
# feature-environment list.
#
# This string is also a reserved feature-environment name: `winter ws init`
# rejects it so a real env named "workspace" can never exist.
#
# Downstream consumers derive their display strings from this constant so the
# canonical spelling is never duplicated across modules.  Import direction is
# strictly one-way: service → workspace (never the reverse).
WORKSPACE_SCOPE = "workspace"
