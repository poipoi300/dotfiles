# agents

Shared AI-agent tooling for Claude Code and Codex, deployed as `~/.agents` and ported from the
Windows setup.

| Piece | Where |
|---|---|
| Shared instructions (`AGENTS.md`, `RTK.md`), linked as `~/.claude/CLAUDE.md`, `~/.claude/RTK.md`, `~/.codex/AGENTS.md` | `home/.agents/`, `links` in `module.toml` |
| 25 generic skills (agent-roles, benchmark-checklist, bro, building-runner-tuis, coding, correct, council, designing-tuis, forge, full-send, full-stop, gauntlet-loop, hillclimb, humanizer, impeccable, ml-design, project-management, project-setup, research-report, reverse-engineer-anything, skill-pressure-testing, sol-loop, systematic-debugging, system-changes, vcs) | `home/.agents/skills/` |
| MCP registry sync, skill links into Claude (`sync.py`), provider wrappers (`launch.py`, `bin/agent-*`) | `home/.agents/` |
| Ten role briefs and the Claude/Codex adapter generators | `home/.agents/roles/` |
| Native hooks (route guard, RTK rewrite for Codex, Impeccable design feedback, registration) | `home/.agents/hooks/` |
| Tests | `home/.agents/tests/`, `home/.agents/hooks/tests/` |
| RTK (github.com/rtk-ai/rtk) and the Impeccable engine, pinned and checksummed | `pkg/rtk/`, `pkg/impeccable-engine/` |

Not in this module, on purpose: `settings.json` for Claude and `config.toml`/`hooks.json` for
Codex (they hold local state), the MCP registry `mcp.json` (see `mcp.example.json`), usage
budget scripts, and personal skills. The private `agents-private` module adds those.

## Packages

pacman: `python` (3.11+), `python-yaml` (tests), `nodejs`, `npm` (`npx` MCP servers), `uv`
(`uvx` MCP servers). Own builds: `rtk`, `impeccable-engine`. Claude Code and Codex themselves
come from the `dev` module. `ripgrep` and `git` are expected from the base setup.

## Install order

1. `arcus on agents` (or `agents-private`, which requires it), then `arcus apply`. This builds
   the two local packages with makepkg, links `~/.agents` files into `$HOME`, and creates the
   three instruction links.
2. Merge the native settings by hand (they are not deployed): Claude's `settings.json` (hooks
   run `python3 -I -B "$HOME/.agents/hooks/native.py" claude`; Claude's Bash
   PreToolUse is RTK's `rtk hook claude` plus this adapter's route guard;
   statusLine needs the private `usage/budget.py`), Codex's `config.toml`
   and `hooks.json`. Do not run `rtk init -g`: it would rewrite those files.
3. `cp ~/.agents/mcp.example.json ~/.agents/mcp.json` and edit it (the private module ships
   a real one).
4. `python3 -I -B ~/.agents/launch.py sync`: writes the MCP entries into `~/.claude.json` and a
   marked block in `~/.codex/config.toml`, links every shared skill into `~/.claude/skills`,
   generates `~/.claude/agents/*.md` and `~/.codex/agents/*.toml` pointing at
   `~/.agents/roles/<role>.md`, and checks the hook registrations against what you merged.
5. `python3 -I -B ~/.agents/sync.py check` must print that everything is current (exit 0).
6. Run the tests (see `home/.agents/README.md`); trust the Codex hooks once in its TUI; start
   fresh Claude and Codex sessions.
7. Optional: set T3 Code's provider `binaryPath` to `~/.agents/bin/agent-claude` and
   `~/.agents/bin/agent-codex` so every start repeats step 4 silently.

## Notes

- arcus links files one by one, so `~/.agents` stays a real directory: sync state, backups,
  generated adapter state and hook markers are written there, never into this repository.
  The scripts use `os.path.abspath(__file__)` rather than `resolve()` for that reason.
- The Impeccable engine package must track `skills/impeccable/scripts/VERSION`; a test compares
  the PKGBUILD's version and sha256 with the skill's `engine-checksums.txt`.
- RTK is the default edition (Apache-2.0 CLI plus the BUSL-1.1 engine, production use free).
  `pkg/rtk/PKGBUILD` explains how to switch to the Apache-only `rtk-oss` edition.
- Bumping a pin: change `pkgver` and the sha256 from the upstream release's checksums, then
  `makepkg -f` in a scratch copy before `arcus rebuild`.
