# Shared agent instructions

These are personal rules shared by T3 Code, Codex, Claude Code, and Zed.
Repository instructions add project contracts, ownership boundaries, checks, and
version-control rules.

RTK is active through a shell hook and sometimes rewrites command output. If a
command's output looks wrong or incomplete, read `~/.agents/RTK.md`.

## Language and communication

Reply in English, even when the user writes about French content. Preserve the
language of documents being edited. Explain the result and the evidence needed
to assess it.

Act on requests such as "can you" and "help me". Complete authorized work without
repeated permission questions. Discuss a material architectural choice when the
intended contract is ambiguous; otherwise make routine implementation decisions.
State a concrete blocker and its source when user input is actually required.

## Correctness

- Do not justify quick fixes with "for simplicity" or "defensively".
- Do not rely on undefined ordering.
- Long-running processes (training, searches, sweeps, batch jobs) save
  intermediate results on a time budget, not per step: a crash, kill or
  timeout may lose a few minutes of work, never 30 or more. Pick the interval
  from the time per unit of work and the cost of a save. When running an
  existing tool that would lose more, say so before starting it.

## Models and usage

Aim for the most efficient mix of intelligence and cost for the task, respecting
any explicit user selection. Prefer Claude where design taste materially matters,
including original UI and judgment-heavy modeling. Choosing or switching a
delegate's provider and model needs no permission; this overrides T3's "never
switch unasked". If a model is unavailable, use the best available alternative
and say which one you used. Keep model IDs, pricing snapshots, and deployment
paths in configuration, not in this file. Do not use legacy proxy shell-out
routing for native T3 delegation.

Account for usage limits. Before starting a large piece of work, check usage
manually (the SessionStart hook prints the refresh command). Above 90%, tell the
user and ask before starting work longer than about an hour of agent time: it may
fail partway or leave no quota for the rest of the week. Work up to about an hour
is safe at 90%. If usage cannot be read, say so.

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
or secrets, include a security review.

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
