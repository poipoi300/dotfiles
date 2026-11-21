"""Antigravity and Grok: MCP translation, native config ownership, skill links, roles, launch."""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

# abspath, not resolve(): run from ~/.agents/tests, not from the dotfiles repository the links point into.
AGENTS = Path(os.path.abspath(__file__)).parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync = load("agent_common_sync_providers", AGENTS / "sync.py")
try:
    import tomlkit  # noqa: F401  python-tomlkit, installed by the agents module
except ImportError:
    tomlkit = None
needs_tomlkit = unittest.skipIf(tomlkit is None, "python-tomlkit is not installed")

GROK_CONFIG = """# Grok writes this file itself
disabled_mcp_servers = ["elsewhere"]

[marketplace]
official_marketplace_auto_installed = true

[[marketplace.sources]]
name = "xAI Official"
git = "https://example.test/plugins.git"

[ui]
yolo = false

[mcp_servers.unmanaged]
command = "keep"
enabled = true

[compat.claude]
rules = true
"""


class TranslationTests(unittest.TestCase):
    def test_antigravity_inherits_forwarded_environment_and_keeps_constants(self):
        registry = {"mcpServers": {
            "stdio": {"command": "tool", "args": ["mcp"], "env": {"TOKEN": "${TOKEN}", "PORT": "9000"}},
            "only_forwarded": {"command": "tool", "env": {"TOKEN": "${TOKEN}"}},
            "http": {"type": "http", "url": "https://example.test/mcp", "headers": {"X-Mode": "local"}},
        }}
        servers = sync.native_servers(registry, "antigravity")
        self.assertEqual(servers["stdio"], {"command": "tool", "args": ["mcp"], "env": {"PORT": "9000"}})
        self.assertEqual(servers["only_forwarded"], {"command": "tool"})
        self.assertEqual(servers["http"], {"url": "https://example.test/mcp", "headers": {"X-Mode": "local"}})

    def test_antigravity_rejects_references_it_would_pass_through_literally(self):
        for server in ({"type": "http", "url": "https://example.test", "headers": {"Authorization": "${AUTH}"}},
                       {"command": "x", "env": {"TOKEN": "${OTHER}"}},
                       {"command": "x", "env": {"TOKEN": "Bearer ${TOKEN}"}},
                       {"command": "x", "args": ["${DIR}"]}):
            with self.subTest(server=server), self.assertRaises(sync.SyncError):
                sync.native_servers({"mcpServers": {"sample": server}}, "antigravity")

    def test_grok_keeps_references_because_it_expands_them(self):
        registry = {"mcpServers": {
            "stdio": {"command": "tool", "args": ["${DIR}"], "env": {"TOKEN": "${TOKEN}"}},
            "http": {"type": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "Bearer ${AUTH}"}},
        }}
        servers = sync.native_servers(registry, "grok")
        self.assertEqual(servers["stdio"], {"command": "tool", "args": ["${DIR}"], "env": {"TOKEN": "${TOKEN}"}})
        self.assertEqual(servers["http"], {"url": "https://example.test/mcp",
                                           "headers": {"Authorization": "Bearer ${AUTH}"}})
        for name in ("dotted.name", "9starts-with-digit", "trailing_", "double__underscore"):
            with self.subTest(name=name), self.assertRaisesRegex(sync.SyncError, "Grok skips the tools"):
                sync.native_servers({"mcpServers": {name: {"command": "x"}}}, "grok")
        self.assertEqual(set(sync.native_servers({"mcpServers": {"_ok": {"command": "x"}, "ok-1_a": {"command": "x"}}},
                                                 "grok")), {"_ok", "ok-1_a"})

    def test_the_public_example_registry_translates_for_every_provider(self):
        example = json.loads((AGENTS / "mcp.example.json").read_text())
        for provider in sync.PROVIDERS:
            with self.subTest(provider=provider):
                self.assertEqual(set(sync.native_servers(example, provider)), set(example["mcpServers"]))

    def test_provider_env_reaches_only_the_named_provider(self):
        registry = {"mcpServers": {"tool": {"command": "x", "providerEnv": {"grok": {"MODE": "g"},
                                                                          "antigravity": {"MODE": "a"}}}}}
        self.assertEqual(sync.native_servers(registry, "grok")["tool"]["env"], {"MODE": "g"})
        self.assertEqual(sync.native_servers(registry, "antigravity")["tool"]["env"], {"MODE": "a"})
        self.assertNotIn("env", sync.native_servers(registry, "codex")["tool"])


class ProviderHomeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        for folder in (".agents", ".claude", ".codex"):
            (self.home / folder).mkdir()
        self.registry = self.home / ".agents/mcp.json"
        self.registry.write_text(json.dumps({"mcpServers": {
            "sample": {"command": "sample", "args": ["--flag"], "env": {"MODE": "x"}},
            "remote": {"type": "http", "url": "https://example.test/mcp"},
        }}))

    def enable(self, *providers):
        for provider in providers:
            (self.home / sync.PROVIDER_HOMES[provider]).mkdir(exist_ok=True)

    def test_absent_providers_are_left_alone(self):
        sync.apply(self.home, self.registry)
        self.assertFalse((self.home / ".gemini").exists())
        self.assertFalse((self.home / ".grok").exists())

    def test_antigravity_mcp_entries_are_owned_beside_unmanaged_ones(self):
        self.enable("antigravity")
        path = self.home / sync.ANTIGRAVITY_MCP
        path.parent.mkdir(parents=True)
        path.write_text("")  # Antigravity creates the file empty
        sync.apply(self.home, self.registry)
        servers = json.loads(path.read_text())["mcpServers"]
        self.assertEqual(servers["sample"], {"command": "sample", "args": ["--flag"], "env": {"MODE": "x"}})
        self.assertEqual(servers["remote"], {"url": "https://example.test/mcp"})
        config = json.loads(path.read_text())
        config["mcpServers"]["mine"] = {"command": "kept"}
        path.write_text(json.dumps(config))
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])
        config["mcpServers"]["sample"]["disabled"] = True
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(sync.SyncError, "Drift in managed Antigravity MCP entry sample"):
            sync.prepare(self.home, self.registry)

    @needs_tomlkit
    def test_grok_config_keeps_everything_else_and_turns_off_claude_compatibility(self):
        self.enable("grok")
        path = self.home / sync.GROK_CONFIG
        path.write_text(GROK_CONFIG)
        sync.apply(self.home, self.registry)
        text = path.read_text()
        self.assertTrue(text.startswith("# Grok writes this file itself\n"))
        config = tomllib.loads(text)
        before = tomllib.loads(GROK_CONFIG)
        for key in ("disabled_mcp_servers", "marketplace", "ui"):
            self.assertEqual(config[key], before[key])
        self.assertEqual(config["mcp_servers"], {
            "unmanaged": {"command": "keep", "enabled": True},
            "sample": {"command": "sample", "args": ["--flag"], "env": {"MODE": "x"}},
            "remote": {"url": "https://example.test/mcp"},
        })
        self.assertEqual(config["compat"]["claude"], {"rules": True, "mcps": False})
        self.assertEqual(config["skills"]["ignore"], ["~/.claude/skills"])
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])
        # A registry removal deletes only the owned table.
        self.registry.write_text(json.dumps({"mcpServers": {"remote": {"type": "http", "url": "https://example.test/mcp"}}}))
        sync.apply(self.home, self.registry)
        self.assertEqual(set(tomllib.loads(path.read_text())["mcp_servers"]), {"unmanaged", "remote"})

    @needs_tomlkit
    def test_grok_native_edits_to_owned_settings_are_drift(self):
        self.enable("grok")
        path = self.home / sync.GROK_CONFIG
        sync.apply(self.home, self.registry)
        edited = path.read_text().replace('[mcp_servers.sample]\n', '[mcp_servers.sample]\nenabled = false\n')
        path.write_text(edited)
        with self.assertRaisesRegex(sync.SyncError, "Drift in managed Grok MCP entry sample"):
            sync.prepare(self.home, self.registry)
        path.write_text(edited.replace("enabled = false\n", "").replace("mcps = false", "mcps = true"))
        with self.assertRaisesRegex(sync.SyncError, "Drift in managed Grok Claude compatibility entry mcps"):
            sync.prepare(self.home, self.registry)
        path.write_text(edited.replace("enabled = false\n", "").replace('"~/.claude/skills"', ""))
        with self.assertRaisesRegex(sync.SyncError, "Drift in managed Grok skills.ignore entry"):
            sync.prepare(self.home, self.registry)

    @needs_tomlkit
    def test_grok_compatibility_the_user_turned_on_is_not_overridden(self):
        self.enable("grok")
        path = self.home / sync.GROK_CONFIG
        path.write_text("[compat.claude]\nmcps = true\n")
        with self.assertRaisesRegex(sync.SyncError, "Unmanaged Grok Claude compatibility entry collides with mcps"):
            sync.apply(self.home, self.registry)
        self.assertEqual(path.read_text(), "[compat.claude]\nmcps = true\n")

    @needs_tomlkit
    def test_grok_keeps_its_other_ignored_skills_and_inline_tables(self):
        self.enable("grok")
        path = self.home / sync.GROK_CONFIG
        original = ('mcp_servers = { mine = { command = "kept" } }\ncompat = { cursor = { skills = false } }\n\n'
                    '[skills]\nignore = ["~/wip"]\n')
        path.write_text(original)
        sync.apply(self.home, self.registry)
        config = tomllib.loads(path.read_text())
        self.assertEqual(set(config["mcp_servers"]), {"mine", "sample", "remote"})
        self.assertEqual(config["compat"], {"cursor": {"skills": False}, "claude": {"mcps": False}})
        self.assertEqual(config["skills"]["ignore"], ["~/wip", "~/.claude/skills"])
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])

    @needs_tomlkit
    def test_one_providers_refusal_leaves_the_others_to_sync(self):
        self.enable("grok", "antigravity")
        grok = self.home / sync.GROK_CONFIG
        grok.write_text("[compat.claude]\nmcps = true\n")
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"grok"})
        self.assertEqual(grok.read_text(), "[compat.claude]\nmcps = true\n")
        self.assertIn("sample", json.loads((self.home / ".claude.json").read_text())["mcpServers"])
        self.assertIn("sample", json.loads((self.home / sync.ANTIGRAVITY_MCP).read_text())["mcpServers"])
        state = json.loads((self.home / ".agents/state/sync-state.json").read_text())
        self.assertNotIn("grok", state)
        with self.assertRaisesRegex(sync.SyncError, "collides with mcps"):
            sync.apply(self.home, self.registry)  # strict mode, as `sync.py sync` and `launch.py sync` use

    def test_backups_stay_private_whatever_the_umask(self):
        self.enable("antigravity")
        path = self.home / sync.ANTIGRAVITY_MCP
        path.parent.mkdir(parents=True)
        (self.home / ".agents/state").mkdir()  # exists on a real machine; 0177 could not create it
        for umask in (0o022, 0o177):  # 0177 also drops the owner's x from a new folder
            with self.subTest(umask=oct(umask)):
                path.write_text('{"mcpServers": {"other": {"command": "%s"}}}' % umask)
                path.chmod(0o600)
                previous = os.umask(umask)
                try:
                    sync.apply(self.home, self.registry)
                finally:
                    os.umask(previous)
                backups = self.home / ".agents/backups"
                self.assertEqual(backups.stat().st_mode & 0o777, 0o700)
                for item in backups.rglob("*"):
                    self.assertEqual(item.stat().st_mode & 0o777, 0o700 if item.is_dir() else 0o600, item)
                (self.home / ".agents/state/sync-state.json").unlink()

    def test_unreadable_provider_files_stay_inside_that_provider(self):
        self.enable("grok")
        (self.home / sync.GROK_CONFIG).mkdir()  # a folder where the file belongs
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"grok"})
        self.assertIn("sample", json.loads((self.home / ".claude.json").read_text())["mcpServers"])

    def test_a_file_where_a_skills_folder_belongs_is_that_providers_collision(self):
        self.enable("antigravity")
        source = self.home / ".agents/skills/alpha"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n")
        (self.home / ".gemini/config").mkdir(parents=True)
        (self.home / ".gemini/config/skills").write_text("not a folder")
        with self.assertRaisesRegex(sync.SyncError, "not a folder"):
            sync.apply(self.home, self.registry)
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"antigravity"})
        self.assertTrue((self.home / ".claude/skills/alpha").is_symlink())

    def test_a_failed_link_write_undoes_only_that_provider(self):
        self.enable("antigravity")
        source = self.home / ".agents/skills/alpha"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n")
        skills = self.home / ".gemini/config/skills"
        skills.mkdir(parents=True)
        skills.chmod(0o500)  # read-only: planning passes, creating the link fails
        self.addCleanup(skills.chmod, 0o700)
        with self.assertRaises(OSError):
            sync.apply(self.home, self.registry)
        self.assertFalse((self.home / ".claude.json").exists())  # strict: Claude's new file is undone too
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"antigravity"})
        self.assertIn("sample", json.loads((self.home / ".claude.json").read_text())["mcpServers"])
        self.assertTrue((self.home / ".claude/skills/alpha").is_symlink())
        self.assertFalse((self.home / sync.ANTIGRAVITY_MCP).exists())
        self.assertNotIn("antigravity", json.loads((self.home / ".agents/state/sync-state.json").read_text()))

    def test_undoing_a_file_written_through_a_dangling_link_keeps_the_link(self):
        self.enable("antigravity")
        source = self.home / ".agents/skills/alpha"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n")
        config = self.home / ".gemini/config"
        config.mkdir(parents=True)
        target = self.home / "managed/mcp_config.json"
        target.parent.mkdir()
        (config / "mcp_config.json").symlink_to(target)  # dangling until something writes it
        (config / "skills").mkdir()
        (config / "skills").chmod(0o500)
        self.addCleanup((config / "skills").chmod, 0o700)
        sync.apply(self.home, self.registry, {})
        self.assertTrue((config / "mcp_config.json").is_symlink())
        self.assertFalse(target.exists())

    def test_a_concurrent_state_edit_undoes_the_run(self):
        self.enable("antigravity")
        source = self.home / ".agents/skills/alpha"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n")
        state = self.home / ".agents/state/sync-state.json"
        real = sync.create_skill_link

        def someone_edits_the_state(link):
            state.write_text('{"version": 1, "edited": true}')
            real(link)

        with mock.patch.object(sync, "create_skill_link", side_effect=someone_edits_the_state), \
             self.assertRaisesRegex(sync.SyncError, "Concurrent edit"):
            sync.apply(self.home, self.registry, {})
        self.assertEqual(json.loads(state.read_text()), {"version": 1, "edited": True})
        self.assertFalse((self.home / ".claude.json").exists())
        self.assertFalse((self.home / ".claude/skills/alpha").exists())

    def test_providers_sharing_one_file_through_links_are_refused(self):
        self.enable("antigravity")
        shared = self.home / "shared.json"
        shared.write_text('{"mcpServers": {}}')
        (self.home / ".gemini/config").mkdir(parents=True)
        (self.home / ".claude.json").symlink_to(shared)
        (self.home / sync.ANTIGRAVITY_MCP).symlink_to(shared)
        with self.assertRaisesRegex(sync.SyncError, "same file"):
            sync.apply(self.home, self.registry)
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"claude", "antigravity"})
        self.assertEqual(shared.read_text(), '{"mcpServers": {}}')
        self.assertNotIn("claude", json.loads((self.home / ".agents/state/sync-state.json").read_text()))

    def test_a_symlinked_native_file_stays_a_link(self):
        target = self.home / "managed-claude.json"
        target.write_text('{"keep": true}')
        (self.home / ".claude.json").symlink_to(target)
        sync.apply(self.home, self.registry)
        self.assertTrue((self.home / ".claude.json").is_symlink())
        self.assertIn("sample", json.loads(target.read_text())["mcpServers"])

    def test_a_skill_collision_holds_back_all_of_that_providers_changes(self):
        self.enable("antigravity")
        for name in ("alpha", "beta"):
            source = self.home / ".agents/skills" / name
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n")
        (self.home / ".gemini/config/skills/alpha").mkdir(parents=True)  # someone else's folder
        problems = {}
        sync.apply(self.home, self.registry, problems)
        self.assertEqual(set(problems), {"antigravity"})
        self.assertFalse((self.home / sync.ANTIGRAVITY_MCP).exists())
        self.assertFalse((self.home / ".gemini/config/skills/beta").exists())
        self.assertTrue((self.home / ".claude/skills/beta").is_symlink())
        self.assertNotIn("antigravity", json.loads((self.home / ".agents/state/sync-state.json").read_text()))

    def test_grok_without_tomlkit_refuses_instead_of_rewriting(self):
        self.enable("grok")
        with mock.patch.dict(sys.modules, {"tomlkit": None}), self.assertRaisesRegex(sync.SyncError, "python-tomlkit"):
            sync.prepare(self.home, self.registry)

    def test_shared_skills_are_linked_into_antigravity_and_collisions_refused(self):
        self.enable("antigravity")
        source = self.home / ".agents/skills/example"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: example\ndescription: Example\n---\n")
        sync.apply(self.home, self.registry)
        link = self.home / ".gemini/config/skills/example"
        self.assertTrue(link.samefile(source))
        self.assertTrue((self.home / ".claude/skills/example").samefile(source))
        link.unlink()
        link.mkdir()
        with self.assertRaisesRegex(sync.SyncError, "Antigravity skill collision: example"):
            sync.plan_skill_links(self.home)


class RoleAdapterTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.out = Path(scratch.name)
        self.roles = AGENTS / "roles"
        self.grok = load("shared_grok_role_renderer", self.roles / "sync_grok.py")
        self.antigravity = load("shared_antigravity_role_renderer", self.roles / "sync_antigravity.py")
        self.catalog = self.grok.load_claude_renderer().load_catalog(self.roles)

    def header(self, content: bytes) -> dict:
        _, header, body = content.decode().split("---", 2)
        return {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1]) for line in header.strip().splitlines()}, body

    def test_grok_adapters_name_the_role_and_load_the_shared_brief(self):
        for role in self.catalog:
            with self.subTest(role=role["name"]):
                metadata, body = self.header(self.grok.render_adapter(role, self.roles))
                self.assertEqual(metadata, {"name": role["name"], "description": role["description"]})
                self.assertIn((self.roles / role["brief"]).as_posix(), body)

    def test_antigravity_adapters_are_subagents_with_mapped_tools(self):
        reviewer = next(role for role in self.catalog if role["name"] == "reviewer")
        metadata, body = self.header(self.antigravity.render_adapter(reviewer, self.roles))
        self.assertEqual(metadata["tools"], ["read_file", "run_command", "manage_task"])
        self.assertIs(metadata["subagent"], True)
        self.assertIs(metadata["mainAgent"], False)
        self.assertTrue(body.strip().startswith("# reviewer\n"))
        for role in self.catalog:
            with self.subTest(role=role["name"]):
                self.assertIn("read_file", self.antigravity.native_tools(role))  # needed to read the brief
        with self.assertRaisesRegex(ValueError, "No Antigravity tool"):
            self.antigravity.native_tools({"name": "x", "claude": {"tools": "Read, NotebookEdit"}})
        with self.assertRaisesRegex(ValueError, "built-in"):
            self.antigravity.render_adapter({**reviewer, "name": "self"}, self.roles)

    def test_written_adapters_are_current_and_edits_are_not_overwritten(self):
        # A copy, so the adapter state files land in the scratch folder, not the live roles folder.
        roles = self.out / "roles"
        shutil.copytree(self.roles, roles, ignore=shutil.ignore_patterns("archive", "*-state.json", "__pycache__"))
        for renderer in ("sync_grok", "sync_antigravity"):
            with self.subTest(renderer=renderer), mock.patch("sys.stdout", new_callable=StringIO):
                module = load(f"copied_{renderer}", roles / f"{renderer}.py")
                agents = self.out / renderer
                self.assertEqual(module.synchronize(roles, agents, write=True), 0)
                self.assertEqual(module.synchronize(roles, agents), 0)
                self.assertEqual(len(list(agents.glob("*.md"))), len(self.catalog))
                edited = agents / "reviewer.md"
                edited.write_text(edited.read_text() + "local edit\n")
                with self.assertRaisesRegex(ValueError, "edited adapter"):
                    module.synchronize(roles, agents, write=True)
                edited.unlink()
                edited.symlink_to(self.out / "elsewhere.md")  # dangling
                with self.assertRaisesRegex(ValueError, "through a symlink"):
                    module.synchronize(roles, agents, write=True)
                self.assertFalse((self.out / "elsewhere.md").exists())
                edited.unlink()
                # A run that wrote the adapters but not its state is repaired, not refused later.
                state = roles / module.STATE_FILE
                self.assertEqual(module.synchronize(roles, agents, write=True), 0)
                state.write_text("{}")
                self.assertEqual(module.synchronize(roles, agents, write=True), 0)
                self.assertIn(str((agents / "reviewer.md").absolute()), json.loads(state.read_text()))
                state.unlink()
                state.symlink_to(self.out / "state-target.json")
                with self.assertRaisesRegex(ValueError, "through a symlink"):
                    module.synchronize(roles, agents, write=True)
                self.assertFalse((self.out / "state-target.json").exists())
                state.unlink()


class LaunchTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        (self.home / ".agents").mkdir()
        self.launch = load("agent_common_launch_providers", AGENTS / "launch.py")

    def test_agy_and_grok_start_their_own_executables_silently(self):
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"agy": sys.executable,
                                                                                  "grok": sys.executable}}))
        executable = str(Path(sys.executable).resolve())
        for provider, arguments in (("agy", ["acp"]), ("grok", ["agent", "--always-approve", "stdio"])):
            with self.subTest(provider=provider), mock.patch.object(self.launch.Path, "home", return_value=self.home), \
                 mock.patch.object(self.launch, "apply", return_value=[]), \
                 mock.patch.object(self.launch, "sync_generated_adapters", return_value={}), \
                 mock.patch.object(self.launch.os, "execv") as execv, \
                 mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                self.launch.main([provider, *arguments])
            self.assertEqual(stdout.getvalue(), "")
            execv.assert_called_once_with(executable, [executable, *arguments])

    def test_a_conflict_in_another_providers_files_does_not_block_the_start(self):
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"codex": sys.executable,
                                                                                  "grok": sys.executable}}))
        failing = lambda home, registry, problems=None: problems.update(grok=self.launch.SyncError("grok conflict")) or []
        with mock.patch.object(self.launch.Path, "home", return_value=self.home), \
             mock.patch.object(self.launch, "apply", side_effect=failing), \
             mock.patch.object(self.launch, "sync_generated_adapters", return_value={}), \
             mock.patch.object(self.launch.os, "execv") as execv, \
             mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            self.launch.main(["codex", "app-server"])
            execv.assert_called_once()
            self.assertIn("grok configuration not refreshed (grok conflict)", stderr.getvalue())
            execv.reset_mock()
            self.assertEqual(self.launch.main(["grok", "agent", "stdio"]), 2)
            execv.assert_not_called()
            self.assertIn("refused: grok conflict", stderr.getvalue())

    def test_every_recorded_error_of_the_started_provider_is_a_clean_refusal(self):
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"codex": sys.executable}}))
        failing = lambda home, registry, problems=None: problems.update(codex=TypeError("mcp_servers = false")) or []
        with mock.patch.object(self.launch.Path, "home", return_value=self.home), \
             mock.patch.object(self.launch, "apply", side_effect=failing), \
             mock.patch.object(self.launch.os, "execv") as execv, \
             mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            self.assertEqual(self.launch.main(["codex", "app-server"]), 2)
        execv.assert_not_called()
        self.assertIn("refused: TypeError", stderr.getvalue())

    def test_a_lock_that_cannot_be_opened_is_reported_not_raised(self):
        with mock.patch.object(self.launch, "sync_lock", side_effect=PermissionError("state folder")), \
             mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            failures = self.launch.sync_generated_adapters(self.home)
        self.assertIn("lock", failures)
        self.assertIn("not refreshed", stderr.getvalue())

    def test_explicit_sync_fails_and_a_start_refuses_on_its_own_hook_conflict(self):
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"codex": sys.executable,
                                                                                  "grok": sys.executable}}))
        conflict = {"hooks:grok": ValueError("foreign agents.json")}
        with mock.patch.object(self.launch.Path, "home", return_value=self.home), \
             mock.patch.object(self.launch, "apply", return_value=[]), \
             mock.patch.object(self.launch, "sync_generated_adapters", return_value=conflict), \
             mock.patch.object(self.launch.os, "execv") as execv, \
             mock.patch("sys.stdout", new_callable=StringIO), mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            self.assertEqual(self.launch.main(["sync"]), 2)
            self.assertEqual(self.launch.main(["grok", "agent", "stdio"]), 2)
            execv.assert_not_called()
            self.assertIn("grok hook registration failed", stderr.getvalue())
            self.launch.main(["codex", "app-server"])
            execv.assert_called_once()

    def test_a_link_to_a_wrapper_elsewhere_on_path_is_not_the_provider(self):
        wrappers, alias_dir, real_dir = self.home / ".agents/bin", self.home / "local-bin", self.home / "usr-bin"
        for folder in (wrappers, alias_dir, real_dir):
            folder.mkdir(parents=True)
        for path in (wrappers / "agy", real_dir / "agy"):
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
        (alias_dir / "agy").symlink_to(wrappers / "agy")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([str(alias_dir), str(real_dir)])}):
            self.assertEqual(self.launch.provider_executable(self.home, "agy"), (real_dir / "agy").resolve())
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"agy": str(alias_dir / "agy")}}))
        with self.assertRaisesRegex(self.launch.SyncError, "not a wrapper"):
            self.launch.provider_executable(self.home, "agy")
        # A user's own link in the wrapper folder to the real binary is not a wrapper.
        (wrappers / "grok").symlink_to(real_dir / "agy")
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"grok": str(real_dir / "agy")}}))
        self.assertEqual(self.launch.provider_executable(self.home, "grok"), (real_dir / "agy").resolve())

    def test_role_renderers_follow_installed_providers(self):
        names = lambda: [renderer for renderer, _ in self.launch.role_renderers(self.home)]
        self.assertEqual(names(), ["sync_claude", "sync_codex"])
        (self.home / ".gemini").mkdir()
        (self.home / ".grok").mkdir()
        self.assertEqual(names(), ["sync_claude", "sync_codex", "sync_antigravity", "sync_grok"])

    def test_wrappers_exec_the_launcher_for_their_provider(self):
        for wrapper, provider in (("agy", "agy"), ("agent-grok", "grok")):
            text = (AGENTS / "bin" / wrapper).read_text()
            self.assertIn(f'"$HOME/.agents/launch.py" {provider} "$@"', text)
            self.assertTrue(os.access(AGENTS / "bin" / wrapper, os.X_OK))


if __name__ == "__main__":
    unittest.main()
