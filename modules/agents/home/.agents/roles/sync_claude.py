"""Render Claude's native agent adapters from shared role briefs and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


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
    body = adapter_instructions(roles_dir, role, "sync_claude.py")
    return f"---\n{header}\n---\n\n{body}".encode("utf-8")


def adapter_instructions(roles_dir: Path, role: dict, renderer: str) -> str:
    """The body every native adapter carries: load the shared brief, follow its contracts."""
    brief = (roles_dir / role["brief"]).absolute().as_posix()
    return (
        f"Generated adapter. Maintain the shared role brief and catalog.json; regenerate with {renderer}.\n\n"
        f"Read the shared role brief at `{brief}` before acting. Follow its complete input, access, "
        "execution, and output contracts together with the task and repository instructions. "
        "Use only tools available in this runtime. If the brief cannot be read, report that blocker.\n"
    )


def replace_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_adapters(outputs: dict[Path, bytes], state_path: Path, label: str, *, write: bool,
                   recognized: dict[str, str] | None = None) -> int:
    """Write changed adapters, refusing to replace a file this generator did not write last.

    `recognized` maps file names to extra digests that may be replaced (pre-migration files)."""
    for path in (*outputs, state_path):
        if path.is_symlink():
            # A link, dangling or not, would make the write land outside the agents folder.
            raise ValueError(f"Refusing to write a role adapter or its state through a symlink: {path.name}")
    changed = {path: content for path, content in outputs.items() if not path.exists() or path.read_bytes() != content}
    state = {str(path.absolute()): digest(content) for path, content in outputs.items()}
    state_bytes = (json.dumps(state, indent=2) + "\n").encode("utf-8")
    if not changed:
        # Files identical to the generated output are this generator's; record them if a
        # previous run wrote the adapters but not its state.
        if write and (state_path.read_bytes() if state_path.exists() else None) != state_bytes:
            replace_file(state_path, state_bytes)
        print(f"All {len(outputs)} {label} role adapters are current.")
        return 0
    if not write:
        print(f"{label} role adapters need regeneration: " + ", ".join(path.name for path in changed))
        return 1
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    for path in changed:
        accepted = {previous.get(str(path.absolute())), (recognized or {}).get(path.name)}
        if path.exists() and digest(path.read_bytes()) not in accepted:
            raise ValueError(f"Refusing to overwrite an unrecognized or edited adapter: {path.name}")
    for path, content in changed.items():
        replace_file(path, content)
    replace_file(state_path, state_bytes)
    print(f"Regenerated {len(changed)} {label} role adapters.")
    return 0


def synchronize(roles_dir: Path, agents_dir: Path, *, write: bool = False) -> int:
    roles = load_catalog(roles_dir)
    outputs = {agents_dir / role["brief"]: render_adapter(role, roles_dir) for role in roles}
    # Digests of pre-migration hand-written agents that may be replaced; absent on a fresh machine.
    baseline_path = roles_dir / "migration-baseline.json"
    legacy = json.loads(baseline_path.read_text(encoding="utf-8")).get("legacyAgents", {}) if baseline_path.exists() else {}
    return write_adapters(outputs, roles_dir / "claude-adapter-state.json", "Claude", write=write, recognized=legacy)


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
