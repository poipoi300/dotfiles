"""Provider startup adapter: refresh shared configuration, then exec the real provider.

`launch.py claude|codex|agy|grok [args...]` syncs the MCP registry, the generated role adapters
and the hook registrations (silently; stdout belongs to the provider's protocol stream), then
replaces itself with the provider's own executable so arguments, streams, signals and the exit
code are the provider's. Point T3 Code's provider `binaryPath` at `bin/agent-claude`,
`bin/agent-codex`, `bin/agy` or `bin/agent-grok` to get this on every start. `launch.py sync`
runs the same refresh and exits, which is what the install steps use.

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
from sync import SyncError, apply, provider_enabled, read_json, sync_lock

# Executable name -> the provider key sync.py and hooks/register.py use.
PROVIDERS = {"claude": "claude", "codex": "codex", "agy": "antigravity", "grok": "grok"}
WRAPPERS = ("agent-claude", "agent-codex", "agy", "agent-grok")
STARTUP_ERRORS = (SyncError, ValueError, OSError, KeyError, TypeError, AttributeError)


def role_renderers(home: Path) -> list[tuple[str, Path]]:
    """Each provider's role renderer and agents folder; Antigravity and Grok once installed."""
    renderers = [("sync_claude", home / ".claude/agents"), ("sync_codex", home / ".codex/agents")]
    if provider_enabled(home, "antigravity"):
        renderers.append(("sync_antigravity", home / ".gemini/config/agents"))
    if provider_enabled(home, "grok"):
        renderers.append(("sync_grok", home / ".grok/agents"))
    return renderers


def load_module(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sync_generated_adapters(home: Path) -> dict:
    """Regenerate native role agents and hook registrations from the shared sources.

    Failures are reported on stderr and returned: `hooks:<provider>` for a provider whose hook
    registration was refused, the step's label otherwise. stdout is the provider's protocol
    stream, so the renderers' progress messages are discarded."""
    import io
    from contextlib import redirect_stdout
    roles = home / ".agents/roles"
    # Each step on its own: one provider's edited adapter must not leave the others stale.
    steps = [(renderer, lambda renderer=renderer, agents_dir=agents_dir: load_module(
        f"common_{renderer}", roles / f"{renderer}.py").synchronize(roles, agents_dir, write=True))
        for renderer, agents_dir in role_renderers(home)]
    problems: dict = {}
    steps.append(("hook registrations", lambda: load_module(
        "common_hook_register", home / ".agents/hooks/register.py").synchronize(home, apply=True, problems=problems)))
    failures: dict = {}
    try:
        with sync_lock(home), redirect_stdout(io.StringIO()):
            for label, step in steps:
                try:
                    step()
                except (SyncError, ValueError, OSError, KeyError, TypeError, AttributeError) as error:
                    failures[label] = error
                    print(f"Common provider startup: {label} not refreshed ({type(error).__name__}: {error}).",
                          file=sys.stderr)
            for provider, error in problems.items():
                failures[f"hooks:{provider}"] = error
                print(f"Common provider startup: {provider} hook registration not refreshed ({error}).", file=sys.stderr)
    except (SyncError, OSError) as error:
        failures["lock"] = error
        print(f"Common provider startup: role agents or hooks not refreshed ({error}).", file=sys.stderr)
    return failures


def provider_executable(home: Path, provider: str) -> Path:
    wrapper_dir = os.path.abspath(home / ".agents/bin")
    # The wrappers resolve into the dotfiles checkout; a link to one elsewhere on PATH (such as
    # ~/.local/bin/agy) is the wrapper too and would exec itself forever.
    wrappers = {(Path(wrapper_dir) / name).resolve() for name in WRAPPERS if os.path.lexists(Path(wrapper_dir) / name)}
    configured = read_json(home / ".agents/machine.json").get("providers", {}).get(provider)
    if configured:
        executable = Path(configured).expanduser().resolve()
    else:
        executable = None
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.abspath(entry or ".") == wrapper_dir:
                continue
            found = shutil.which(provider, path=entry or ".")
            if found and Path(found).resolve() not in wrappers:
                executable = Path(found).resolve()
                break
        if executable is None:
            raise SyncError(f"No {provider} executable on PATH; set providers.{provider} in machine.json")
    if (executable in wrappers or not executable.is_file() or not os.access(executable, os.X_OK)
            or executable.suffix == ".py"):
        raise SyncError("Provider original must be a real executable, not a wrapper")
    return executable


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {*PROVIDERS, "sync"}:
        print(f"Expected provider name {', '.join(PROVIDERS)}, or sync", file=sys.stderr)
        return 2
    provider, forwarded = arguments[0], arguments[1:]
    home = Path.home()
    # `sync` refuses on any provider's conflict; a provider start refuses only on its own, so a
    # conflict in Grok's files never keeps Claude from starting.
    problems = None if provider == "sync" else {}
    try:
        executable = provider_executable(home, provider) if provider != "sync" else None
        # Never print success diagnostics onto the provider's JSON/stdout protocol.
        changes = apply(home, home / ".agents/mcp.json", problems)
        if problems and PROVIDERS[provider] in problems:
            raise problems.pop(PROVIDERS[provider])
    except STARTUP_ERRORS as error:
        detail = str(error) if isinstance(error, SyncError) else type(error).__name__
        print(f"Common provider startup refused: {detail}. Run ~/.agents/sync.py check locally.", file=sys.stderr)
        return 2
    for other, error in (problems or {}).items():
        detail = str(error) if isinstance(error, SyncError) else type(error).__name__
        print(f"Common provider startup: {other} configuration not refreshed ({detail}).", file=sys.stderr)
    failures = sync_generated_adapters(home)
    if executable is None:
        for change in changes:
            print(f"updated: {change.path}")
        return 2 if failures else 0
    # The provider's own guards must be registered; a stale adapter only warns.
    if {f"hooks:{PROVIDERS[provider]}", "hook registrations", "lock"} & failures.keys():
        print(f"Common provider startup refused: {provider} hook registration failed. "
              "Run ~/.agents/hooks/register.py check locally.", file=sys.stderr)
        return 2
    # Replace this process: the provider owns stdin/stdout/stderr, signals and the exit code.
    os.execv(str(executable), [str(executable), *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
