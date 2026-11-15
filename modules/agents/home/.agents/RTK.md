# RTK - Rust Token Killer

RTK (https://github.com/rtk-ai/rtk) compresses shell command output to save tokens.
A PreToolUse hook rewrites Bash commands automatically (Claude: `rtk hook claude` in
`~/.claude/settings.json`; Codex: `~/.agents/hooks/native.py` via
`~/.codex/hooks.json`), so you do not need to type the `rtk` prefix. A command
that already starts with `rtk` is left as is. The hook only fires on Bash tool
calls; built-in tools such as Read, Grep and Glob do not pass through it.

## When output looks wrong

The rewritten output can drop lines, summarize, or reformat (for example `ls`
becomes `644  file  1.0K`). If you need the exact output:

```bash
rtk bypass <cmd>         # run the raw command without filtering (older releases: rtk proxy <cmd>)
```

A warning about untrusted project filters (`.rtk/filters.toml`) means RTK
ignored that directory's filters; it is harmless. Do not run `rtk trust` without
asking the user. Configuration lives in `~/.config/rtk`; telemetry is off unless the user
enabled it.

## Checks

```bash
rtk --version
rtk gain                 # savings dashboard; "command not found" means another "rtk" is installed
rtk gain --history       # recent commands RTK handled, including hook rewrites
```
