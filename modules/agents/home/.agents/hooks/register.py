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


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


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


def synchronize(home: Path, apply: bool = False) -> list[str]:
    root = home / ".agents" / "hooks"
    state_path = root / "state" / "registrations.json"
    state = read_json(state_path)
    updates = []
    new_state = {}
    for provider, path in (("claude", home / ".claude/settings.json"), ("codex", home / ".codex/hooks.json")):
        original = path.read_bytes() if path.exists() else None
        config = json.loads(original) if original is not None else {}
        if not isinstance(config, dict):
            raise TypeError("Native settings must be an object")
        current_digest = digest(owned_view(config, home, provider))
        if provider in state and state[provider] != current_digest:
            raise ValueError(f"Owned {provider} hook registration drift; reconcile before regenerating")
        desired = generated(config, home, provider)
        new_state[provider] = digest(owned_view(desired, home, provider))
        if desired != config:
            updates.append((provider, path, original, desired))
    if not apply:
        return [provider for provider, *_ in updates]
    if updates:
        backup = root / "backups" / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        backup.mkdir(parents=True)
        manifest = {}
        for provider, path, original, _ in updates:
            saved = backup / f"{provider}.json"
            if original is not None:
                saved.write_bytes(original)
            manifest[str(path)] = str(saved) if original is not None else None
        atomic_json(backup / "manifest.json", manifest)
        completed = []
        try:
            for provider, path, original, desired in updates:
                # Native settings can be edited while this script runs.
                if (path.read_bytes() if path.exists() else None) != original:
                    raise ValueError(f"Concurrent native {provider} settings edit; registration refused")
                atomic_json(path, desired)
                completed.append((path, original))
        except Exception:
            for path, original in reversed(completed):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise
    atomic_json(state_path, new_state)
    return [provider for provider, *_ in updates]


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
