"""Provider startup adapter: refresh shared configuration, then exec the real provider.

`launch.py claude|codex [args...]` syncs the MCP registry, the generated role adapters and the
hook registrations (silently; stdout belongs to the provider's protocol stream), then replaces
itself with the provider's own executable so arguments, streams, signals and the exit code are
the provider's. Point T3 Code's provider `binaryPath` at `bin/agent-claude` or `bin/agent-codex`
to get this on every start. `launch.py sync` runs the same refresh and exits, which is what the
install steps use.

The provider's original executable comes from `providers.<name>` in an optional
`~/.agents/machine.json`, else the first `<name>` on PATH that is not this wrapper.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# abspath, not resolve(): arcus links this file into $HOME, and the shared tree is ~/.agents.
HERE = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(HERE))
from sync import SyncError, apply, read_json, sync_lock


def load_module(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sync_generated_adapters(home: Path) -> None:
    """Regenerate native role agents and hook registrations from the shared sources.

    A failure is reported on stderr but never blocks the provider; stdout is the provider's
    protocol stream, so the renderers' progress messages are discarded."""
    import io
    from contextlib import redirect_stdout
    roles = home / ".agents/roles"
    try:
        with sync_lock(home), redirect_stdout(io.StringIO()):
            for renderer, agents_dir in (("sync_claude", home / ".claude/agents"), ("sync_codex", home / ".codex/agents")):
                load_module(f"common_{renderer}", roles / f"{renderer}.py").synchronize(roles, agents_dir, write=True)
            load_module("common_hook_register", home / ".agents/hooks/register.py").synchronize(home, apply=True)
    except (SyncError, ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(f"Common provider startup: role agents or hooks not refreshed ({type(error).__name__}: {error}).",
              file=sys.stderr)


def provider_executable(home: Path, provider: str) -> Path:
    configured = read_json(home / ".agents/machine.json").get("providers", {}).get(provider)
    if configured:
        executable = Path(configured).expanduser().resolve()
    else:
        wrapper_dir = os.path.abspath(home / ".agents/bin")
        search = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                                 if os.path.abspath(entry or ".") != wrapper_dir)
        found = shutil.which(provider, path=search)
        if not found:
            raise SyncError(f"No {provider} executable on PATH; set providers.{provider} in machine.json")
        executable = Path(found).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK) or executable.suffix == ".py":
        raise SyncError("Provider original must be a real executable, not a wrapper")
    return executable


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"claude", "codex", "sync"}:
        print("Expected provider name claude or codex, or sync", file=sys.stderr)
        return 2
    provider, forwarded = arguments[0], arguments[1:]
    home = Path.home()
    try:
        executable = provider_executable(home, provider) if provider != "sync" else None
        # Never print success diagnostics onto the provider's JSON/stdout protocol.
        changes = apply(home, home / ".agents/mcp.json")
    except (SyncError, ValueError, OSError, KeyError) as error:
        detail = str(error) if isinstance(error, SyncError) else type(error).__name__
        print(f"Common provider startup refused: {detail}. Run ~/.agents/sync.py check locally.", file=sys.stderr)
        return 2
    sync_generated_adapters(home)
    if executable is None:
        for change in changes:
            print(f"updated: {change.path}")
        return 0
    # Replace this process: the provider owns stdin/stdout/stderr, signals and the exit code.
    os.execv(str(executable), [str(executable), *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
