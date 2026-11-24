"""Thin Claude/Codex/Grok/Antigravity lifecycle adapter; stdout is reserved for protocol JSON.

Grok sends Claude's snake_case fields beside its own, with its own tool names. Antigravity sends
camelCase `toolCall` payloads and reads `decision`/`injectSteps` replies; it denies a tool call
when a PreToolUse hook prints `{}` or exits non-zero, and runs it normally when the hook prints
nothing, so the Antigravity adapter prints nothing unless it has a decision or context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
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
GROK_TOOLS = {
    "run_terminal_command": "Bash",
    "spawn_subagent": "Task",
    "t3-code__delegate_task": "mcp__t3-code__delegate_task",
    "t3-code__t3_thread_launch": "mcp__t3-code__t3_thread_launch",
    "t3-code__create_threads": "mcp__t3-code__create_threads",
}
ANTIGRAVITY_TOOLS = {
    "run_command": "Bash",
    "invoke_subagent": "invoke_subagent",
    "mcp_t3-code_delegate_task": "mcp__t3-code__delegate_task",
    "mcp_t3-code_t3_thread_launch": "mcp__t3-code__t3_thread_launch",
    "mcp_t3-code_create_threads": "mcp__t3-code__create_threads",
}
GROK_TRUNCATED = ("This shell command is too large for the hook to read whole, so it was not run. "
                  "Write long content to a file with the edit tools first, then run a shorter command.")
SESSION_BUDGET_REFRESH_SECONDS = 20 * 60
SESSION_BUDGET_RETENTION_SECONDS = 7 * 86400
SESSION_BUDGET_LOCK_STALE_SECONDS = 30


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
              "session_id": payload.get("conversationId"), "model_name": payload.get("modelName"),
              "transcript_path": payload.get("transcriptPath"),
              "last_user_input": payload.get("lastUserInput")}
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


GROK_USAGE_RETENTION_SECONDS = 7 * 86400
GROK_USAGE_LOCK_STALE_SECONDS = 30


def _grok_session_usage(payload: dict) -> str | None:
    """Inject the usage snapshot once per main Grok session on its first allowed PreToolUse.

    Grok discards additionalContext on SessionStart and UserPromptSubmit, and drops it on a
    PreToolUse deny; callers must invoke this only after every deny guard has passed.
    """
    if budget is None or os.environ.get("WPO_SESSION") or payload.get("subagentType"):
        return None
    session = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(session, str) or not session:
        return None
    directory = ROOT / "hooks" / "state" / "grok-usage"
    key = hashlib.sha256(f"grok:{session}".encode()).hexdigest()[:24]
    marker = directory / f"{key}.json"
    if marker.exists():
        return None
    lock = directory / f"{key}.lock"
    descriptor = None
    for attempt in range(2):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                if attempt == 0 and time.time() - lock.stat().st_mtime > GROK_USAGE_LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError:
            return None
    if descriptor is None:
        return None
    try:
        os.close(descriptor)
        if marker.exists():
            return None
        try:
            context = budget.startup(payload.get("cwd") or str(Path.cwd()))
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            context = ("Usage is UNKNOWN; native snapshot unavailable. Manual refresh: "
                       "python3 -I -B ~/.agents/usage/budget.py --refresh")
        # Write the session marker only after startup() finishes so a hook killed by timeout
        # retries on a later tool call instead of losing the snapshot for the whole session.
        try:
            now = time.time()
            marker_fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(marker_fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": now}))
            cutoff = now - GROK_USAGE_RETENTION_SECONDS
            for entry in directory.iterdir():
                if entry not in (marker, lock) and entry.suffix in (".json", ".lock"):
                    try:
                        if entry.stat().st_mtime < cutoff:
                            entry.unlink(missing_ok=True)
                    except OSError:
                        continue
        except OSError:
            pass
        return context
    finally:
        lock.unlink(missing_ok=True)


def _session_budget_marker(provider: str, session: str) -> Path:
    directory = ROOT / "hooks" / "state" / "session-budget"
    key = hashlib.sha256(f"{provider}:{session}".encode()).hexdigest()[:24]
    return directory / f"{key}.json"


def _record_session_budget_seen(provider: str, payload: dict, now: float | None = None) -> None:
    session = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(session, str) or not session:
        return
    stamp = time.time() if now is None else now
    marker = _session_budget_marker(provider, session)
    temporary = None
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=marker.parent, prefix=marker.name, suffix=".tmp")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": stamp}))
        os.chmod(temporary, 0o600)
        os.replace(temporary, marker)
        temporary = None
        cutoff = stamp - SESSION_BUDGET_RETENTION_SECONDS
        for entry in marker.parent.iterdir():
            if entry != marker and entry.suffix in (".json", ".lock"):
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
    except OSError:
        pass
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _session_budget_is_fresh(marker: Path, now: float) -> bool:
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8")).get("at") if marker.is_file() else None
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(recorded, (int, float)) and not isinstance(recorded, bool) and 0 <= now - recorded < SESSION_BUDGET_REFRESH_SECONDS


def _mid_session_budget(provider: str, payload: dict, *, require_delegation: bool = True) -> str | None:
    """Re-inject a refreshed budget/routing snapshot when the session's copy is stale."""
    if budget is None or os.environ.get("WPO_SESSION") or payload.get("subagentType"):
        return None
    if require_delegation and payload.get("tool_name") not in model_guard.DELEGATION_TOOLS:
        return None
    session = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(session, str) or not session:
        return None
    now = time.time()
    marker = _session_budget_marker(provider, session)
    if _session_budget_is_fresh(marker, now):
        return None
    lock = marker.with_suffix(".lock")
    descriptor = None
    for attempt in range(2):
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                if attempt == 0 and time.time() - lock.stat().st_mtime > SESSION_BUDGET_LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError:
            return None
    if descriptor is None:
        return None
    try:
        os.close(descriptor)
        if _session_budget_is_fresh(marker, time.time()):
            return None
        try:
            context = budget.startup(payload.get("cwd") or str(Path.cwd()))
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            return None
        _record_session_budget_seen(provider, payload, time.time())
        return context
    finally:
        lock.unlink(missing_ok=True)


def dispatch_grok(payload: dict) -> dict:
    """Route and model guards, RTK for the shell, one-shot usage context, and post-PR reminder."""
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
        result = behavior.rewrite_shell(shaped) if payload.get("toolInputTruncated") is False else {}
        context = _grok_session_usage(payload)
        if context:
            _record_session_budget_seen("grok", payload)
        else:
            context = _mid_session_budget("grok", shaped)
        if context:
            specific = result.setdefault("hookSpecificOutput", {"hookEventName": "PreToolUse"})
            specific["additionalContext"] = "\n".join(filter(None, [specific.get("additionalContext"), context]))
        return result
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
            _record_session_budget_seen("antigravity", shaped)
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            context = ("Usage is UNKNOWN; native snapshot unavailable. Manual refresh: "
                       "python3 -I -B ~/.agents/usage/budget.py --refresh")
        if context:
            return {"injectSteps": [{"systemMessage": {"systemMessage": context}}]}
    if event == "PreInvocation":
        context = _mid_session_budget("antigravity", shaped, require_delegation=False)
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
        result = behavior.rewrite_shell(payload) if provider == "codex" else {}
        context = _mid_session_budget(provider, payload)
        if context:
            specific = result.setdefault("hookSpecificOutput", {"hookEventName": "PreToolUse"})
            specific["additionalContext"] = "\n".join(filter(None, [specific.get("additionalContext"), context]))
        return result
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
            _record_session_budget_seen(provider, payload)
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
