"""Local migration invariants and helper smoke tests; no live provider calls."""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

try:
    import yaml
except ImportError:  # python-yaml
    yaml = None


# abspath, not resolve(): the tests run from ~/.agents/tests, whose files arcus links into $HOME.
AGENTS = Path(os.path.abspath(__file__)).parents[1]
HOME = AGENTS.parent
ROLES = AGENTS / "roles"
SKILLS = AGENTS / "skills"
installed = unittest.skipUnless((HOME / ".claude/agents").is_dir() and (HOME / ".codex/agents").is_dir(),
                                "role adapters are not generated yet (run launch.py sync)")
needs_yaml = unittest.skipIf(yaml is None, "python-yaml is not installed")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_module("shared_role_renderer", ROLES / "sync_claude.py")
codex_renderer = load_module("shared_codex_role_renderer", ROLES / "sync_codex.py")


def frontmatter(path: Path) -> tuple[dict, str]:
    _, header, body = path.read_text(encoding="utf-8-sig").split("---", 2)
    return yaml.safe_load(header), body.strip()


@needs_yaml
class InstalledContentTests(unittest.TestCase):
    @installed
    def test_every_codex_adapter_matches_catalog_and_loads_shared_brief(self):
        import tomllib
        roles = renderer.load_catalog(ROLES)
        for role in roles:
            with self.subTest(role=role["name"]):
                path = codex_renderer.adapter_path(role, HOME / ".codex/agents")
                self.assertEqual(path.read_bytes(), codex_renderer.render_adapter(role, ROLES))
                config = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(config), {"name", "description", "developer_instructions"})
                self.assertEqual(config["name"], role["name"])
                self.assertEqual(config["description"], role["description"])
                self.assertIn((ROLES / role["brief"]).as_posix(), config["developer_instructions"])

    @installed
    def test_all_ten_adapters_match_catalog_and_load_shared_briefs(self):
        roles = renderer.load_catalog(ROLES)
        self.assertEqual(len(roles), 10)
        for role in roles:
            with self.subTest(role=role["name"]):
                path = HOME / ".claude/agents" / role["brief"]
                self.assertEqual(path.read_bytes(), renderer.render_adapter(role, ROLES))
                metadata, body = frontmatter(path)
                self.assertEqual(metadata["model"], "inherit")
                self.assertIn((ROLES / role["brief"]).as_posix(), body)
                self.assertLess(len(body), 700)
                for skill in metadata.get("skills", []):
                    self.assertTrue((SKILLS / skill / "SKILL.md").is_file(), skill)

    @installed
    @unittest.skipUnless((ROLES / "archive").is_dir(), "no pre-migration archive on this machine")
    def test_native_metadata_preserves_legacy_contracts(self):
        for role in renderer.load_catalog(ROLES):
            with self.subTest(role=role["name"]):
                original, _ = frontmatter(ROLES / "archive/claude-agents-pre-migration" / role["brief"])
                current, _ = frontmatter(HOME / ".claude/agents" / role["brief"])
                original["model"] = "inherit"
                if isinstance(original.get("skills"), str):
                    original["skills"] = [part.strip() for part in original["skills"].split(",")]
                self.assertEqual(current, original)

    @unittest.skipUnless((ROLES / "archive").is_dir(), "no pre-migration archive on this machine")
    def test_impeccable_output_and_repair_contracts_survive_extraction(self):
        for role in renderer.load_catalog(ROLES):
            if not role["name"].startswith("impeccable-"):
                continue
            with self.subTest(role=role["name"]):
                _, original = frontmatter(ROLES / "archive/claude-agents-pre-migration" / role["brief"])
                current = (ROLES / role["brief"]).read_text(encoding="utf-8")
                self.assertEqual(current.split("## Output Contract", 1)[1].strip(),
                                 original.split("## Output Contract", 1)[1].strip())
                if "## Entry Atomicity" in original:
                    self.assertIn(original.split("## Entry Atomicity", 1)[1], current)

    @installed
    def test_shared_skill_entries_still_share_physical_sources(self):
        for shared in sorted(SKILLS.glob("*/SKILL.md")):
            with self.subTest(skill=shared.parent.name):
                claude = HOME / ".claude/skills" / shared.parent.name / "SKILL.md"
                self.assertTrue(claude.is_file(), "run launch.py sync to link the shared skills into Claude")
                self.assertTrue(shared.samefile(claude))

    def test_new_skills_are_discoverable_without_shadowing_native_skill_creator(self):
        for name in ("agent-roles", "research-report"):
            metadata, body = frontmatter(SKILLS / name / "SKILL.md")
            self.assertEqual(metadata["name"], name)
            self.assertTrue(metadata["description"])
            self.assertLess(len(metadata["description"]), 1024)
            self.assertTrue(body)
            self.assertNotIn("disable-model-invocation", metadata)
        self.assertFalse((SKILLS / "skill-creator").exists())

    @unittest.skipUnless((HOME / ".codex/skills/.system/skill-creator/scripts/quick_validate.py").is_file(),
                         "Codex's bundled skill-creator is not installed")
    def test_owned_skill_core_metadata_passes_native_quick_validation(self):
        validator = load_module("native_skill_validator", HOME / ".codex/skills/.system/skill-creator/scripts/quick_validate.py")
        names = (
            "agent-roles", "research-report", "building-runner-tuis", "coding",
            "designing-tuis", "gauntlet-loop", "humanizer",
            "image-generation", "ml-design", "reverse-engineer-anything", "sillytavern-local",
            "systematic-debugging", "vcs", "skill-pressure-testing", "council", "correct", "bro",
        )
        for name in names:
            if not (SKILLS / name).is_dir():
                continue  # private-module skills are absent without that module
            with self.subTest(skill=name):
                valid, reason = validator.validate_skill(SKILLS / name)
                self.assertTrue(valid, reason)
        metadata, _ = frontmatter(SKILLS / "impeccable/SKILL.md")
        self.assertEqual(metadata["name"], "impeccable")
        self.assertEqual(metadata["version"], "4.8.0")
        self.assertTrue(metadata["user-invocable"])
        self.assertEqual(metadata["license"], "Apache 2.0")

    def test_referenced_shared_role_and_local_markdown_paths_exist(self):
        files = [*ROLES.glob("*.md"), SKILLS / "agent-roles/SKILL.md", SKILLS / "humanizer/SKILL.md",
                 SKILLS / "impeccable/reference/new-work.md", SKILLS / "impeccable/reference/live.md",
                 *list((SKILLS / "impeccable/reference/degraded").glob("*.md"))]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(file=str(path), target=target):
                    self.assertTrue((path.parent / target.split("#", 1)[0]).exists())
            for target in re.findall(r"~/(\.agents/roles/[A-Za-z0-9-]+\.md)", text):
                self.assertTrue((HOME / target).is_file(), target)


