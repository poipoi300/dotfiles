---
name: system-changes
description: How to install software and change system or app configuration on the user's Arch machine - through arcus modules in the dotfiles repos, with supply-chain checks and no secrets in git. Load before installing, removing or updating any program, adding a PKGBUILD, editing any config file under ~/.config, /etc or a systemd unit, adding a script, or creating a skill. Not needed for edits inside a project repository's own code.
---

# System changes

The machine is managed by **arcus** (the user's fork, `~/src/arcus`): every package, config file,
unit and group belongs to a module. A change made outside a module is drift; the next
`arcus doctor` reports it and `arcus clean` may undo it.

## Where things live

| What | Where |
|---|---|
| Shared config, packages, units (no personal details) | public `~/dotfiles/modules/<name>/` (= `~/.config/arcus`) |
| Config with personal details (paths with the username, LAN IPs, hostnames, emails, serials, monitor names) | private `~/dotfiles-private/modules/<name>/` (loaded via `module_dirs`) |
| Machine-only facts (kernel, drives, labels) | `/etc/arcus/modules/` (`arcus machine`; `storage-local`) |
| The user's scripts | private repo `~/src/scripts` (github poipoi300/scripts) |
| Shared skills, AGENTS.md, MCP definitions | `agents` module → `~/.agents` (MCP servers in `~/.agents/mcp.json`, private module) |
| Secrets (keys, tokens, OAuth state) | never in git: provider stores, `~/.cli-proxy-api`, untracked 0600 env files, Bitwarden |

Module layout: `module.toml` (`[packages] pacman/aur/local`, `[units] system/user`, `groups`,
`links`), `home/` mirrored into `$HOME` as symlinks, `system/` copied into `/etc` and
`/usr/local`, `pkg/<name>/PKGBUILD` own builds. Profile `profiles/desktop.toml` lists modules;
the host file `~/.local/state/arcus/host.toml` adds machine-local ones. Read
`~/src/arcus/README.md` for details.

## Workflow

1. **Edit the module, not the live file.** Live files under `~` are symlinks into the repo: edit
   the repo copy (the symlink makes it live at once). For a new file, add it under `home/` or
   `system/`; for a package, add it to `module.toml`. Never `pacman -S`/`yay -S` directly except
   to unblock something urgent, and then list the package in a module right away.
2. `arcus plan` and read it (conflicts, removals, overwritten files go to arcus backups), then
   `arcus apply -y < /dev/null` (flags before the module name: `arcus on -y <module>`).
   Long builds: run detached (`systemd-run --user --unit=... sh -c 'arcus apply -y ...'`) so a
   session restart can't kill them. Don't edit modules while an apply runs: arcus refuses root
   steps that changed since its plan.
3. `arcus doctor`: nothing undeclared. Then commit in small commits.
4. Before **every push**: `gitleaks git --redact .` in that repo, and grep the outgoing diff for
   usernames, `/home/` paths, LAN IPs, emails, hostnames, serials, drive labels, Windows paths.
   Commit identity is the GitHub noreply address (pushes with any other email are rejected).

## Installing software

Order of preference: official repos → AUR (only after reading the PKGBUILD and .install: source
URLs must be the project's official org/domain; pinned checksums; no build-time fetches of
unpinned code; no surprising root scripts) → own PKGBUILD in `pkg/` from the vendor's official
release, pinned version and sha256 (verify against published checksums/signatures). Never
`curl | sh`, never a download link from a search result, never Flatpak or self-updating
installers unless the user agrees (then note it in the module README). Always the latest
upstream version unless the user says otherwise. Python is uv-only (per-project venvs,
`uv python install`); no extra system Python packages.

## Config hygiene

- Read a config before copying or porting it; strip secrets, Windows paths and machine state
  (window geometry, recent files, caches). Apps that rewrite their own config are fine:
  Qt/KConfig/Quickshell atomic saves write through arcus symlinks.
- Generated files (matugen/DMS output, `niri/dms/*.kdl` marked "do not edit") stay out of modules;
  theme them through the generator (user matugen templates in `niri-desktop`).
- Match hardware by stable names (monitor make/model/serial in private config), not connector
  names that change across driver updates.
- Units that need user-supplied config get `ConditionPathExists=` so they idle instead of failing.
- GUI and audio testing follow AGENTS.md "GUI testing": never on the user's screen or devices.

## Skills

New or changed shared skills go in `~/dotfiles/modules/agents/home/.agents/skills/<name>/`
(private ones in `agents-private`), then `python3 -I -B ~/.agents/launch.py sync` and
`python3 -I -B ~/.agents/sync.py check` to link them into each provider.
