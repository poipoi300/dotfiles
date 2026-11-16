# Shared agent configuration

Edit shared skills through `~/.agents/skills/`, and MCP definitions in `~/.agents/mcp.json`.
The same physical skill files are visible through Claude's native skills directory and Codex's
native `.agents/skills` discovery. There is no copied library to keep in sync.

`~/.agents/AGENTS.md` is the common instruction source, linked into the providers' native
global instruction paths. Project `AGENTS.md` files remain project-owned. Changing shared
instructions or skills does not rewrite a currently running conversation's loaded context;
start a fresh provider session to verify discovery.

## Maintenance

```sh
python3 -I -B ~/.agents/sync.py check
python3 -I -B ~/.agents/sync.py sync --dry-run
python3 -I -B ~/.agents/sync.py sync
python3 -I -B ~/.agents/launch.py sync     # MCP sync + role adapters + hook registration, once
```

`check` returns 0 when current, 1 when a sync is needed, and 2 for a collision, drift, invalid
configuration, or I/O error. `sync` translates managed MCP definitions and creates missing
Claude links for new shared skills. It refuses to replace a different real folder or link.
Resolve collisions by comparing and archiving the alternate copy, never by blindly forcing a
sync. There is deliberately no force flag. It also refuses changes made directly to owned
native MCP entries; reconcile that edit with the registry and restore the last generated
native entry before running sync again.

`bin/agent-claude` and `bin/agent-codex` are optional provider wrappers (point T3 Code's
provider `binaryPath` at them). Each runs `launch.py`, which syncs silently, regenerates the
role agents and hook registrations, then `exec`s the real provider so arguments, protocol
streams, signals and the exit code are the provider's. The real executable is `providers.<name>`
in an optional local `machine.json`, else the first `claude`/`codex` on PATH that is not the
wrapper. A flock on `state/sync.lock` serializes concurrent starts; the kernel drops it on a
crash, so the file itself can remain. Providers launched without the wrappers use the latest
generated native settings; run `launch.py sync` after editing the registry.

## MCP ownership

`mcp.json` uses the `mcpServers` object with `type`, `command`, `args`, `env`, `url`, and `headers`.
STDIO and HTTP transports are supported; unsupported fields fail instead of being discarded.
The registry updates only its server entries in `~/.claude.json` and a clearly marked block
in `~/.codex/config.toml`. Unrelated settings and the unmanaged `node_repl` server are retained.
Claude JSON metadata is preserved; existing TOML is parsed for validation, not reserialized.

Whole `${NAME}` values in STDIO `env` become Codex `env_vars` forwarding (the key must also
be NAME). Whole `${NAME}` HTTP header values become `env_http_headers`. Constants remain
constants. Interpolated strings, renamed variables, variable URLs, and defaults are rejected
when they cannot be translated losslessly. For bearer authentication, put the full header
value in the referenced environment variable. Never enter secret values into a committed
registry. Auth/OAuth stores stay native to each client.

The registry is local (`mcp.json`, kept out of git); `mcp.example.json` shows the shape. The author's covers `comfyui`, `rea`, `context7` and `blender`. ComfyUI runs with `--compact`,
which exposes only `list_tools`/`describe_tool`/`call_tool` to keep idle context small.
REA 6.9.0 (needs Node >= 26.12.1) runs Claude-only in `hot-cold` catalog mode: 5 tools at startup,
the rest loaded through `discover_tools`. Codex ignores MCP `tools/list_changed`, so it keeps
the full catalog. Per-provider environment uses the optional `providerEnv` field
(`{"claude": {...}, "codex": {...}}`), merged over `env` for that provider only. `npx` and `uvx` are
launched directly. Context7's redundant Claude plugin is disabled by the
explicit `claudePluginOverrides` field, whose native flag is tracked for drift too.
Adding an entry to that field intentionally adopts its current plugin flag on first sync.

Every changed native file is backed up before a sync. `backups/<timestamp>/manifest.json`
maps originals to backups; missing-original entries are recorded as null. A failed write
restores already-written files. `state/sync-state.json` contains hashes and ownership, not
credential values. Backups can contain private native configuration: keep `backups`, `state`,
`mcp.json`, and `machine.json` out of public repositories. Use `mcp.example.json` for sharing.

## Skill sources and exclusions

Editable skills live in `~/.agents/skills/`; `sync.py` links each one into `~/.claude/skills/`
(Codex discovers `~/.agents/skills` natively). WPO stays in its independently maintained
repository and is linked into the common root; its source is not part of this tree.

On 2026-11-12 these were removed: Graphify (not useful); `daily-brief` and `frontend-design`
(overlapping); and the Claude links to account-synced `deep-research` and `morning`. Account
skills still load as `anthropic-skills:*` until disabled in the claude.ai account.

