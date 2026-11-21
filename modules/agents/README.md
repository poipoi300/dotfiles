# agents

Shared AI-agent tooling for Claude Code, Codex, Antigravity (`agy`) and Grok Build (`grok`),
deployed as `~/.agents` and ported from the Windows setup.

| Piece | Where |
|---|---|
| Shared instructions (`AGENTS.md`, `RTK.md`), linked as `~/.claude/CLAUDE.md`, `~/.claude/RTK.md`, `~/.codex/AGENTS.md`, `~/.gemini/config/AGENTS.md`, `~/.grok/rules/AGENTS.md` | `home/.agents/`, `links` in `module.toml` |
| 25 generic skills (agent-roles, benchmark-checklist, bro, building-runner-tuis, coding, correct, council, designing-tuis, forge, full-send, full-stop, gauntlet-loop, hillclimb, humanizer, impeccable, ml-design, project-management, project-setup, research-report, reverse-engineer-anything, skill-pressure-testing, sol-loop, systematic-debugging, system-changes, vcs) | `home/.agents/skills/` |
| MCP registry sync, skill links into Claude and Antigravity (`sync.py`), provider wrappers (`launch.py`, `bin/agent-*`, `bin/agy`) | `home/.agents/` |
| Ten role briefs and the Claude, Codex, Antigravity and Grok adapter generators | `home/.agents/roles/` |
| Native hooks (route and model guards, RTK rewrite for Codex and Grok, Impeccable design feedback, registration) | `home/.agents/hooks/` |
| Tests | `home/.agents/tests/`, `home/.agents/hooks/tests/` |
| RTK (github.com/rtk-ai/rtk) and the Impeccable engine, pinned and checksummed | `pkg/rtk/`, `pkg/impeccable-engine/` |

Not in this module, on purpose: `settings.json` for Claude and `config.toml`/`hooks.json` for
Codex (they hold local state), the MCP registry `mcp.json` (see `mcp.example.json`), usage
budget scripts, and personal skills. The private `agents-private` module adds those.

## Packages

pacman: `python` (3.11+), `python-yaml` (tests), `python-tomlkit` (edits to Grok's
`config.toml`), `nodejs`, `npm` (`npx` MCP servers), `uv` (`uvx` MCP servers). Own builds:
`rtk`, `impeccable-engine`. Claude Code and Codex themselves come from the `dev` module, `agy`
and `grok` from the `ai` module. `ripgrep` and `git` are expected from the base setup.

## Install order

1. `arcus on agents` (or `agents-private`, which requires it), then `arcus apply`. This builds
   the two local packages with makepkg, links `~/.agents` files into `$HOME`, and creates the
   five instruction links.
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
   Once `~/.gemini` and `~/.grok` exist it also does the same for Antigravity and Grok (see
   below); their hook registrations are generated whole, with nothing to merge by hand.
5. `python3 -I -B ~/.agents/sync.py check` must print that everything is current (exit 0).
6. Run the tests (see `home/.agents/README.md`); trust the Codex hooks once in its TUI; start
   fresh Claude, Codex, Antigravity and Grok sessions.
7. Optional: set T3 Code's provider `binaryPath` to `~/.agents/bin/agent-claude`,
   `~/.agents/bin/agent-codex`, `~/.agents/bin/agy` and `~/.agents/bin/agent-grok` (absolute
   paths) so every start repeats step 4 silently. The Antigravity wrapper is named `agy`
   because T3 starts a binary path as the Antigravity CLI (`agy acp`) only when its file name is
   `agy` or `antigravity`.

## Antigravity and Grok

Both CLIs are managed once their home folder exists (`~/.gemini`, `~/.grok`); the instruction
links above create them. In T3 both run as ACP providers and read the same global files as in
a terminal: T3 gives `agy` a private `GEMINI_HOME`, but the CLI still reads `~/.gemini/config`
(checked with logging hooks in T3-started sessions).

| | Antigravity CLI | Grok Build |
|---|---|---|
| Instructions | `~/.gemini/config/AGENTS.md` (global rule) | `~/.grok/rules/AGENTS.md` (global rule) |
| Skills | links in `~/.gemini/config/skills/`, the folder T3 also links into its profile | scans `~/.agents/skills` itself |
| MCP servers | owned entries in `~/.gemini/config/mcp_config.json` | owned `[mcp_servers.<name>]` tables in `~/.grok/config.toml` |
| Roles | `~/.gemini/config/agents/*.md`: subagents, Claude tool grants mapped to native tools | `~/.grok/agents/*.md`, which win over the same names in `~/.claude/agents` |
| Hooks | named entry `agents-common` in `~/.gemini/config/hooks.json` | whole file `~/.grok/hooks/agents.json` |

- Grok's Claude compatibility scan also reads home and project Claude files. Two home-level
  parts are switched off because the native files replace them: `compat.claude.mcps = false`
  (that cell covers only `~/.claude.json`) and `~/.claude/skills` in `[skills] ignore`, which
  hides the Claude-only account skills (browser, computer use, docs) by path, so a project's
  `.claude/skills` still loads. Both are owned like the MCP tables. `~/.claude/CLAUDE.md` stays
  on: Grok loads it once with `~/.grok/rules/AGENTS.md`, since both are the same file. Claude
  hooks stay on too, so a project's `.claude/settings.json` hooks still run in Grok; the home
  Claude registrations do nothing when Grok runs them (`hooks/native.py` recognizes Grok's
  payload), and the Grok registration does the work. Grok rewrites `config.toml` and drops
  comments, so a marked block like Codex's would not survive; `python-tomlkit` edits the owned
  keys in place and the result is checked against the original for any other change.
- A provider start refuses only on a conflict in its own files, including a hook registration
  that could not be written: drift in Grok's `config.toml` is reported on stderr when Claude
  starts and blocks only Grok's start. A stale role adapter only warns. `launch.py sync` and
  `sync.py sync` refuse (exit 2) on any conflict.
- Antigravity's `mcp_config.json` expands no environment references. A whole `${VAR}` in a
  stdio server's `env` is dropped (the server inherits it); any other reference is refused.
  Grok expands `${VAR}` in every field, so entries pass through as in Claude.
- Hooks: both get the route guard and the Fable model guard on their shell and T3
  `delegate_task` calls, and Grok also gets the RTK rewrite and the route reminder after a PR
  opens. Grok cuts the long strings of a tool input over 128 KiB but runs the whole command, so
  a truncated shell call is denied with a reason asking for a shorter command. Antigravity gets
  the usage snapshot at session start. Antigravity's PreToolUse has no
  "no opinion" reply: `{}` or a non-zero exit denies the call and only an empty reply leaves it
  to Antigravity, so the adapter prints nothing unless it denies, and the registered command
  ends in `|| true`. RTK's own `rtk hook antigravity` answers `allow` to every call, which
  would skip Antigravity's permission checks, so it is not registered. Grok reads only a
  `systemMessage` at session start, so it gets no usage snapshot; neither gets the Impeccable
  design feedback, whose engine reads Claude and Codex payloads.

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
