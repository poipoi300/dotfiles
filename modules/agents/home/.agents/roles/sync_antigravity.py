"""Render Antigravity's custom agents (~/.gemini/config/agents) from shared role briefs and metadata.

Antigravity gives an agent without a `tools` list only a small read-only set, so each role's
Claude tool grants are mapped to Antigravity's native tool names, the mapping Antigravity's own
Claude importer uses. Every adapter stays a subagent, never a main agent.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
from pathlib import Path
import sys

ROLES_DIR = Path(__file__).absolute().parent
STATE_FILE = "antigravity-adapter-state.json"
# Built-in agents a custom agent must not shadow.
BUILT_IN = {"self", "browser", "research", "deepagent", "review-agent"}
NATIVE_TOOLS = {
    "Read": ["read_file"], "Glob": ["read_file"], "Grep": ["read_file"],
    "Edit": ["edit_file"], "MultiEdit": ["edit_file"], "Write": ["write_file"],
    "Bash": ["run_command", "manage_task"], "WebSearch": ["search_web"], "WebFetch": ["search_web"],
}


@functools.cache
def load_claude_renderer():
    # The catalog validation and adapter writer live in the Claude renderer; reuse them.
    spec = importlib.util.spec_from_file_location("shared_role_renderer", ROLES_DIR / "sync_claude.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_tools(role: dict) -> list[str]:
    grants = role["claude"]["tools"]
    if isinstance(grants, str):
        grants = [grant.strip() for grant in grants.split(",")]
    tools = []
    for grant in grants:
        if grant not in NATIVE_TOOLS:
            raise ValueError(f"No Antigravity tool for {role['name']}'s {grant} grant")
        tools.extend(tool for tool in NATIVE_TOOLS[grant] if tool not in tools)
    return tools


def render_adapter(role: dict, roles_dir: Path) -> bytes:
    if role["name"].casefold() in BUILT_IN:
        raise ValueError(f"Role {role['name']} would shadow a built-in Antigravity agent")
    metadata = {"name": role["name"], "description": role["description"], "subagent": True,
                "mainAgent": False, "tools": native_tools(role)}
    # JSON values are valid YAML, so no YAML library is needed to write the header.
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    body = load_claude_renderer().adapter_instructions(roles_dir, role, "sync_antigravity.py")
    # Antigravity takes the system prompt from the H1 section of a Markdown agent.
    return f"---\n{header}\n---\n\n# {role['name']}\n\n{body}".encode("utf-8")


def adapter_path(role: dict, agents_dir: Path) -> Path:
    return agents_dir / role["brief"]


def synchronize(roles_dir: Path, agents_dir: Path, *, write: bool = False) -> int:
    shared = load_claude_renderer()
    outputs = {adapter_path(role, agents_dir): render_adapter(role, roles_dir) for role in shared.load_catalog(roles_dir)}
    return shared.write_adapters(outputs, roles_dir / STATE_FILE, "Antigravity", write=write)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=Path.home() / ".gemini/config/agents")
    parser.add_argument("--write", action="store_true", help="Regenerate recognized adapters; otherwise check only")
    args = parser.parse_args()
    try:
        return synchronize(ROLES_DIR, args.agents_dir, write=args.write)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Role adapter error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
