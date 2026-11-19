"""Deny subagent and delegate launches that name a Fable model."""

from __future__ import annotations

REASON = "Fable is too expensive for the task. Disallowed as subagent or delegate."
# Claude Agent/Task, Codex spawn_agent, and T3 delegate_task under either provider's MCP prefix.
MATCHER = r"^(Agent|Task|spawn_agent|mcp__t3[-_]code__delegate_task)$"
TOOLS = {"Agent", "Task", "spawn_agent", "mcp__t3-code__delegate_task", "mcp__t3_code__delegate_task"}


def _names_fable(value) -> bool:
    if isinstance(value, dict):
        return any((key.lower().startswith("model") and isinstance(item, str) and "fable" in item.lower())
                   or _names_fable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_names_fable(item) for item in value)
    return False


def guard(payload: dict) -> dict | None:
    if payload.get("tool_name") not in TOOLS or not _names_fable(payload.get("tool_input")):
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": REASON,
    }}
