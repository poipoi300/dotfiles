#!/usr/bin/env python3
"""
Quick validation script for portable agent skills.

Modified from OpenAI Codex's and Anthropic's skill-creator validators (Apache-2.0):
- Enforces Codex's portable 5-key frontmatter allowlist with explicit guidance
  when `disable-model-invocation` is present.
- Rejects unfinished `[TODO:...]` placeholders in metadata and outside fenced
  code blocks.
- Checks for nested `SKILL.md` files using relative paths (ignoring `evals`,
  `tests`, `__pycache__`, and `node_modules`).
- Uses explicit UTF-8 file I/O.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
ALLOWED_PROPERTIES = frozenset({"name", "description", "license", "allowed-tools", "metadata"})
IGNORED_SUBDIRS = frozenset({"evals", "tests", "__pycache__", "node_modules", ".git"})


def _find_nested_skill_mds(skill_path: Path) -> list[str]:
    """Return relative POSIX paths of unexpected nested SKILL.md files."""
    nested: list[str] = []
    for candidate in sorted(skill_path.rglob("SKILL.md")):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(skill_path)
        if rel == Path("SKILL.md"):
            continue
        if any(part in IGNORED_SUBDIRS for part in rel.parts[:-1]):
            continue
        nested.append(rel.as_posix())
    return nested


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Validate a skill directory and return `(is_valid, message)`."""
    skill_path = Path(skill_path)

    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        return False, "SKILL.md not found"

    nested = _find_nested_skill_mds(skill_path)
    if nested:
        return (
            False,
            f"Unexpected nested SKILL.md file(s): {', '.join(nested)}. "
            "Each skill directory must contain only one root SKILL.md.",
        )

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        if "disable-model-invocation" in unexpected_keys:
            return (
                False,
                "Unexpected key 'disable-model-invocation' in SKILL.md frontmatter: "
                "Codex's skill loader rejects it. Limit triggering in 'description' "
                "or set policy.allow_implicit_invocation: false in agents/openai.yaml.",
            )
        allowed = ", ".join(sorted(ALLOWED_PROPERTIES))
        unexpected = ", ".join(sorted(unexpected_keys))
        return (
            False,
            f"Unexpected key(s) in SKILL.md frontmatter: {unexpected}. Allowed properties are: {allowed}",
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if not name:
        return False, "Name cannot be empty"
    if name.startswith("[TODO:"):
        return False, "Name contains an unfinished TODO placeholder"
    if not re.match(r"^[a-z0-9-]+$", name):
        return (
            False,
            f"Name '{name}' should be hyphen-case (lowercase letters, digits, and hyphens only)",
        )
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return (
            False,
            f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens",
        )
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return (
            False,
            f"Name is too long ({len(name)} characters). "
            f"Maximum is {MAX_SKILL_NAME_LENGTH} characters.",
        )

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if not description:
        return False, "Description cannot be empty"
    if description.startswith("[TODO:"):
        return False, "Description contains an unfinished TODO placeholder"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)"
    if len(description) > MAX_DESCRIPTION_LENGTH:
        return (
            False,
            f"Description is too long ({len(description)} characters). "
            f"Maximum is {MAX_DESCRIPTION_LENGTH} characters.",
        )

    body = content[match.end() :]
    fence_marker = None
    fence_length = 0
    for line in body.splitlines():
        fence = re.match(r"^[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker[0]
                fence_length = len(marker)
            elif (
                marker[0] == fence_marker
                and len(marker) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_marker = None
                fence_length = 0
            continue

        if fence_marker is None and re.fullmatch(
            r"[ ]{0,3}\[TODO:[^\n]*\][ \t]*", line
        ):
            return False, "Skill instructions contain an unfinished TODO placeholder"

    return True, "Skill is valid!"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python3 -I -B quick_validate.py <skill_directory>")
        return 1

    valid, message = validate_skill(args[0])
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
