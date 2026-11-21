# Shared agent configuration

Edit shared skills through `~/.agents/skills/`, and MCP definitions in `~/.agents/mcp.json`.
The same physical skill files are visible through Claude's and Antigravity's native skills
directories (links) and Codex's and Grok's native `.agents/skills` discovery. There is no
copied library to keep in sync. The module README covers how Antigravity and Grok are wired.

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

`bin/agent-claude`, `bin/agent-codex`, `bin/agy` and `bin/agent-grok` are optional provider
wrappers (point T3 Code's provider `binaryPath` at them; T3 recognizes the Antigravity CLI by
the file name `agy`). Each runs `launch.py`, which syncs silently, regenerates the role agents
and hook registrations, then `exec`s the real provider so arguments, protocol streams, signals
and the exit code are the provider's. The real executable is `providers.<name>` (`claude`,
`codex`, `agy`, `grok`) in an optional local `machine.json`, else the first one on PATH that is
not the wrapper. A flock on `state/sync.lock` serializes concurrent starts; the kernel drops it on a
crash, so the file itself can remain. Providers launched without the wrappers use the latest
generated native settings; run `launch.py sync` after editing the registry.

## MCP ownership

`mcp.json` uses the `mcpServers` object with `type`, `command`, `args`, `env`, `url`, and `headers`.
STDIO and HTTP transports are supported; unsupported fields fail instead of being discarded.
The registry updates only its server entries in `~/.claude.json`, a clearly marked block
in `~/.codex/config.toml`, its entries in Antigravity's `~/.gemini/config/mcp_config.json` and
its `[mcp_servers.<name>]` tables in `~/.grok/config.toml`. Unrelated settings and the
unmanaged `node_repl` server are retained. Claude JSON metadata is preserved; the Codex TOML is
parsed for validation, not reserialized. Grok rewrites its own `config.toml` without comments,
so there the owned tables (plus `compat.claude.mcps = false` and the `~/.claude/skills` entry of
`[skills] ignore`, which keep Grok from also loading the home Claude MCP entries and Claude-only
skills) are edited in place with `python-tomlkit` and the result must differ from the original
in nothing else. A native edit to any owned entry is drift, as for Claude and Codex. Provider
wrappers apply every provider whose files accept the change and refuse to start only when
their own provider's files refuse; `sync.py sync` and `launch.py sync` refuse on any conflict.

Whole `${NAME}` values in STDIO `env` become Codex `env_vars` forwarding (the key must also
be NAME). Whole `${NAME}` HTTP header values become `env_http_headers`. Constants remain
constants. Interpolated strings, renamed variables, variable URLs, and defaults are rejected
when they cannot be translated losslessly. For bearer authentication, put the full header
value in the referenced environment variable. Grok expands `${NAME}` (and defaults) in every
field, so its entries match Claude's; its server names must start with a letter or `_` and
contain no `__` and no trailing `_`, or Grok skips their tools. Antigravity expands none: whole `${NAME}` STDIO `env`
values are dropped because the server inherits them, and any other reference is rejected. Never
enter secret values into a committed registry. Auth/OAuth stores stay native to each client.