@needs_yaml
class RoleRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable roles ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.roles = self.root / "shared roles"
        self.roles.mkdir()
        self.output = self.root / "native agents"
        self.catalog = {"schemaVersion": 1, "roles": [{
            "name": "scout", "brief": "scout.md", "description": "Inspect the given scope.",
            "claude": {"model": "inherit", "tools": "Read, Grep", "maxTurns": 10},
        }]}
        self.save_catalog()
        (self.roles / "scout.md").write_text("Read-only. Return scoped evidence.\n", encoding="utf-8")
        (self.roles / "migration-baseline.json").write_text('{"legacyAgents": {}}', encoding="utf-8")

    def save_catalog(self):
        (self.roles / "catalog.json").write_text(json.dumps(self.catalog), encoding="utf-8")

    def sync(self, *, write=False):
        with redirect_stdout(StringIO()):
            return renderer.synchronize(self.roles, self.output, write=write)

    def test_check_does_not_create_native_directory(self):
        self.assertEqual(self.sync(), 1)
        self.assertFalse(self.output.exists())

    def test_generation_roundtrip_is_idempotent_and_handles_spaces(self):
        self.assertEqual(self.sync(write=True), 0)
        target = self.output / "scout.md"
        before = target.stat().st_mtime_ns
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.sync(write=True), 0)
        self.assertEqual(target.stat().st_mtime_ns, before)
        metadata, body = frontmatter(target)
        self.assertEqual(metadata["maxTurns"], 10)
        self.assertIn((self.roles / "scout.md").as_posix(), body)

    def test_edited_adapter_is_not_overwritten(self):
        self.sync(write=True)
        target = self.output / "scout.md"
        target.write_text("User edit", encoding="utf-8")
        self.assertEqual(self.sync(), 1)
        with self.assertRaisesRegex(ValueError, "edited adapter"):
            self.sync(write=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "User edit")

    def test_source_metadata_update_can_regenerate_recognized_output(self):
        self.sync(write=True)
        self.catalog["roles"][0]["claude"]["maxTurns"] = 12
        self.save_catalog()
        self.assertEqual(self.sync(write=True), 0)
        self.assertEqual(frontmatter(self.output / "scout.md")[0]["maxTurns"], 12)

    def test_unrecognized_existing_agent_blocks_all_writes(self):
        self.output.mkdir()
        target = self.output / "scout.md"
        target.write_text("Existing custom agent", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            self.sync(write=True)
        self.assertFalse((self.roles / "claude-adapter-state.json").exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "Existing custom agent")

    def test_missing_brief_and_escaping_path_are_rejected(self):
        for value in ("missing.md", "../outside.md"):
            self.catalog["roles"][0]["brief"] = value
            self.save_catalog()
            with self.assertRaises(ValueError):
                self.sync(write=True)
        self.assertFalse(self.output.exists())

    def test_duplicate_filenames_and_names_are_rejected(self):
        for duplicate in ({"name": "other"}, {"name": "SCOUT", "brief": "second.md"}):
            self.catalog["roles"] = [self.catalog["roles"][0]]
            self.catalog["roles"].append({**self.catalog["roles"][0], **duplicate})
            self.save_catalog()
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                self.sync()

    def test_skill_metadata_requires_list_and_read_access(self):
        self.catalog["roles"][0]["claude"]["skills"] = "coding"
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "skills must be a list"):
            self.sync()
        self.catalog["roles"][0]["claude"] = {"model": "inherit", "tools": "Bash"}
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "Read access"):
            self.sync()


class CodexAdapterRendererTests(unittest.TestCase):
    def test_write_refuses_an_edited_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            roles_dir = Path(temporary) / "roles"
            shutil.copytree(ROLES, roles_dir, ignore=shutil.ignore_patterns("archive", "*-state.json"))
            agents_dir = Path(temporary) / "agents"
            with redirect_stdout(StringIO()):
                self.assertEqual(codex_renderer.synchronize(roles_dir, agents_dir, write=True), 0)
                self.assertEqual(codex_renderer.synchronize(roles_dir, agents_dir), 0)
            edited = agents_dir / "reviewer.toml"
            edited.write_text('name = "reviewer"\n', encoding="utf-8")
            with redirect_stdout(StringIO()), self.assertRaises(ValueError):
                codex_renderer.synchronize(roles_dir, agents_dir, write=True)


if __name__ == "__main__":
    unittest.main()
