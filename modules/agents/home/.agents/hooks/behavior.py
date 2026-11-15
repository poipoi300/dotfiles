"""Shared native hook behavior. Provider payloads stay at the boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


def rewrite_shell(payload: dict) -> dict:
    if payload.get("tool_name") != "Bash":
        return {}
    original = payload.get("tool_input")
    if not isinstance(original, dict) or not isinstance(original.get("command"), str):
        return {}
    command = original["command"]
    # Native RTK owns command parsing; the adapter only avoids a second pass
    # for commands that already carry the shared instruction's prefix.
    if re.match(r"^\s*rtk(?:\s|$)", command):
        return {}
    try:
        result = subprocess.run(
            ["rtk", "rewrite", command], check=False, capture_output=True, text=True, encoding="utf-8", timeout=3,
        )
    except OSError:
        # rtk is not installed: leave the command alone instead of reporting a hook failure on every call.
        return {}
    rewritten = result.stdout.strip()
    # RTK returns 3 for a handled rewrite with its default permission verdict.
    # Codex still evaluates the rewritten shell command's permissions.
    if result.returncode not in (0, 3) or not rewritten or rewritten == command:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**original, "command": rewritten}}}


def matching_project_design_hook(provider: str, payload: dict, home: Path | None = None) -> bool:
    """A project-owned Impeccable hook takes precedence over the user adapter."""
    home = Path.home() if home is None else home
    cwd = Path(payload.get("cwd") or Path.cwd()).resolve()
    for directory in (cwd, *cwd.parents):
        if directory == home.resolve():
            continue
        names = (".claude/settings.json", ".claude/settings.local.json") if provider == "claude" else (
            ".codex/hooks.json", ".codex/config.toml")
        for name in names:
            path = directory / name
            try:
                if path.suffix == ".toml":
                    import tomllib
                    config = tomllib.loads(path.read_text(encoding="utf-8"))
                else:
                    config = json.loads(path.read_text(encoding="utf-8"))
                groups = config.get("hooks", {}).get(payload.get("hook_event_name"), [])
                for group in groups:
                    matcher = group.get("matcher", "")
                    tool_name = payload.get("tool_name", "")
                    aliases = [tool_name, "Edit", "Write"] if tool_name == "apply_patch" else [tool_name]
                    if payload.get("hook_event_name") == "PostToolUse" and matcher and not any(
                        re.search(matcher, alias) for alias in aliases
                    ):
                        continue
                    if any("impeccable" in handler.get("command", "").lower() and
                           re.search(r"\bhook\b", handler.get("command", ""))
                           for handler in group.get("hooks", [])):
                        return True
            except (OSError, ValueError, TypeError, AttributeError, re.error):
                continue
    return False


def impeccable_engine(skill: Path) -> Path | None:
    """The engine binary: $IMPECCABLE_BIN, the one shipped beside the skill, then `impeccable` on PATH.

    The engine package installs /usr/bin/impeccable; this deliberately never downloads, because
    hooks run on every edit and must stay fast and offline."""
    override = os.environ.get("IMPECCABLE_BIN")
    if override and os.access(override, os.X_OK):
        return Path(override)
    import platform
    architecture = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    sibling = skill / "scripts" / "bin" / f"linux-{architecture}" / "impeccable"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return sibling
    found = shutil.which("impeccable")
    return Path(found) if found else None


def design_hook(provider: str, payload: dict, root: Path) -> dict:
    if matching_project_design_hook(provider, payload):
        return {}
    skill = root / "skills" / "impeccable"
    binary = impeccable_engine(skill)
    if binary is None:
        return {"systemMessage": "Impeccable native engine unavailable; use the skill's manual detector fallback."}
    env = {**os.environ, "IMPECCABLE_SKILL_DIR": str(skill), "IMPECCABLE_SELF": str(binary)}
    event = payload.get("hook_event_name")
    result = subprocess.run(
        [str(binary), "hook"], check=False, input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=payload.get("cwd") or None, env=env,
        timeout=25 if event == "Stop" else 4,
    )
    if result.returncode != 0:
        return {"systemMessage": "Impeccable native hook failed; use the skill's manual detector fallback."}
    output = json.loads(result.stdout) if result.stdout.strip() else {}
    return output if isinstance(output, dict) else {}


def record_marker(provider: str, payload: dict, state: Path) -> None:
    """Local Delta-style boundaries only; never persist prompts, tool input, or replies."""
    event = payload.get("hook_event_name")
    if event not in ("SessionStart", "PostToolUse", "Stop"):
        return
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return
    key = hashlib.sha256(f"{provider}:{session}".encode()).hexdigest()[:24]
    directory = state / "markers" / key
    directory.mkdir(parents=True, exist_ok=True)
    row = {"schema_version": 1, "provider": provider, "event": event, "at": time.time()}
    if event == "SessionStart" and payload.get("source") in ("startup", "resume", "clear", "compact", "fork"):
        row["source"] = payload["source"]
    if event == "Stop":
        row["continued"] = payload.get("stop_hook_active") is True
    # UUID filenames avoid contention between simultaneous tools or providers.
    path = directory / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(row), encoding="utf-8")
