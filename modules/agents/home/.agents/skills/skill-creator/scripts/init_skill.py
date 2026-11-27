#!/usr/bin/env python3
"""
Skill Initializer - Creates a new portable skill from template.

Modified from OpenAI Codex's init_skill.py (Apache-2.0):
- Works cleanly under `python3 -I -B scripts/init_skill.py` and
  `python3 -I -B -m scripts.init_skill` without sys.path hacks.
- Uses trigger-only description placeholder and provider-agnostic wording.
- Makes `agents/openai.yaml` generation opt-in (`--openai-yaml` or `--interface`).
- Enforces explicit UTF-8 LF file output.

Usage:
    init_skill.py <skill-name> --path <path> [--resources scripts,references,assets]
                  [--examples] [--openai-yaml] [--interface key=value]
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_TEMPLATE = """---
name: {skill_name}
description: "[TODO: Describe when to trigger this skill (user intents, situations, symptoms) without summarizing its workflow.]"
---

# {skill_title}

[TODO: Add the non-obvious guidance, rules, or workflow an agent needs. Reference supporting files only when relevant.]
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""Helper script for {skill_name}. Replace with real logic or delete if unused."""

from __future__ import annotations


def main() -> None:
    print("Replace with real logic for {skill_name}")


if __name__ == "__main__":
    main()
'''

EXAMPLE_REFERENCE = """# Reference for {skill_title}

Replace this placeholder with task-specific details an agent would not reliably
know on its own (local schemas, operational constraints, fragile edge cases).

Delete this file if no supported workflow needs it.
"""

EXAMPLE_ASSET = """# Example Asset Placeholder

