"""Render Grok's native agent definitions (~/.grok/agents) from shared role briefs and metadata.

Grok also reads ~/.claude/agents, but a definition in ~/.grok/agents with the same name wins, so
each role is defined natively. No `tools` list is written: like the Codex adapters, access follows
the brief's own contract instead of a mapping of Claude tool names.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
from pathlib import Path
import sys

ROLES_DIR = Path(__file__).absolute().parent
STATE_FILE = "grok-adapter-state.json"


@functools.cache
def load_claude_renderer():
    # The catalog validation and adapter writer live in the Claude renderer; reuse them.
    spec = importlib.util.spec_from_file_location("shared_role_renderer", ROLES_DIR / "sync_claude.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_adapter(role: dict, roles_dir: Path) -> bytes:
    metadata = {"name": role["name"], "description": role["description"]}
    # JSON strings are valid YAML scalars, so no YAML library is needed to write the header.
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    body = load_claude_renderer().adapter_instructions(roles_dir, role, "sync_grok.py")
    return f"---\n{header}\n---\n\n{body}".encode("utf-8")


def adapter_path(role: dict, agents_dir: Path) -> Path:
    return agents_dir / role["brief"]


def synchronize(roles_dir: Path, agents_dir: Path, *, write: bool = False) -> int:
    shared = load_claude_renderer()
    outputs = {adapter_path(role, agents_dir): render_adapter(role, roles_dir) for role in shared.load_catalog(roles_dir)}
    return shared.write_adapters(outputs, roles_dir / STATE_FILE, "Grok", write=write)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=Path.home() / ".grok/agents")
    parser.add_argument("--write", action="store_true", help="Regenerate recognized adapters; otherwise check only")
    args = parser.parse_args()
    try:
        return synchronize(ROLES_DIR, args.agents_dir, write=args.write)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Role adapter error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