The registry is local (`mcp.json`, kept out of git); `mcp.example.json` shows the shape. The author's covers `comfyui`, `rea`, `context7` and `blender`. ComfyUI runs with `--compact`,
which exposes only `list_tools`/`describe_tool`/`call_tool` to keep idle context small.
REA 6.9.0 (needs Node >= 26.12.1) runs Claude-only in `hot-cold` catalog mode: 5 tools at startup,
the rest loaded through `discover_tools`. Codex ignores MCP `tools/list_changed`, so it keeps
the full catalog; Antigravity and Grok keep it too. Per-provider environment uses the optional
`providerEnv` field (keys `claude`, `codex`, `antigravity`, `grok`), merged over `env` for that
provider only. `npx` and `uvx` are
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
`pr-wait` script), `leaving-pr-comments`, `sol-loop`, `full-send`, and `full-stop`. On 2026-11-18
the first three were merged into `vcs`: `file-pr` and `leaving-pr-comments` became its "Pull
requests" and "Comments on GitHub" sections, `babysit-pr` became `vcs/babysit.md`, and
`pr-wait` moved to `vcs/scripts/`. Local changes:
`pr-wait` treats an empty CI gate as passed after a 10-minute quiet period on
that head and exits 1 when a Greptile or Macroscope check fails; babysit.md
treats a watcher exit as news, checks full-send's merge gate live (paginated
threads and the user's own comments), owns the untrusted-data rule, and does
not fix or push on a watch-only request, and caps bot-fix rounds at the
review-loop budget; `full-send` checks usage, keeps the
live desktop forbidden, defines that merge gate (confirmed absence of CI, only
`CLEAN` or `HAS_HOOKS`, every check run, no unresolved threads,
bots judged from recent PRs), keeps auto-merge off until that gate passes, and
adds `ml-reviewer` for ML changes; `full-stop` runs only on an explicit
request, stops if auto-merge or the queue cannot be cleared, and stops at that
gate without merging; `sol-loop` follows `agent-roles` review loops, restarts a
failed or silent reviewer once, can fall back to another model family or a
disclosed same-family `reviewer`, and names that reviewer above the PR's
model line; the comment rules point at babysit.md for untrusted data and
attribute comments to the user's GitHub login only; filing a PR pushes first
and its model line replaces harness attribution. Compare against
upstream before updating.
On 2026-11-21 four pieces came from pstack (Lauren Tan, `cursor/plugins/pstack`, MIT, upstream
commit `d42aff08aa58`); `poteto-mode`, its playbooks, the other principles and the model sheet
duplicate the skills above and were not adopted. `correct` and `bro` are skills, with
the source in each `metadata.source` and the license beside it. `roles/adversary.md` adapts
`interrogate`'s adversary brief (license in `roles/LICENSE-pstack`); `council` runs it in the
code council's attacking seats, which replaces `interrogate`'s use of `arena`. `principle-attack-the-premise`
became step 5 of Phase 4 (Phase 4.5) in `systematic-debugging`, with the license beside it. Local changes: no imported skill uses
`disable-model-invocation`, which Codex's skill validator rejects, so their descriptions limit
when they trigger; `correct` also reads the user's recorded corrections (feedback memories,
Claude and Codex transcripts, PR and T3 threads), sends classes that recur across repositories
to `~/.agents` hooks or the owning skill, adds gates through forge, and files deferred classes
as issues; `bro` (word for word the skill in backnotprop/bro, which has no license; pstack ships it under MIT) adds the plain-voice rules from pstack's `ketchup` and keeps questions waiting
on the user; the adversary reruns each finding, reports what held and what it could not run,
and uses `reviewer.md` severities so review loops can consume it. Later that day, from
the same commit, `benchmark-checklist` came as a skill (license beside it), with this machine's
profilers, GPU checks, and pointers to `hillclimb` and `ml-design` in place of pstack's
playbooks; its question 5 replaces upstream's "gap smaller than the run-to-run variation" rule,
which rejects most real improvements, with a preset run count, warmup discard, ABBA order, and
a rank-sum test. `principle-explain-the-number` was folded into `ml-design` (license beside it),
and `hillclimb` now judges benchmark candidates by the checklist's question 5. Compare against upstream before
updating.
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
uv run ~/.agents/skills/council/tests/test_garble.py
python3 -I -B ~/.agents/skills/council/tests/test_witness.py
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

Ten role bodies live in `roles/`, with descriptions/native metadata in
`roles/catalog.json`. `roles/sync_claude.py --write`, `roles/sync_codex.py --write`,
`roles/sync_antigravity.py --write` and `roles/sync_grok.py --write` generate the thin native
Claude (`~/.claude/agents`), Codex (`~/.codex/agents`), Antigravity
(`~/.gemini/config/agents`) and Grok (`~/.grok/agents`) adapters; running them without that
flag checks them. Antigravity gets the Claude tool grants mapped to its own tool names, since
an agent without a `tools` list gets only a small read-only set; Codex and Grok get no list. `agent-roles` supplies the same
briefs for cross-provider T3 delegation. Role edits belong in those common sources.

## Hooks and usage

