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

## Review loops

When you fix a code change and re-review it, these rules apply. Council and
Hillclimb evaluations keep their own contracts instead.

- Budget 3 rounds. Extend to 5 only for security, permissions, auth, data loss,
  or state and concurrency while a blocker or should stays open with evidence,
  or when the user asks. Stop early when a round returns only nits; declined
  nits and comment, doc, or format-only fixes need no new round.
- Check every finding and its severity yourself. A rare case, another path, or
  a small cost is a nit whatever label it came with. Agree: make the smallest
  fix, preferring to delete or narrow code. Disagree: give evidence (file:line,
  test, output). Nit or out of scope: decline in one line and list it as a
  follow-up. Do not agree just to end the loop.
- Keep a ledger of id, severity, and outcome (`fixed`, `disagree`,
  `declined`), and send it with each new round. If a round grew the diff
  (`git diff --shortstat <base>`), cut before the next one.
- Each round gets the round-1 task brief, the ledger, the same base diff, and
  the change since the last round. In T3 it is a fresh delegated task;
  elsewhere a follow-up to the same native reviewer is fine.
- Finish on `APPROVE` for the current diff with no blocker or should open. If an
  item stays disputed after two replies, or the budget runs out, show both sides
  in two or three lines and let the user decide. Budget end is not approval.

The `sol-loop` skill applies these rules with GPT Sol as the reviewer.

Claude (`~/.claude/agents`) and Codex (`~/.codex/agents`) have generated native
agents with these names that read the same files. Other runtimes may not, and
a role's access contract is a prompt constraint, not a tool sandbox.
