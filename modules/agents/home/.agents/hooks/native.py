"""Thin Claude/Codex/Grok/Antigravity lifecycle adapter; stdout is reserved for protocol JSON.

Grok sends Claude's snake_case fields beside its own, with its own tool names. Antigravity sends
camelCase `toolCall` payloads and reads `decision`/`injectSteps` replies; it denies a tool call
when a PreToolUse hook prints `{}` or exits non-zero, and runs it normally when the hook prints
nothing, so the Antigravity adapter prints nothing unless it has a decision or context.
"""

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
import model_guard
import vcs_guard

try:
    import budget  # usage/budget.py ships in the private module; the hooks work without it
except ImportError:
    budget = None


# Native tool names under the Claude names the shared guards check.
GROK_TOOLS = {"run_terminal_command": "Bash", "spawn_subagent": "Task",
              "t3-code__delegate_task": "mcp__t3-code__delegate_task"}
ANTIGRAVITY_TOOLS = {"run_command": "Bash", "mcp_t3-code_delegate_task": "mcp__t3-code__delegate_task"}
GROK_TRUNCATED = ("This shell command is too large for the hook to read whole, so it was not run. "
                  "Write long content to a file with the edit tools first, then run a shorter command.")


def shell_directory(session: object, requested: object) -> object:
    """Where a shell call runs: its own directory argument, else the session's.

    Both CLIs report the session directory in the event and keep a shell call's own directory
    in its arguments; the route guard must judge the repository the command runs in."""
    if not isinstance(requested, str) or not requested.strip():
        return session
    path = requested[len("file://"):] if requested.startswith("file://") else requested
    path = os.path.expanduser(path)
    if not os.path.isabs(path) and isinstance(session, str) and session:
        path = os.path.join(session, path)
    return os.path.normpath(path)


def canonical(provider: str, payload: dict) -> dict:
    """The payload in the Claude shape the shared guards read."""
    if provider == "grok":
        tool = GROK_TOOLS.get(payload.get("tool_name"), payload.get("tool_name"))
        shaped = {**payload, "tool_name": tool}
        if tool == "Bash" and isinstance(payload.get("tool_input"), dict):
            shaped["cwd"] = shell_directory(payload.get("cwd"), payload["tool_input"].get("cwd"))
        return shaped
    if provider != "antigravity":
        return payload
    call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    arguments = call.get("args") if isinstance(call.get("args"), dict) else {}
    tool = ANTIGRAVITY_TOOLS.get(call.get("name"), call.get("name"))
    result = {"hook_event_name": payload.get("hookEventName"), "cwd": payload.get("cwd"),
              "session_id": payload.get("conversationId")}
    if tool == "Bash":
        result.update(tool_name=tool, tool_input={"command": arguments.get("CommandLine")},
                      cwd=shell_directory(payload.get("cwd"), arguments.get("Cwd")))
    elif tool is not None:
        result.update(tool_name=tool, tool_input=arguments)
    return result


def from_grok(payload: dict) -> bool:
    """Grok also runs the Claude registrations in ~/.claude/settings.json (its compatibility scan,
    kept on for project hooks). Grok has its own registration, so those runs do nothing."""
    return bool(os.environ.get("GROK_HOOK_EVENT")) or ("hookEventName" in payload and "hook_event_name" in payload)


def dispatch_grok(payload: dict) -> dict:
    """Route and model guards, RTK for the shell, and the route reminder after a PR opens."""
    shaped = canonical("grok", payload)
    event = shaped.get("hook_event_name")
    if event == "PreToolUse":
        # Model names are short and Grok never cuts a string under 4 KiB, so this guard sees them.
        denied = model_guard.guard(shaped)
        if denied:
            return denied
        if shaped.get("tool_name") == "Bash" and payload.get("toolInputTruncated") is True:
            # Grok cuts the long strings of a large input but runs the whole command, so the
            # route guard cannot see the end of it.
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": GROK_TRUNCATED}}
        try:
            denied = vcs_guard.guard(shaped)
        except Exception:
            denied = None
        if denied:
            return denied
        # Rewrite only an input Grok reports as whole.
        return behavior.rewrite_shell(shaped) if payload.get("toolInputTruncated") is False else {}
    if event == "PostToolUse" and shaped.get("tool_name") == "Bash":
        return vcs_guard.remind(shaped) or {}
    return {}


def dispatch_antigravity(payload: dict) -> dict:
    """Route and model guards, and usage context at session start, in Antigravity's reply format."""
    shaped = canonical("antigravity", payload)
    event = shaped.get("hook_event_name")
    if event == "PreToolUse":
        denied = model_guard.guard(shaped)
        if not denied:
            try:
                denied = vcs_guard.guard(shaped)
            except Exception:
                denied = None
        if denied:
            reason = denied["hookSpecificOutput"].get("permissionDecisionReason", "Denied by ~/.agents hooks.")
            return {"decision": "deny", "reason": reason}
        return {}
    if event == "SessionStart" and budget is not None and not os.environ.get("WPO_SESSION"):
        try:
            context = budget.startup(payload.get("cwd") or str(Path.cwd()))
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            context = ("Usage is UNKNOWN; native snapshot unavailable. Manual refresh: "
                       "python3 -I -B ~/.agents/usage/budget.py --refresh")
        if context:
            return {"injectSteps": [{"systemMessage": {"systemMessage": context}}]}
    return {}


def dispatch(provider: str, payload: dict) -> dict:
    if provider == "grok":
        return dispatch_grok(payload)
    if provider == "antigravity":
        return dispatch_antigravity(payload)
    if from_grok(payload):
        return {}
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        denied = model_guard.guard(payload)
        if denied:
            return denied
        # The route guard runs for both providers. A deny wins; Codex still rewrites what it allows.
        try:
            denied = vcs_guard.guard(payload)
        except Exception:
            denied = None
        if denied:
            return denied
        return behavior.rewrite_shell(payload) if provider == "codex" else {}
    if event not in ("SessionStart", "PostToolUse", "Stop"):
        return {}
    if event == "PostToolUse" and payload.get("tool_name") == "Bash":
        # Bash is registered only for the route reminder; design feedback is for file edits.
        return vcs_guard.remind(payload) or {}
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
    parser.add_argument("provider", choices=("claude", "codex", "grok", "antigravity"))
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise TypeError("Hook payload must be an object")
        output = dispatch(arguments.provider, payload)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.TimeoutExpired):
        # Never echo payloads, provider logs, exception strings, or credentials.
        output = {"systemMessage": "Shared native hook unavailable; usage is unknown and automatic design feedback may be missing."}
        if arguments.provider == "antigravity":
            output = {}  # a reply without a decision would deny the tool call
    if arguments.provider == "antigravity" and not output:
        return  # no reply: Antigravity's own permission flow decides
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
