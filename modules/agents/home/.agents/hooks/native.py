"""Thin Claude/Codex lifecycle adapter; stdout is reserved for protocol JSON."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# abspath, not resolve(): arcus links the files into $HOME, and ROOT must be ~/.agents
# (hooks/state and skills/ live there), not the dotfiles repository the links point into.
ROOT = Path(os.path.abspath(__file__)).parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "usage"))

import behavior

try:
    import budget  # usage/budget.py ships in the private module; the hooks work without it
except ImportError:
    budget = None


def dispatch(provider: str, payload: dict) -> dict:
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        return behavior.rewrite_shell(payload) if provider == "codex" else {}
    if event not in ("SessionStart", "PostToolUse", "Stop"):
        return {}
    # Recording failure must not suppress design feedback or startup context.
    try:
        behavior.record_marker(provider, payload, ROOT / "hooks" / "state")
    except OSError:
        pass
    try:
        output = behavior.design_hook(provider, payload, ROOT)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        output = {"systemMessage": "Impeccable native hook unavailable; use the skill's manual detector fallback."}
    if event == "SessionStart" and budget is not None and not os.environ.get("WPO_SESSION"):
        try:
            context = budget.startup(payload.get("cwd") or str(Path.cwd()))
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            context = ("Usage is UNKNOWN; native snapshot unavailable. Manual refresh: "
                       "python3 -I -B ~/.agents/usage/budget.py --refresh")
        specific = output.setdefault("hookSpecificOutput", {"hookEventName": event})
        specific["additionalContext"] = "\n".join(filter(None, [specific.get("additionalContext"), context]))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("claude", "codex"))
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise TypeError("Hook payload must be an object")
        output = dispatch(arguments.provider, payload)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        # Never echo payloads, provider logs, exception strings, or credentials.
        output = {"systemMessage": "Shared native hook unavailable; usage is unknown and automatic design feedback may be missing."}
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