Replace with output templates, schemas, or static files used in produced outputs,
or delete this file if not needed.
"""


def _load_write_openai_yaml():
    """Import `write_openai_yaml` cleanly under both script and `-I -m` invocations."""
    if __package__:
        from .generate_openai_yaml import write_openai_yaml
        return write_openai_yaml
    sibling = Path(__file__).resolve().with_name("generate_openai_yaml.py")
    spec = importlib.util.spec_from_file_location("skill_creator_generate_openai_yaml", sibling)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {sibling}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.write_openai_yaml


def normalize_skill_name(skill_name: str) -> str:
    """Normalize a skill name to lowercase hyphen-case."""
    normalized = skill_name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def title_case_skill_name(skill_name: str) -> str:
    """Convert hyphenated skill name to Title Case for display."""
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def parse_resources(raw_resources: str) -> list[str]:
    if not raw_resources:
        return []
    resources = [item.strip() for item in raw_resources.split(",") if item.strip()]
    invalid = sorted({item for item in resources if item not in ALLOWED_RESOURCES})
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_RESOURCES))
        print(f"[ERROR] Unknown resource type(s): {', '.join(invalid)}")
        print(f"   Allowed: {allowed}")
        sys.exit(1)
    deduped = []
    seen = set()
    for resource in resources:
        if resource not in seen:
            deduped.append(resource)
            seen.add(resource)
    return deduped


def create_resource_dirs(
    skill_dir: Path,
    skill_name: str,
    skill_title: str,
    resources: list[str],
    include_examples: bool,
) -> None:
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)
        if resource == "scripts":
            if include_examples:
                example_script = resource_dir / "example.py"
                example_script.write_text(
                    EXAMPLE_SCRIPT.format(skill_name=skill_name),
                    encoding="utf-8",
                    newline="\n",
                )
                example_script.chmod(0o755)
                print("[OK] Created scripts/example.py")
            else:
                print("[OK] Created scripts/")
        elif resource == "references":
            if include_examples:
                example_reference = resource_dir / "reference.md"
                example_reference.write_text(
                    EXAMPLE_REFERENCE.format(skill_title=skill_title),
                    encoding="utf-8",
                    newline="\n",
                )
                print("[OK] Created references/reference.md")
            else:
                print("[OK] Created references/")
        elif resource == "assets":
            if include_examples:
                example_asset = resource_dir / "example_asset.txt"
                example_asset.write_text(EXAMPLE_ASSET, encoding="utf-8", newline="\n")
                print("[OK] Created assets/example_asset.txt")
            else:
                print("[OK] Created assets/")


def init_skill(
    skill_name: str,
    path: str | Path,
    resources: list[str],
    include_examples: bool,
    interface_overrides: list[str],
    *,
    openai_yaml: bool = False,
) -> Path | None:
    """Initialize a new skill directory with a portable SKILL.md template."""
    skill_dir = Path(path).resolve() / skill_name

    if skill_dir.exists():
        print(f"[ERROR] Skill directory already exists: {skill_dir}")
        return None

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        print(f"[OK] Created skill directory: {skill_dir}")
    except OSError as e:
        print(f"[ERROR] Error creating directory: {e}")
        return None

    skill_title = title_case_skill_name(skill_name)
    skill_content = SKILL_TEMPLATE.format(
        skill_name=skill_name, skill_title=skill_title
    )

    skill_md_path = skill_dir / "SKILL.md"
    try:
        skill_md_path.write_text(skill_content, encoding="utf-8", newline="\n")
        print("[OK] Created SKILL.md")
    except OSError as e:
        print(f"[ERROR] Error creating SKILL.md: {e}")
        return None

    if openai_yaml or interface_overrides:
        try:
            write_openai_yaml = _load_write_openai_yaml()
            result = write_openai_yaml(skill_dir, skill_name, interface_overrides)
            if not result:
                return None
        except Exception as e:
            print(f"[ERROR] Error creating agents/openai.yaml: {e}")
            return None

    if resources:
        try:
            create_resource_dirs(
                skill_dir, skill_name, skill_title, resources, include_examples
            )
        except OSError as e:
            print(f"[ERROR] Error creating resource directories: {e}")
            return None

    print(f"\n[OK] Skill '{skill_name}' initialized successfully at {skill_dir}")
    print("\nNext steps:")
    print("1. Replace the TODO items in SKILL.md with a trigger-only description and concise instructions")
    if resources:
        if include_examples:
            print("2. Customize or delete the placeholder files in scripts/, references/, and assets/")
        else:
            print("2. Populate scripts/, references/, or assets/ as needed")
    else:
        print("2. Add scripts/, references/, or assets/ only when needed")
    print("3. Validate with: python3 -I -B ~/.agents/skills/skill-creator/scripts/quick_validate.py <skill-dir>")
    return skill_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a new skill directory with a portable SKILL.md template.",
    )
    parser.add_argument("skill_name", help="Skill name (normalized to hyphen-case)")
    parser.add_argument("--path", required=True, help="Output directory for the skill")
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated list: scripts,references,assets",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="Create example files inside the selected resource directories",
    )
    parser.add_argument(
        "--openai-yaml",
        action="store_true",
        help="Also generate optional Codex UI metadata at agents/openai.yaml",
    )
    parser.add_argument(
        "--interface",
        action="append",
        default=[],
        help="Codex agents/openai.yaml interface override in key=value format (implies --openai-yaml)",
    )
    args = parser.parse_args(argv)

    raw_skill_name = args.skill_name
    skill_name = normalize_skill_name(raw_skill_name)
    if not skill_name:
        print("[ERROR] Skill name must include at least one letter or digit.")
        return 1
    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(
            f"[ERROR] Skill name '{skill_name}' is too long ({len(skill_name)} characters). "
            f"Maximum is {MAX_SKILL_NAME_LENGTH} characters."
        )
        return 1
    if skill_name != raw_skill_name:
        print(f"Note: Normalized skill name from '{raw_skill_name}' to '{skill_name}'.")

    resources = parse_resources(args.resources)
    if args.examples and not resources:
        print("[ERROR] --examples requires --resources to be set.")
        return 1

    print(f"Initializing skill: {skill_name}")
    print(f"   Location: {args.path}")
    if resources:
        print(f"   Resources: {', '.join(resources)}")
        if args.examples:
            print("   Examples: enabled")
    else:
        print("   Resources: none (create as needed)")
    print()

    result = init_skill(
        skill_name,
        args.path,
        resources,
        args.examples,
        args.interface,
        openai_yaml=args.openai_yaml,
    )
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
