---
name: agent-roles
description: Use the shared reviewer, verifier, data-profiler, Explore, ML reviewer, or Impeccable worker briefs when a task needs one of these roles or asks to delegate to one, especially for a T3 delegated task on another provider. Does not itself authorize delegation.
---

# Shared agent roles

Read `~/.agents/roles/README.md` and the selected brief below. Resolve `~` to
the user's home; these canonical paths also work when the skill was loaded
through a provider-directory junction.
Use the active runtime's delegation rules and live tools. Pass the absolute
brief path and a self-contained task with scope, access, evidence, and output.
Honor a request to work directly or without further subagents.

| Role | Brief |
| --- | --- |
| General code review | `~/.agents/roles/reviewer.md` |
| Scoped project checks | `~/.agents/roles/verifier.md` |
| Data schema and anomalies | `~/.agents/roles/data-profiler.md` |
| Read-only code search | `~/.agents/roles/Explore.md` |
| Empirical ML review | `~/.agents/roles/ml-reviewer.md` |
| Approved raster asset production | `~/.agents/roles/impeccable-asset-producer.md` |
| Finished UI review | `~/.agents/roles/impeccable-finish-reviewer.md` |
| Design system recording | `~/.agents/roles/impeccable-documenter.md` |
| Leased live copy edits | `~/.agents/roles/impeccable-manual-edit-applier.md` |

Claude (`~/.claude/agents`) and Codex (`~/.codex/agents`) have generated native
agents with these names that read the same files. Other runtimes may not, and
a role's access contract is a prompt constraint, not a tool sandbox.
