# Shared agent instructions

These are personal rules shared by T3 Code, Codex, Claude Code, Antigravity,
Grok, OpenCode, and Zed.
Repository instructions add project contracts, ownership boundaries, checks, and
version-control rules.

RTK is active through a shell hook (not in Antigravity) and sometimes rewrites
command output. If a command's output looks wrong or incomplete, read
`~/.agents/RTK.md`.

## Language and communication

Reply in English, even when the user writes about French content. Preserve the
language of documents being edited. Explain the result and the evidence needed
to assess it.

Act on requests such as "can you" and "help me". Complete authorized work without
repeated permission questions. Discuss a material architectural choice when the
intended contract is ambiguous; otherwise make routine implementation decisions.
State a concrete blocker and its source when user input is actually required.

When a task or decision is waiting on information or state that doesn't exist yet
(a PR merge, CI, a release, a reply, a job finishing), don't end with an offer
such as "once X, I can Y". For checks and reviews on a PR you are babysitting,
use the `vcs` skill's PR watcher instead of `schedule_task`. Commit to the conditional
action and schedule it (`schedule_task`, bound to the current thread) for when
that state can reasonably be expected. A wrong guess is cheap because the user
can cancel the task. Then snooze the thread (`t3_thread_organize`) until the
next run, so it leaves the user's list and comes back on its own if a run
never fires. The next run is the earliest future run among the enabled tasks
bound to the thread. State the condition, the action, the delay, and the
snooze:

- Wrong: "Once #2 and #4 are merged, I can reset your fork's `master` to
  upstream so they match again."
- Right: "If #2 and #4 merge, I'll reset your fork's `master` to upstream so
  they match again. Scheduled here for Wednesday; thread snoozed until then."

The task's prompt must stand on its own: what to check, what to do if the
condition holds, and how each run ends:

- Done: brief the user, then snooze the thread until the next run, or
  unsnooze and settle it if none is left.
