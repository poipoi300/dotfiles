"""Generate native hook registrations while preserving unrelated provider settings."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_guard
from sync import provider_enabled

# Antigravity's hooks.json is shared by named hooks; this is the one these scripts own.
ANTIGRAVITY_HOOK = "agents-common"
GROK_SHELL = "^run_terminal_command$"
GROK_DELEGATE = "^(spawn_subagent|t3-code__delegate_task)$"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def json_text(value: dict) -> bytes:
    return json.dumps(value, allow_nan=False).encode("utf-8")


def atomic_bytes(path: Path, content: bytes) -> None:
    # Through a symlink to its target, so a native file managed elsewhere stays a link.
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, json_text(value))


def python_command(home: Path, script: str, argument: str) -> str:
    """`python3 -I -B "$HOME/.agents/<script>" <argument>`; the shell expands $HOME.

    A home other than the invoking user's (tests, a staged tree) gets its absolute path."""
    if home.absolute() == Path.home().absolute():
        path = f"$HOME/.agents/{script}"
    else:
        path = (home / ".agents" / script).as_posix()
        if any(character in path for character in ('"', '$', '`', '%', '!', '\n', '\r')):
            raise ValueError("Hook path contains shell metacharacters")
    return f'python3 -I -B "{path}" {argument}'


def handlers(home: Path, provider: str) -> dict:
    command = python_command(home, "hooks/native.py", provider)
    if provider == "grok":
        # Grok's own tool names; it fails open when a hook cannot run.
        handler = {"type": "command", "command": command, "timeout": 5}
        return {"PreToolUse": [{"matcher": GROK_SHELL, "hooks": [handler]},
                               {"matcher": GROK_DELEGATE, "hooks": [dict(handler)]}],
                "PostToolUse": [{"matcher": GROK_SHELL, "hooks": [dict(handler)]}]}
    if provider == "antigravity":
        # Antigravity denies the tool call when a hook exits non-zero (a missing script while arcus
        # relinks, say); `|| true` keeps that a pass-through. Exact tool names, one group each.
        handler = {"type": "command", "command": f"{command} || true", "timeout": 10}
        return {"PreToolUse": [{"matcher": "run_command", "hooks": [handler]},
                               {"matcher": "mcp_t3-code_delegate_task", "hooks": [dict(handler)]}],
                "SessionStart": [{**handler, "timeout": 20}]}
    result = {}
    for event, matcher, timeout in (
        ("SessionStart", None, 20),
        ("PostToolUse", "^(Edit|Write|apply_patch)$", 5),
        ("PostToolUse", "^Bash$", 5),
        ("Stop", None, 30),
    ):
        group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            group["matcher"] = matcher
        result.setdefault(event, []).append(group)
    # Both providers: the route guard inspects Bash before Codex's RTK rewrite.
    result["PreToolUse"] = [{"matcher": "^Bash$", "hooks": [
        {"type": "command", "command": command, "timeout": 5},
    ]}, {"matcher": model_guard.MATCHER, "hooks": [
        {"type": "command", "command": command, "timeout": 5},
    ]}]
    return result


def is_owned(handler: dict, home: Path) -> bool:
    command = handler.get("command", "")
    return str(home / ".agents" / "hooks" / "native.py") in command or "$HOME/.agents/hooks/native.py" in command


def legacy_handler(handler: dict, home: Path) -> bool:
    command = handler.get("command", "")
    # The retired standalone SessionStart usage hook, with whichever Python the old setup used.
    if re.fullmatch(r'\S*python\S* -I \S*/\.claude/hooks/usage_budget\.py --hook', command):
        return True
    match = re.fullmatch(r'\[ ! -f ("[^"]+") \] \|\| \1 hook', command)
    if not match:
        return False
    return match[1].strip('"') == (home / ".claude/skills/impeccable/scripts/impeccable").as_posix()


def owned_view(config: dict, home: Path, provider: str) -> dict:
    if provider == "grok":
        return config  # ~/.grok/hooks/agents.json holds nothing else
    if provider == "antigravity":
        return {ANTIGRAVITY_HOOK: config[ANTIGRAVITY_HOOK]} if ANTIGRAVITY_HOOK in config else {}
    result = {}
    for event, groups in config.get("hooks", {}).items():
        selected = []
        for group in groups:
            owned = [handler for handler in group.get("hooks", []) if is_owned(handler, home)]
            if owned:
                selected.append({**group, "hooks": owned})
        if selected:
            result[event] = selected
    if provider == "claude":
        status = config.get("statusLine", {})
        if "/.agents/usage/budget.py" in status.get("command", ""):
            result["statusLine"] = status
    return result


