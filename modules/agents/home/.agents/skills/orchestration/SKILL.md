---
name: orchestration
description: Coordinate multi-agent, multi-issue, or parallel-PR implementation batches across worktrees, builders, cross-family reviewers, slow verification gates, and periodic batch monitoring (acting as an agent orchestrator). Not for a single direct coding task or a single code review.
---

# Orchestration

Run multi-issue and multi-PR batches with high throughput, small diffs, low
rework, and bounded host load. The orchestrator owns decomposition, concurrency,
merge order, review loops, slow gates, and cleanup. Builders own one scoped
change and do not spawn subagents.

This skill composes with:

- `AGENTS.md` for live model routing (`budget.py --route`), quota pace, and GUI/audio isolation.
- [sol-loop](../sol-loop/SKILL.md) and [agent-roles](../agent-roles/SKILL.md) for the single coordinator-owned cross-family review loop (budget, ledger, severities).
- [vcs](../vcs/SKILL.md) for commits, PRs, and PR babysitting.
- [full-send](../full-send/SKILL.md) for the merge gate when the user authorized autonomous merging.

Helper scripts live in `S = ~/.agents/skills/orchestration/scripts`.

## 1. Decompose, size, and sequence before spawning

- **Target small PRs (~1–3k LOC).** Split larger epics into cohesive slices
  before delegating. Monolith PRs inflate rebase churn and force multi-round
  rework.
- **Partition by file ownership.** Group parallel waves so active worktrees do
  not edit the same hot files (`Cargo.lock`, workspace `Cargo.toml`, shared
  registries, or top-level UI wiring) at the same time.
- **Parallelize disjoint work off `origin/<default>`.** Do not stack unrelated
  work on another PR's branch.
- **Speculative stacking rule:** Stack dependent PR B on PR A **only after A's
  shared types/contracts have passed review** (or land a tiny contract PR first).
  Never stack on an unreviewed multi-thousand-LOC draft whose API is still moving.
- **Merge continuously.** Land each approved, gate-passing PR (or small coherent
  chunk) promptly instead of letting a backlog pile up. After each merge, remove
  its worktree (`git worktree remove`) and reclaim its **per-worktree** build
  artifacts (`<worktree>/target/`, per-worktree `/dev/shm` build dirs; never a
  shared `CARGO_TARGET_DIR` mid-wave) before launching the next wave.

## 2. Pre-flight concurrency, effort, and host headroom

`~/.agents/hooks/model_guard.py` mechanically enforces these invariants on tool
calls across Claude, Codex, Grok, and Antigravity:

- **Builder cap:** max **4** active delegated builders in compiled workspaces
  (`Cargo.toml`, `CMakeLists.txt`, `meson.build`, `go.mod`, `build.zig`), **6**
  otherwise (`AGENTS_MAX_BUILDERS` overrides). Non-worktree `review`/`research`
  delegates do not consume builder slots.
- **Host headroom:** blocks builders below `8 GiB` `MemAvailable`, `6 GiB` free
  on `/` or the target workspace mount, or `4 GiB` free on `/dev/shm` (and
  blocks all launches below `3 GiB` `MemAvailable`).
- **Claude effort:** blocks `effort="max"` and `"ultrathink"` unless explicitly
  requested with `[max-effort: <reason>]`. Use `xhigh` for hard architecture or
  tricky bugs, and `high` for routine implementation.
- **No nested subagents:** delegated children are default-denied from spawning
  subagents unless the parent brief explicitly grants permission
  (`"you may delegate ..."`).
- **No `t3_thread_send` recycling:** blocks sending reviews/re-reviews,
  `freshContext: true` recycling, or assigning a different `#issue` to an
  existing delegated child thread.

Before launching the first wave:

1. Refresh routing once (`python3 -I -B ~/.agents/usage/budget.py --refresh`)
   and call `orchestrator_capabilities` once per thread.
2. Check host headroom and active/stalled children:
   ```sh
   python3 -I -B ~/.agents/skills/orchestration/scripts/resource_watch.py snapshot --path <repo>
   ```
3. In compiled repositories, keep heavy parallel builds off `/dev/shm` unless
   you clean finished target dirs between waves, and avoid launching concurrent
   full-workspace `llvm-cov` runs on the same wave.

## 3. Builder briefs and slow-gate handoff (>5 min cache TTL)

Give each builder its own isolated worktree (`isolation: "worktree"`), an
explicit provider/model/effort, exact scope and out-of-scope boundaries,
expected tests, and a clear reminder to work directly without spawning subagents
or running its own review loop.

Split fast inner loops from slow full-repo gates around the ~5-minute prompt
cache TTL:

- **Fast checks (<2 min):** Keep the builder alive to run targeted unit tests,
  type checks, and `cargo check` / `clippy` while its prompt cache is hot.