Native hook behavior lives in `hooks/behavior.py`, with thin Claude, Codex, Grok and
Antigravity adapters (`hooks/native.py`) and generated registrations from `hooks/register.py`.
Run its `check`/`sync` commands when changing registrations; registrations use
`python3 -I -B "$HOME/.agents/hooks/native.py" <provider>` so one settings file works
for any home directory. The only matcher on the shell hook is `Bash` for Claude and Codex,
`^run_terminal_command$` for Grok (`~/.grok/hooks/agents.json`) and `run_command` for
Antigravity (the `agents-common` entry of `~/.gemini/config/hooks.json`); the adapters map
those payloads to the Claude shape the guards read, and the module README lists what each
provider gets. `hooks/fix_route.py` decides where a change goes: a direct push to the
user's own repository, an upstream pull request, that pull request plus a carry onto the fork's
default branch, or a branch and pull request on someone else's repository (`bin/fix-route`
prints it, for debugging). The user's own repository can opt into pull requests with
`git config agents.route pr`; only that repository's own config selects `pr`, never the global
config. `hooks/model_guard.py` denies any subagent or delegate launch (Claude `Agent`, Codex
`spawn_agent`, T3 `delegate_task`) whose model names Fable. Agents aren't told about routes: `hooks/vcs_guard.py` denies a commit on the default
branch of a fork, someone else's repository, or a `pr` repository, and a push or pull request that
misses the route. In a `pr` repository it also denies a merge, cherry-pick, rebase, `am`, revert
or pull on the default branch (mid-operation too) unless it only fast-forwards from origin's
default branch (`git pull --ff-only`, optionally `origin <default>`; `git merge --ff-only
origin/<default>`), and a `gh pr create` that targets another repository, runs on the default
branch, or leaves gh to pick the base among several GitHub remotes without `--repo <origin>`.
A configured route never guesses origin's default branch: when `origin/HEAD` is missing it
denies and says to run `git remote set-head origin -a`. An explicit route that cannot be applied
(an invalid `agents.route`, or `upstream` without its remote or refs) denies every mutation with
the router's error instead of allowing. Each reason is the whole instruction; after an
upstream PR opens, a Bash PostToolUse note says to carry it or to ask about carrying. It runs
offline, allows what it can't classify without a configured route, and runs before Codex rewrites
the command through RTK.

Threat model: the guard steers well-meaning agents away from the wrong integration route. It is
not a security boundary against a deliberately crafted command (an option value that looks like a
flag, shell variable state, an overridden `GH_HOST`), and it is not a Git or gh parser.
Claude's RTK rewrite stays its own `rtk hook claude` hook. Native hook trust and
lifecycle delivery still require acceptance in a fresh session. Shared script
changes use the same path in both providers; do not maintain second provider copies.

Codex runs these hooks only after they are trusted in its interactive TUI (hashes in
`[hooks.state]` of `~/.codex/config.toml`); editing `hooks.json` requires trusting them again.

The Impeccable design hook runs the engine from `$IMPECCABLE_BIN`, the binary beside the skill
(`skills/impeccable/scripts/bin/linux-<arch>/impeccable`), or `impeccable` on PATH, which the
`impeccable-engine` package provides. It never downloads anything.

`usage/budget.py` (private module) reports observed quota usage formatted as remaining-budget
pace (`remaining/time-left` vs even weekly pace: `>1x` under pace, `<1x` over pace) plus
available free OpenCode Zen and OpenRouter tool-calling models. Without it the hooks add no
usage context and the Claude `statusLine` is not registered. Probes read Claude's native
`get_usage` control request and Codex's `account/rateLimits/read` via temporary stdio processes
(hooks/MCP disabled, no model prompts), Grok's fixed HTTPS billing endpoint using its default
native login (no redirects or token refreshes), Antigravity's `retrieveUserQuotaSummary` using
the freshest native OAuth access token (refreshing via `agy models` or Google's token endpoint
when expired, no redirects), OpenCode's local `opencode models --verbose`, and OpenRouter's
public `/api/v1/models` (only when a local OpenRouter key is configured). Quota reads cache for
five minutes and free-model catalogs for six hours; `--snapshot` uses those caches (for hooks
and the OpenCode plugin), while `--refresh` forces fresh reads. WPO's separate routing usage
adapter (`~/.claude/hooks/usage_budget.py`) is a distinct integration: it reads the Claude Code
and CLIProxyAPI OAuth token files and queries only `api.anthropic.com` and `chatgpt.com`.
