---
name: sol-loop
description: Use when the user says "sol loop".
metadata:
  source: https://github.com/t3dotgg/tstack/tree/11e37a74d0a6868c121cd0910ab2b3bd9873b92a
---

# sol-loop

This is a workflow for making your code changes more correct before (or
sometimes after) filing a pull request. tl;dr - use OpenAI's GPT Sol to review
your code in a loop.

For general code, this loop is the independent review AGENTS.md requires
before you commit completed feature work. Follow `agent-roles` "Review loops"
for the budget, the ledger, how to answer findings, and when to stop.
Severities and the reply format are in `~/.agents/roles/reviewer.md`.

Be careful of scope creep and don't be scared to push back. One general
reviewer only. full-send may add `ml-reviewer` alongside. Reviewers must
not start subagents or reviewers. Record usage when the reviewer returns it;
never guess it.

## 1. Scope

Run `git status --short`. Do not stash, discard, or include unrelated
changes. Limit review to the change paths if they separate cleanly.
Otherwise stop and ask.

Find the base after `git fetch origin`:

- PR: `gh pr view <n> --json baseRefName,headRefOid`. Check out the branch
  only if clean. Confirm `HEAD` is the PR head, or ahead only by this
  change. Base: `git merge-base HEAD origin/<baseRefName>`.
- Local: `git merge-base HEAD origin/<default-branch>`. No remote: a base
  the user names, or `HEAD` for uncommitted changes only.

The change is base to working tree in those paths, with untracked files.
Use one diff command all rounds. State intent and out-of-scope in 1-2
sentences. Run covering checks. Do not send code this change breaks. Note
missing or pre-failing checks in the brief.

## 2. Start Sol

In T3, call `orchestrator_capabilities` before the first launch. Pick the
latest Sol unless the user named a version. Do not hardcode an ID. Record
provider, model, and effort only if exposed.

If the host already runs that provider and you need no extra settings, use
the native subagent (T3's native-tool rule). Otherwise in T3, use
`delegate_task` every round (`role: "review"`, `mode: "async"`). Save
`taskId`. New `clientRequestId` per round. `childThreadId` is storage, not
the next target. Do not use `t3_thread_send`, `create_threads`, or
`t3_thread_launch` for review.

If T3 tools are not listed, load the deferred tool first where the harness
defers tools (ToolSearch in Claude Code), then try one direct capability
call. Outside T3, use a native Sol subagent and keep its ID. In Claude
Code, use a custom subagent only if it is set up to run Sol. If no route
runs Sol, use the strongest available reviewer from another model family,
and name that model in the report and the PR line. If no other family is
reachable (plain Claude Code, where every subagent is Claude, or Codex
outside T3), use the `reviewer` role on the strongest available model and
disclose a same-family review in the report and the PR line.

Do not edit scoped files while review runs. Wait for the notice. Do not
poll or start a second reviewer. Timeout, error, or no reply is not
approval. If the reviewer fails to start, times out, errors, or gives no
reply: cancel it, verify it stopped, and restart once with the ledger. If
that fails too, ask the user. Under full-send or full-stop, leave the PR
unmerged.

Round 1 brief (T3 children get no history). Resolve `~` and pass the
absolute path of `~/.agents/roles/reviewer.md` for severities and format:

```
Review only. Do not edit, commit, push, comment on PRs, or start subagents.
Repo: <abs path> Base: <sha> Intent: <intent> Out of scope: <scope> Checks: <results>
See: git diff <base> [-- <paths>]; git status --short (read untracked).
Read surrounding code. Find bugs, broken callers, mismatch with intent.
Skip style unless it hides a bug. Hold to the smallest change. Flag code,
tests, or guards the intent does not need.
Severities and reply format: <absolute path of ~/.agents/roles/reviewer.md>
Read that file and use it. Do not invent labels or a reply format.
```

## 3. After each round

Follow `agent-roles` "Review loops" for the ledger, the fix, the next round,
and when to stop. Do not push during the loop. Push after agreement only if
the user asked to push, ship, or update the PR.

If you push to a PR, add this line above file-pr's `Created with ...` line.
Name the reviewer model you actually ran, Sol or the fallback:

`Reviewed with sol-loop: <N> rounds with <reviewer model>. Verdict: approved`.

Add `on <effort>` only if verified.

Report: rounds, model, what the reviewer changed, diff size before/after,
fixed and declined findings with reasons, usage if exposed (else say not),
pushed or not.
