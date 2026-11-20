# dev

Python environments are per project: uv, or pixi when conda packages are needed (the
`project-setup` skill owns that choice).
`~/.config/uv/.python-version` pins 3.15 as the default; install it once per machine with
`uv python install 3.15`. No other system Python packages beyond what other packages pull in.

Rust comes from rustup (`rustup default stable`).

Claude Code is the vendor's native install, not a package: `~/.local/bin/claude` links into
`~/.local/share/claude/versions/`. It gets releases before the AUR package does, and it updates
itself (`claude update`; background updates are off via `autoUpdates: false`). This
self-updating installer is a deliberate exception to the no-self-updaters rule.
