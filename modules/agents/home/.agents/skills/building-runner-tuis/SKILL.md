---
name: building-runner-tuis
description: Use when creating a runner TUI from scratch, meaning a terminal app launched from one script that starts, supervises, and controls a project's dev services, servers, workers, or containers. Also use when reworking an existing runner's process supervision, restart policy, or port handling. Not for small fixes to a single runner behavior.
---

# Building runner TUIs

## Overview

A runner is a supervisor with a face. The supervisor owns process trees, ports, restarts, and logs, and imports nothing from the UI. The TUI renders the supervisor's snapshots and sends it commands.

**REQUIRED SUB-SKILL:** Use designing-tuis for every visual and interaction decision.

## Fit the project first

Read the project before designing. Build the registry from what is actually there: each service's command, working directory, build or install pre-steps, ports and who depends on them, readiness signal, and project-specific actions such as open URL, run setup, or migrate. Anything beyond the core below is added because this project's workflow uses it, not imported from a generic feature list.

## Shape

The launcher script runs the runner module (`uv run -m <pkg>.runner`, or the bun/cargo equivalent). Inside it:

| Module | Owns |
|---|---|
| services | Declarative `ServiceSpec` registry, read from project config |
| process | Spawning, process-tree ownership, tree kill, port probes |
| supervisor | Lifecycle state machine, restart policy, log ring buffers; decisions as pure functions |
| tui | Rendering and key bindings only |
| `__main__` | CLI flags, preflight, the non-TTY path |

`ServiceSpec` fields: name, argv (never a shell string), cwd, env, port policy, pre-steps, readiness, autorestart, depends_on, description, required executable.

## Core behavior

- **Own the process tree.** Package-manager wrappers (uv, bun, npm) exit and orphan the real server, so PIDs alone are not enough. Windows: create suspended, assign to a Job Object with kill-on-close, then resume, in a new process group so CTRL_BREAK reaches it. POSIX: new session, signal the group.
- **Stop in stages.** Graceful signal, grace period, re-enumerate descendants, force-kill survivors.
- **A user stop is not a crash.** Set `stop_requested` before stopping. Exits after it never trigger a restart.
- **Readiness.** A service is `running` only when its readiness signal passes. The default signal is every declared port listening under a PID in the owned tree. Before that it is `starting`.
- **Restarts.** Exponential backoff from 1s, doubling, capped at 30s, with a visible countdown. Park as `failed` after 5 crashes in 60s. Pre-step failures get their own budget of 3 in 120s and their own `build failed` state. Running 30s clears crash history. A manual start clears everything. `should_restart` and `next_delay` are pure functions with unit tests.
- **States.** `stopped`, `building`, `starting`, `running`, `backoff`, `stopping`, `failed`, `build failed`, `blocked`.
- **Logs.** A ring buffer of about 2000 lines per service. Stdout and stderr merged, child ANSI colors preserved, the runner's own lines tagged `[runner]`. A key writes the buffer to `logs/<service>.log`.
- **Shutdown.** Quit stops all services concurrently under a bounded timeout. The same path runs on unmount and Ctrl+C.

## Ports

Every service declares a port policy. Pinning needs evidence found in the project; without it the policy is `dynamic` with a preferred port.

| Policy | Choose when | Behavior |
|---|---|---|
| `pinned` | You found where something outside the runner hardcodes the port: an external client, OAuth callback, CORS allowlist, or another repo's config. Cite that location in the spec. Bookmarks and "conventional" ports are not evidence, because a preferred port already keeps URLs stable. | Preflight before start. Our own leftover holder, identified by cwd or cmdline inside the project with create_time verified, is reclaimed. A foreign holder sets `blocked` with its PID and name, and is released only on explicit confirmation. Never kill PID 0 or 4, the runner, or its parents. |
| `dynamic` | Only this runner's services talk to it | Allocate a free port at each start and pass it in through argv or env. An optional preferred port is tried first so URLs stay stable across runs. Retry with a new port if the bind races. |

A port is free only when the OS listener table shows no listener on it, on any address or IP family. Never trust a bind probe on one address: on Windows a loopback bind succeeds beside another process's `0.0.0.0` listener.

Resolved ports flow to dependents: a provider's actual port is exported into the env of services that depend on it, such as `BACKEND_URL`. Dependents therefore start after their providers are ready. The table always shows the actual bound port, and open-URL uses it.

## Launcher script

Keep it thin. Ensure the toolchain exists, installing and relaunching if missing. Change to the project directory, run the runner forwarding all arguments, and keep the window open on a nonzero exit. All logic lives in the runner.

## CLI

- `--no-autostart`: open the TUI without starting services.
- `--only NAME...`: manage a subset.
- `--check`: non-interactive preflight of config, executables, and ports; plain report and exit code.
- `--force`: reclaim foreign port holders without the prompt.

Without a TTY, behave like `--check` or stream plain prefixed logs. Never start the TUI.

## Default layout

On wide terminals, put the services table on the left, sized to its columns. On the right, put a one-line detail for the selected service above its live log. The detail line holds the description, retry countdown, and actual URL. As the window narrows, drop the `pid` and `auto` columns first, and stack the table above the log only when the log would get under 40 columns. The header holds only the centered title, and the command row sits at the bottom.

Columns come from the project. A typical set is `service`, `state`, `pid`, `ports`, `uptime`, `↻`, `auto`. Typical keys are `s` start, `x` stop, `r` restart, `a` start all, `z` stop all, `A` toggle autorestart, `o` open URL, `w` write log, `?` help, and `q` quit. Project-specific actions get their own keys.

## Common mistakes

| Mistake | Fix |
|---|---|
| Killing by PID; the wrapper exits and the server lives on | Own the tree |
| Treating "process alive" as healthy | Readiness signal |
| Counting user stops as crashes | `stop_requested` |
| One retry budget for builds and crashes | Separate budgets |
| Every port fixed, or every port auto-incremented | Per-service policy |
| Pinning on a guess such as "may be in a CORS list" | Pin only with a cited location; otherwise dynamic with a preferred port |
| Parsing stdout to learn the port | Allocate the port and pass it in |
| Calling a port free because a `127.0.0.1` bind succeeded | Check the listener table on all addresses |
| CPU and memory columns, hot reload, palettes by default | Add only what this project uses |

Implementation notes and reference code: [references/implementation.md](references/implementation.md).