- **Slow gates (>5 min — full workspace suites, `llvm-cov`, `pre-push` hooks):**
  Do not park a 100k+ token builder idle across a 10-minute test run only to
  cold-cache re-prefill its transcript afterward. Instead:
  1. Instruct the builder to verify fast targeted tests, commit cleanly on its
     worktree branch, write a short handoff file outside the repo using a flat
     slug with no `/` (for example `/tmp/orch-handoff-<slug>.md` with:
     **Intent**, **Key invariants**, **Non-obvious choices / rejected paths**,
     **Exact gate command**), and exit.
  2. Run the slow gate yourself with **zero LLM tokens** via `gate_runner.py`:
     ```sh
     python3 -I -B ~/.agents/skills/orchestration/scripts/gate_runner.py \
       --workdir <worktree> \
       --cmd "<slow gate command>" \
       --handoff /tmp/orch-handoff-<slug>.md
     ```
  3. **If `gate_runner.py` exits `0`:** spend zero post-test LLM tokens and move
     straight to cross-family review.
  4. **If `gate_runner.py` fails:** spawn a **fresh fix delegate** in the same
     worktree (or send a short follow-up only if the builder is still warm and
     small) with the handoff file, `git diff <base>`, and the failure `tail`
     from `gate_runner.py`'s JSON output.

## 4. Coordinator-owned cross-family review loop

Run **one** cross-family review loop per PR, owned by the coordinator — never
double-review by letting builders run same-family review subagents first.

1. Pick a reviewer from a **different model family** than the builder (e.g.,
   GPT or Gemini reviewing Claude builds; Claude or Gemini reviewing GPT builds)
   following [sol-loop](../sol-loop/SKILL.md) and
   [agent-roles](../agent-roles/SKILL.md) "Review loops".
2. Add an `adversary` seat (`~/.agents/roles/adversary.md`) alongside `reviewer`
   **only** when the PR touches security boundaries, auth, sandboxing, untrusted
   input/parsers, or data-loss paths (or when the user asks for `council`).
3. Every review round is a **fresh `delegate_task`** (`role: "review"`,
   `mode: "async"`, new `clientRequestId`), passing the Round 1 brief, the
   finding ledger (`id`, severity, outcome), the same base diff, and the delta
   since the previous round. Never use `t3_thread_send` for reviews.
4. Fix tiny findings (a few obvious lines) directly yourself. For larger fixes,
   dispatch the agreed `R1..RN` findings to the builder (via `t3_thread_send`
   if still warm/small on the same issue, or a fresh worktree fix delegate with
   the handoff brief if cold/large), re-run slow gates through `gate_runner.py`,
   then launch the next fresh review round.
5. Stop at `APPROVE` (or nits-only) within the 3-round budget (5 only for
   security/auth/data-loss/concurrency with live evidence), then file/update and
   merge according to [vcs](../vcs/SKILL.md) and user merge authorization.

## 5. Monitoring long batches (zero-token watchdog + 30-min audit)

Never schedule an LLM turn every few minutes to poll RAM or disk. Split
monitoring into two tiers:

### A. Zero-token resource watchdog (3-min cadence)

Start `resource_watch.py watch` in a background shell when launching a batch
that compiles or runs heavy tests:

```sh
python3 -I -B ~/.agents/skills/orchestration/scripts/resource_watch.py watch \
  --path <repo> --interval 180 --max-minutes 240 --once-on-breach \
  --stop-file /tmp/orch-stop-<batch-id>
```

- **Default (`--once-on-breach`):** consumes zero tokens while healthy and exits
  with code `2` + JSON diagnostics on breach, waking the coordinator reactively
  via background-task completion (works in every runtime, no T3 Connect needed).
- **Optional T3 webhook mode:** if a T3 `schedule_task` `{type: "webhook"}` is
  configured, export its URL into an env var (or `0600` file) and pass
  `--webhook-env ORCH_WEBHOOK_URL` (or `--webhook-file <path>`) without
  `--once-on-breach` so breaches post with cooldown (`--cooldown 600`). Never
  pass webhook URLs on command-line arguments.
- **On breach:** pause new launches, remove merged worktrees and stale
  `/dev/shm` or `target/` dirs, verify `snapshot` is green, then restart the
  watchdog if the batch continues.

### B. Periodic efficiency audit (30-min T3 automation)

For multi-hour autonomous batches in T3, register **one** interval automation
via `schedule_task` (`schedule: {type: "interval", everyMs: 1800000}`,
`bindToCurrentThread: false`, `freshSession: true`) on a cheap or free model,
or bound to the orchestrator thread when you want direct wakeups. Keep its
prompt self-contained:

1. Run `python3 -I -B ~/.agents/skills/orchestration/scripts/resource_watch.py snapshot --path <repo> --stale-minutes 20 [--parent-thread <orchestrator-thread-id>]`.
2. Check open PR backlog (`gh pr list --state open`) and overlapping changed
   files across active worktrees (`git worktree list`).
3. Report **only concrete anomalies**: builders stalled >20 min with no recent
   update/commit, approved PRs sitting unmerged, overlapping hot-file edits,
   orphaned worktrees, or tightening RAM/disk headroom. Reply in one line if
   everything is healthy.

## 6. Batch teardown

Before closing out an orchestration run:

1. Touch the watchdog `--stop-file` (or kill the background watchdog task) and
   call `delete_scheduled_task` on every batch automation you created.
2. Remove finished worktrees (`git worktree remove`) and temporary handoff files
   or `/dev/shm` build directories.
3. Unwatch any PRs you are handing back (`unwatch_pull_request`).
4. Report landed PRs/commits, review verdicts and models used, any blocked or
   deferred issues, and leftover follow-ups.