Five document/card skills (`docx`, `pdf`, `pptx`, `xlsx`, `sillytavern-cards`) link to the existing
read-only Claude account-synced targets. They are external dependencies, not vendored source;
their contents and attributes were not changed. Their paths are excluded from publication.
Their tools may still require packages or workflow adaptation when used outside Claude.

Claude-hosted `chrome-browser`, `computer-use`, `docs`, and `import-memory` remain Claude-only
pending tool adaptation. The account-synced `skill-creator`
stays Claude-only to avoid shadowing Codex's bundled `skill-creator`. `skill-pressure-testing`
(formerly `writing-skills`) is only for explicitly requested test-first pressure testing; ordinary
creation uses the runtime's own authoring skill.
On 2026-11-15 six PR-workflow skills were imported from tstack (MIT, upstream commit in each
skill's `metadata.source`, license copied beside it): `file-pr`, `babysit-pr` (with its
`pr-wait` script), `leaving-pr-comments`, `sol-loop`, `full-send`, and `full-stop`. Local changes:
`babysit-pr` says when to use `--until green` and what it skips; `full-send` checks usage first,
names the GUI testing rule, and adds `ml-reviewer` for ML changes; `full-stop` names the GUI
rule; `sol-loop` falls back to another model family per AGENTS.md instead of stopping;
`file-pr`'s model line replaces harness attribution. Compare against upstream before updating.
The proxy-specific `image-generation` skill is reserved for that requested backend; ordinary
image tasks use a runtime's built-in image skill when available.

Skill content being shared does not create missing browser tools, custom-agent registries,
MCP tools, or lifecycle hooks. Native agent adapters and hook registrations are generated
per provider from the shared sources above.

## Validation

```sh
python3 -I -B -m unittest discover -s ~/.agents/tests
python3 -I -B -m unittest discover -s ~/.agents/hooks/tests
python3 -I -B -m unittest discover -s ~/.agents/usage/tests   # private module only
python3 ~/.agents/skills/humanizer/tests/test_checker.py
```

Tests cover configuration preservation, translation, drift, collision refusal, backup and
rollback, links, concurrent launches, provider argument/exit forwarding, hook registration and
shell rewriting. `rtk` and the Impeccable engine are needed for the tests that call them
(they skip otherwise); `python-yaml` for the role-adapter tests. Run them from the installed
`~/.agents`, not from a repository checkout: the layout checks assume `~/.agents` is the root.
Native version probes are safe smoke tests; they do not prove hosted-provider authentication
or MCP health, and discovery does not establish execution compatibility for every skill.

Research uses native web tools. A personalized daily brief requires connected sources;
desktop control and account memory import still depend on host-only capabilities.

Nine role bodies live in `roles/`, with descriptions/native metadata in
`roles/catalog.json`. `roles/sync_claude.py --write` and `roles/sync_codex.py --write`
generate the thin native Claude (`~/.claude/agents`) and Codex (`~/.codex/agents`)
adapters; running them without that flag checks them. `agent-roles` supplies the same
briefs for cross-provider T3 delegation. Role edits belong in those common sources.

## Hooks and usage

Native hook behavior lives in `hooks/behavior.py`, with thin Claude/Codex adapters
(`hooks/native.py`) and generated registrations from `hooks/register.py`. Run its
`check`/`sync` commands when changing registrations; registrations use
`python3 -I -B "$HOME/.agents/hooks/native.py" <provider>` so one settings file works
for any home directory. The only matcher on the shell hook is `Bash` (Codex's
PreToolUse; Claude uses RTK's own `rtk hook claude`). Native hook trust and lifecycle
delivery still require acceptance in a fresh session. Shared script changes use the same
path in both providers; do not maintain second provider copies.

Codex runs these hooks only after they are trusted in its interactive TUI (hashes in
`[hooks.state]` of `~/.codex/config.toml`); editing `hooks.json` requires trusting them again.

The Impeccable design hook runs the engine from `$IMPECCABLE_BIN`, the binary beside the skill
(`skills/impeccable/scripts/bin/linux-<arch>/impeccable`), or `impeccable` on PATH, which the
`impeccable-engine` package provides. It never downloads anything.

`usage/budget.py` (private module) reports only supported observed usage and explicit
unknown/stale state; without it the hooks simply add no usage context and the Claude
statusLine is not registered. WPO's separate routing usage adapter
(`~/.claude/hooks/usage_budget.py`) is a distinct integration: it reads the Claude Code and
CLIProxyAPI OAuth token files and queries only api.anthropic.com and chatgpt.com.