def generated(config: dict, home: Path, provider: str) -> dict:
    if provider == "grok":
        return {"hooks": handlers(home, provider)}
    if provider == "antigravity":
        return {**config, ANTIGRAVITY_HOOK: handlers(home, provider)}
    result = copy.deepcopy(config)
    hooks = result.setdefault("hooks", {})
    for event, groups in list(hooks.items()):
        remaining = []
        for group in groups:
            group["hooks"] = [handler for handler in group.get("hooks", [])
                              if not is_owned(handler, home) and not (
                                  provider == "claude" and legacy_handler(handler, home))]
            if group["hooks"]:
                remaining.append(group)
        if remaining:
            hooks[event] = remaining
        else:
            del hooks[event]
    for event, groups in handlers(home, provider).items():
        hooks.setdefault(event, []).extend(groups)
    if provider == "claude":
        current = result.get("statusLine")
        if current and "/.agents/usage/budget.py" not in current.get("command", ""):
            raise ValueError("Existing unrelated Claude statusLine must be coordinated before registration")
        # usage/budget.py ships in the private module; without it the statusLine is left alone.
        if (home / ".agents/usage/budget.py").is_file():
            result["statusLine"] = {"type": "command",
                                    "command": python_command(home, "usage/budget.py", "--claude-statusline")}
    return result


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def native_files(home: Path) -> list[tuple[str, Path]]:
    files = [("claude", home / ".claude/settings.json"), ("codex", home / ".codex/hooks.json")]
    if provider_enabled(home, "antigravity"):
        files.append(("antigravity", home / ".gemini/config/hooks.json"))
    if provider_enabled(home, "grok"):
        files.append(("grok", home / ".grok/hooks/agents.json"))
    return files


def restore(written: list[tuple[str, Path, bytes | None, bytes]]) -> None:
    """Undo writes that still hold this run's bytes; one destination failing does not stop the rest."""
    for _, path, original, content in reversed(written):
        try:
            # A native harness may have saved the file since; never discard that save.
            if (path.read_bytes() if path.exists() else None) != content:
                continue
            if original is None:
                # The file was written through any link at the path; remove what was written.
                Path(os.path.realpath(path)).unlink(missing_ok=True)
            else:
                atomic_bytes(path, original)
        except OSError:
            continue


def synchronize(home: Path, apply: bool = False, problems: dict | None = None) -> list[str]:
    """Regenerate owned registrations. With `problems`, a provider whose file refuses (drift, a
    foreign entry, an unwritable destination) is recorded there and skipped while the others
    proceed; otherwise any refusal raises and nothing changes."""
    root = home / ".agents" / "hooks"
    state_path = root / "state" / "registrations.json"
    state = read_json(state_path)
    updates = []
    # A provider that is absent or refused keeps its record for the next run.
    new_state = dict(state)

    def refuse(provider: str, error: Exception) -> None:
        problems[provider] = error
        if provider in state:
            new_state[provider] = state[provider]
        else:
            new_state.pop(provider, None)

    for provider, path in native_files(home):
        try:
            original = path.read_bytes() if path.exists() else None
            config = json.loads(original) if original is not None and original.strip() else {}
            if not isinstance(config, dict):
                raise TypeError("Native settings must be an object")
            current = owned_view(config, home, provider)
            if provider in state and state[provider] != digest(current):
                raise ValueError(f"Owned {provider} hook registration drift; reconcile before regenerating")
            desired = generated(config, home, provider)
            if (provider in ("grok", "antigravity") and provider not in state and current
                    and current != owned_view(desired, home, provider)):
                # Claude and Codex handlers are recognized by their command; these files are not.
                raise ValueError(f"Existing {provider} hook entry was not written by this script; reconcile it first")
            # Serialized now, so a value JSON cannot write (NaN in a foreign entry) refuses here.
            content = json_text(desired) if desired != config else None
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as error:
            # Malformed or unreadable files stay inside their provider too (`{"hooks": []}`).
            if problems is None:
                raise
            refuse(provider, error)
            continue
        new_state[provider] = digest(owned_view(desired, home, provider))
        if content is not None:
            updates.append((provider, path, original, content))
    if not apply:
        return [provider for provider, *_ in updates]
    if not updates and new_state == state:
        return []
    if updates:
        backup = root / "backups"
        backup.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup.chmod(0o700)
        backup = backup / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        backup.mkdir(mode=0o700)
        backup.chmod(0o700)  # mkdir's mode is masked by the umask, which can drop even the owner's x
        manifest = {}
        for provider, path, original, _ in updates:
            saved = backup / f"{provider}.json"
            if original is not None:
                # Native settings can hold credentials; the umask must not widen the copy.
                descriptor = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(original)
            manifest[str(path)] = str(saved) if original is not None else None
        atomic_json(backup / "manifest.json", manifest)
    written = []
    for update in updates:
        provider, path, original, content = update
        try:
            # Native settings can be edited while this script runs.
            if (path.read_bytes() if path.exists() else None) != original:
                raise ValueError(f"Concurrent native {provider} settings edit; registration refused")
            atomic_bytes(path, content)
            written.append(update)
        except (OSError, ValueError) as error:
            if problems is None:
                restore(written)
                raise
            refuse(provider, error)  # a file at ~/.grok/hooks fails only Grok
    try:
        # In the same transaction: new files with old digests would read as drift next time.
        atomic_json(state_path, new_state)
    except Exception:
        restore(written)
        raise
    return [provider for provider, *_ in written]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "sync"))
    arguments = parser.parse_args()
    try:
        changed = synchronize(Path.home(), apply=arguments.action == "sync")
    except (OSError, ValueError, TypeError, AttributeError):
        print("Hook registration refused: invalid configuration, collision, drift, or concurrent edit.", file=sys.stderr)
        raise SystemExit(2)
    print("Hooks " + ("updated: " if arguments.action == "sync" and changed else "need sync: " if changed else "current") + ", ".join(changed))
    if arguments.action == "check" and changed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
