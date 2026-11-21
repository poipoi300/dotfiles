# Shared role briefs

The ten Markdown briefs in this directory are the maintained task bodies.
`adversary.md` adapts the adversary brief from pstack's `interrogate` skill
(MIT, © 2026 Lauren Tan, see `LICENSE-pstack`).
`catalog.json` owns descriptions and Claude's supported native metadata.
Claude's files under `~/.claude/agents` are generated adapters that explicitly
read these briefs. Every adapter inherits the session's chosen model; the old
model pins survive only in the private historical archive.

Use a role when the task calls for it. Read the selected brief, then give the
worker the absolute brief path, objective, repository/checkout, allowed files,
inputs, relevant skills, checks, and required output. A role name is a label,
not evidence that a custom agent is registered in the active harness.

In T3, follow the live orchestration instructions and tool schemas. A T3 task
receives no parent history, so its brief must carry the whole contract. Select
models/options from live capabilities only when selection is needed. Honor
explicit user settings and unavailable-provider errors. Use T3-managed
worktree isolation when required; never make a checkout in the task prompt.
Fresh reviews receive the raw scope and evidence without the author's framing.
Every T3 `delegate_task` re-review is a fresh task; native reviewers follow
agent-roles "Review loops". A worker must not delegate again unless its task
explicitly asks it to.

In Codex, the generated custom agents in `~/.codex/agents` read the same briefs;
spawn them by name when authorized by the current task. If delegation is unavailable or prohibited, perform permitted work
directly and disclose that a review was not independent. A read-only role's
access contract still applies; prompt constraints are not an OS sandbox.

Regenerate/check the Claude and Codex adapters:

```sh
python3 -I -B ~/.agents/roles/sync_codex.py --write
python3 -I -B ~/.agents/roles/sync_codex.py
python3 -I -B ~/.agents/roles/sync_claude.py --write
python3 -I -B ~/.agents/roles/sync_claude.py
```

The check is read-only (0 current, 1 regeneration needed, 2 invalid input).
Writes refuse files changed outside the renderer; reconcile those edits first.
The provider launchers (`launch.py`, via `bin/agent-claude` and `bin/agent-codex`) regenerate
both adapter sets and the hook registrations on every start, after the MCP sync; a failure is
reported on stderr and does not block the provider. `launch.py sync` does the same once.
The generated adapters and the `*-adapter-state.json` files they leave here are local deployment
state, not sources. An optional `migration-baseline.json` (digests of hand-written agents that
the first write may replace) is never needed on a fresh machine. Account-synced skill content
is not copied here.