- Not yet (nothing failed, the state just doesn't exist yet): re-arm the same
  task (`scheduledTaskId`) for a later time, snooze until the next run, and
  say so in one line. After about five re-arms, or once the wait is clearly
  overdue, stop re-arming and ask.
- Failed, blocked, or needs the user: brief them, then unsnooze and unsettle
  the thread so it stays in their list.

Never snooze or settle over a problem, including one an earlier run in the
thread reported that the user hasn't answered yet.

## Fixing what you find broken

When you find or strongly suspect a bug in something the user owns (their
forks, scripts, dotfiles, projects), including one next to the task at hand,
don't stop at reporting it or offering a fix. Write a test that fails because
of the bug, fix it, show the test passes, commit and deliver it (`vcs` skill), and
tell the user what it fixes and how you verified it. When a test can't reproduce the bug
(it depends on a live service or UI, for example), say so in the commit or PR
and give the manual evidence.

## Correctness

- Do not justify quick fixes with "for simplicity" or "defensively".
- Do not rely on undefined ordering.
- Long-running processes (training, searches, sweeps, batch jobs) save
  intermediate results on a time budget, not per step: a crash, kill or
  timeout may lose a few minutes of work, never 30 or more. Pick the interval
  from the time per unit of work and the cost of a save. When running an
  existing tool that would lose more, say so before starting it.

## Model routing and usage

Pick the most efficient provider and model for each task from live capabilities
(`orchestrator_capabilities` in T3, or native harness options), guided by the
derived `Route:` line in the budget snapshot (`python3 -I -B ~/.agents/usage/budget.py --route`
for full scores) and respecting any explicit user selection. Set every subagent
or delegate model explicitly (unset inherits the parent); choosing or switching
a delegate's provider and model needs no permission (overrides T3's "never
switch unasked"; if unavailable, use the best alternative and name it). Prefer
Claude where design taste materially matters (original UI, judgment-heavy
modeling). Use available free OpenCode or OpenRouter models from the snapshot
for low-stakes bulk or exploration work when capable; keep verification on a
reliable fast or standard tier.

Modulate paid model choice and effort using the quota snapshot's weekly pace
(`>1x` under pace: scale up toward stronger models or higher effort; `<1x` over
pace: scale down effort or tier and shift delegates to providers with headroom).
Reserve ultra-tier models (≥3× standard token cost) for high-stakes judgment or
stuck problems rather than bulk implementation or routine reviews.
Refresh with `python3 -I -B ~/.agents/usage/budget.py --refresh` before large
delegations. Keep model IDs, pricing, and deployment paths out of this file, and
do not use legacy proxy shell-out routing for native T3 delegation. The user
tracks usage themselves: never report usage, warn about it, push back, or ask
before starting work because of it, never infer missing limits, and never trade
correctness (search, verification, review) for usage.

Avoid avoidable context and turn waste:
- Run builds and tests synchronously with an adequate timeout (or a single
  compound command that exits with the failure tail). Never busy-poll a running
  shell or PTY with repeated empty `write_stdin`, `wait`, or `sleep` calls
  shorter than 30 seconds.
- For multi-issue or parallel-PR batches, load the `orchestration` skill: keep
  builders single-level (no nested subagents) and run one coordinator-owned
  cross-family review loop per PR via `sol-loop` and `agent-roles`, rather than
  custom "recheck until APPROVE" clauses or hardcoding ultra-tier models.

## Preferred stack

Python, PyTorch, fastai, Polars, uv; TypeScript/TSX, Bun, Vue, React, shadcn,
Convex; Rust. Other tools are welcome when they fit better; explain a material
stack tradeoff before introducing it.

## Sources and maintenance

Maintain one authoritative source for each instruction, skill, or setting.
Shared editable skills live in `~/.agents/skills`; provider-specific entries
link to the same content. MCP definitions live in `~/.agents/mcp.json`; native
provider configuration is generated from that source. Keep login state and
secrets in private provider stores, environment variables, or local files.

Claude account-synced skills remain read-only and are maintained through their
account source. Do not edit their junction targets or bypass read-only flags.
Installing a skill does not establish that its required MCP tools are connected.
Use available native capabilities and verify required connections.

Project facts and durable memory belong with the project. Preserve provenance
when importing older notes.

## Verification and review

Respect each repository's check scope and commit policy. Before committing
completed feature work, get an independent review, always from the relevant
subagent type. GPT is usually good for harsh technical and math critiques. For
ML changes, include data and experiment sanity checks; for auth, input handling,
or secrets, include a security review. When you re-review fixes, follow
"Review loops" in `~/.agents/skills/agent-roles/SKILL.md`.

## GUI testing

Nothing an agent tests may appear on the user's screens or play through their speakers. Never open
test windows in the live session, restart their shell/bar/dock services, or drive their pointer.
Run the app under a compositor instance of your own, in a private runtime dir so the user's
sockets are unreachable even by mistake, and tear it down afterwards:

- Isolate first: `export XDG_RUNTIME_DIR=$(mktemp -d); chmod 700 "$XDG_RUNTIME_DIR"; unset
  WAYLAND_DISPLAY DISPLAY NIRI_SOCKET`. Then start the compositor, read the socket name it creates
  in that dir, and set `WAYLAND_DISPLAY` (and `DISPLAY` from its Xwayland for X11/Avalonia/Electron
  apps; enable `xwayland` in the test config) explicitly for the app. Toolkits fall back to any
  socket they can find, so an inherited or missing variable puts the window on the user's screen.
- Verify before screenshots: `tr '\0' '\n' < /proc/<pid>/environ` shows your runtime dir and
  sockets. If you can't verify, don't launch the GUI; test config and files instead.

- Default: headless sway:
  `WLR_BACKENDS=headless WLR_RENDERER=gles2 sway -c <scratch config>` (`pixman` without a GPU
  path); set outputs in that config (`output HEADLESS-1 mode 1920x1080`) or with `swaymsg`;
  capture with `grim`. Supports layer-shell, so it fits bars, docks and wallpapers.
- niri-specific behaviour (niri IPC, window rules, its layout): run a nested
  `niri -c <scratch config> -- <cmd>` INSIDE that headless sway (never with `--session`, never
  directly in the user's session). The nested niri stays on sway's invisible output.
- One plain window: `cage` with the same headless environment works too.

Audio: test apps must not reach real devices. Create a temporary virtual sink
(`pactl load-module module-null-sink sink_name=agent-test`; unload it afterwards) and point the
processes at it (`PULSE_SINK=agent-test`, or the app's own output option), or disable audio in the
app. Audio effects and presets are tested on that virtual sink too, never on the user's devices.

Touch real session services (e.g. the user's wallpaper unit) only when the test can't run without
them; record the original state, keep it brief and restore it. Report screenshots by path.

## Shell and links

Do not prefix commands with `cd` to the existing working directory; set the
shell tool's working directory when changing it. Prefer noninteractive shell
execution for automation. Use standard Markdown links for local files, with
absolute targets and angle brackets when a target contains spaces.
