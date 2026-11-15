"""Render Claude's native agent adapters from shared role briefs and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_catalog(roles_dir: Path) -> list[dict]:
    catalog = json.loads((roles_dir / "catalog.json").read_text(encoding="utf-8"))
    if catalog.get("schemaVersion") != 1:
        raise ValueError("Unsupported role catalog schema")
    roles = catalog["roles"]
    seen = set()
    brief_names = set()
    for role in roles:
        name = role["name"]
        if not re.fullmatch(r"[A-Za-z0-9-]+", name) or name.casefold() in seen:
            raise ValueError("Invalid or duplicate role name")
        seen.add(name.casefold())
        if role["brief"].casefold() in brief_names:
            raise ValueError("Duplicate role brief filename")
        brief_names.add(role["brief"].casefold())
        brief = roles_dir / role["brief"]
        if brief.parent.resolve() != roles_dir.resolve() or brief.suffix != ".md" or not brief.is_file():
            raise ValueError(f"Missing or invalid brief for {name}")
        if not isinstance(role["description"], str) or not role["description"].strip():
            raise ValueError(f"Missing description for {name}")
        if role["claude"].get("model") != "inherit":
            raise ValueError(f"Role {name} must inherit the selected model")
        metadata = role["claude"]
        if set(metadata) - {"model", "tools", "skills", "effort", "maxTurns"}:
            raise ValueError(f"Unsupported Claude metadata for {name}")
        if "skills" in metadata and (
            not isinstance(metadata["skills"], list)
            or any(not isinstance(skill, str) or not skill.strip() for skill in metadata["skills"])
        ):
            raise ValueError(f"Claude skills must be a list for {name}")
        if "maxTurns" in metadata and (type(metadata["maxTurns"]) is not int or metadata["maxTurns"] < 1):
            raise ValueError(f"Invalid turn limit for {name}")
        tools = metadata.get("tools", [])
        if isinstance(tools, str):
            tools = [tool.strip() for tool in tools.split(",")]
        if not isinstance(tools, list) or "Read" not in tools:
            raise ValueError(f"Role {name} needs Read access to load its brief")
    return roles


def render_adapter(role: dict, roles_dir: Path) -> bytes:
    metadata = {"name": role["name"], "description": role["description"], **role["claude"]}
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    brief = (roles_dir / role["brief"]).absolute().as_posix()
    body = (
        "Generated adapter. Maintain the shared role brief and catalog.json; regenerate with sync_claude.py.\n\n"
        f"Read the shared role brief at `{brief}` before acting. Follow its complete input, access, "
        "execution, and output contracts together with the task and repository instructions. "
        "Use only tools available in this runtime. If the brief cannot be read, report that blocker.\n"
    )
    return f"---\n{header}\n---\n\n{body}".encode("utf-8")


def synchronize(roles_dir: Path, agents_dir: Path, *, write: bool = False) -> int:
    roles = load_catalog(roles_dir)
    outputs = {agents_dir / role["brief"]: render_adapter(role, roles_dir) for role in roles}
    changed = {path: content for path, content in outputs.items() if not path.exists() or path.read_bytes() != content}
    if not changed:
        print(f"All {len(outputs)} Claude role adapters are current.")
        return 0
    if not write:
        print("Claude role adapters need regeneration: " + ", ".join(path.name for path in changed))
        return 1

    state_path = roles_dir / "claude-adapter-state.json"
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    # Digests of pre-migration hand-written agents that may be replaced; absent on a fresh machine.
    baseline_path = roles_dir / "migration-baseline.json"
    legacy = json.loads(baseline_path.read_text(encoding="utf-8")).get("legacyAgents", {}) if baseline_path.exists() else {}
    for path in changed:
        if path.exists() and digest(path.read_bytes()) not in {previous.get(str(path.absolute())), legacy.get(path.name)}:
            raise ValueError(f"Refusing to overwrite an unrecognized or edited adapter: {path.name}")
    agents_dir.mkdir(parents=True, exist_ok=True)
    for path, content in changed.items():
        path.write_bytes(content)
    state = {str(path.absolute()): digest(content) for path, content in outputs.items()}
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Regenerated {len(changed)} Claude role adapters.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=Path.home() / ".claude/agents")
    parser.add_argument("--write", action="store_true", help="Regenerate recognized adapters; otherwise check only")
    args = parser.parse_args()
    try:
        return synchronize(Path(__file__).absolute().parent, args.agents_dir, write=args.write)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Role adapter error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
