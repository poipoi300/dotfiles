import importlib.util
import json
import os
import tempfile
import tomllib
import unittest
import subprocess
from pathlib import Path
from unittest import mock

# abspath, not resolve(): run from ~/.agents/tests, not from the dotfiles repository the links point into.
MODULE = Path(os.path.abspath(__file__)).parents[1] / "sync.py"
spec = importlib.util.spec_from_file_location("agent_common_sync", MODULE)
sync = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = sync
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name)
        (self.home / ".agents").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".claude").mkdir()
        self.registry = self.home / ".agents/mcp.json"
        self.registry.write_text(json.dumps({"mcpServers": {"sample": {"command": "sample", "args": []}}}))
        self.claude = self.home / ".claude.json"
        self.claude.write_text(json.dumps({"metadata": {"keep": True}, "mcpServers": {"unmanaged": {"command": "keep"}}}))
        self.codex = self.home / ".codex/config.toml"
        self.prefix = '# Native comment\nmodel = "existing-model"\n\n[mcp_servers.node_repl]\ncommand = "keep"\n'
        self.codex.write_text(self.prefix)

    def load(self, path):
        return json.loads(path.read_text())

    def test_translation_for_environment_and_http_headers(self):
        registry = {"mcpServers": {
            "stdio": {"type": "stdio", "command": "tool", "args": ["mcp"], "env": {"TOKEN": "${TOKEN}", "PORT": "9000"}},
            "http": {"type": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "${MCP_AUTH}", "X-Mode": "local"}},
        }}
        codex = sync.native_servers(registry, "codex")
        self.assertEqual(codex["stdio"]["env_vars"], ["TOKEN"])
        self.assertEqual(codex["stdio"]["env"], {"PORT": "9000"})
        self.assertEqual(codex["http"]["env_http_headers"], {"Authorization": "MCP_AUTH"})
        self.assertEqual(codex["http"]["http_headers"], {"X-Mode": "local"})
        self.assertEqual(sync.native_servers(registry, "claude"), registry["mcpServers"])
        self.assertEqual(tomllib.loads(sync.toml_block(codex))["mcp_servers"], codex)

    def test_provider_env_applies_only_to_its_provider(self):
        registry = {"mcpServers": {"tool": {"command": "x", "env": {"A": "1"},
                                            "providerEnv": {"claude": {"MODE": "lazy"}}}}}
        claude = sync.native_servers(registry, "claude")["tool"]
        codex = sync.native_servers(registry, "codex")["tool"]
        self.assertEqual(claude["env"], {"A": "1", "MODE": "lazy"})
        self.assertNotIn("providerEnv", claude)
        self.assertEqual(codex["env"], {"A": "1"})
        self.assertEqual(registry["mcpServers"]["tool"]["env"], {"A": "1"})

    def test_translation_rejects_unsupported_or_ambiguous_configuration(self):
        entries = [
            {"command": "x", "providerEnv": {"zed": {"A": "1"}}},
            {"command": "x", "providerEnv": {"claude": {"A": 1}}},
            {"command": "x", "env": {"TOKEN": "prefix-${TOKEN}"}},
            {"command": "x", "env": {"TOKEN": "${OTHER}"}},
            {"type": "http", "url": "${URL}"},
            {"command": "x", "cwd": "unknown"},
            {"type": "sse", "url": "https://example.test"},
        ]
        for server in entries:
            with self.subTest(server=server), self.assertRaises(sync.SyncError):
                sync.native_servers({"mcpServers": {"sample": server}}, "codex")

    def test_dry_run_has_no_side_effects_then_sync_preserves_unmanaged_settings(self):
        before = self.codex.read_bytes()
        changes = sync.prepare(self.home, self.registry)[0]
        self.assertTrue(changes)
        self.assertEqual(self.codex.read_bytes(), before)
        self.assertFalse((self.home / ".agents/state").exists())
        sync.apply(self.home, self.registry)
        self.assertTrue(self.codex.read_text().startswith(self.prefix))
        self.assertEqual(tomllib.loads(self.codex.read_text())["mcp_servers"]["node_repl"], {"command": "keep"})
        self.assertEqual(self.load(self.claude)["metadata"], {"keep": True})
        self.assertEqual(self.load(self.claude)["mcpServers"]["unmanaged"], {"command": "keep"})
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])
        manifests = list((self.home / ".agents/backups").glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1)
        self.assertTrue(any(x["path"] == str(self.claude) for x in self.load(manifests[0])["files"]))

    def test_external_native_changes_are_refused_without_overwriting_any_file(self):
        sync.apply(self.home, self.registry)
        original = self.codex.read_bytes()
        changed = self.load(self.claude)
        changed["mcpServers"]["sample"]["command"] = "external-edit"
        self.claude.write_text(json.dumps(changed))
        with self.assertRaisesRegex(sync.SyncError, "Drift"):
            sync.apply(self.home, self.registry)
        self.assertEqual(self.codex.read_bytes(), original)
        self.assertEqual(self.load(self.claude)["mcpServers"]["sample"]["command"], "external-edit")

    def test_codex_drift_and_unmanaged_name_collision_are_refused(self):
        self.codex.write_text(self.prefix + '\n[mcp_servers.sample]\ncommand = "different"\n')
        with self.assertRaisesRegex(sync.SyncError, "collision"):
            sync.prepare(self.home, self.registry)
        self.codex.write_text(self.prefix)
        sync.apply(self.home, self.registry)
        self.codex.write_text(self.codex.read_text().replace('command = "sample"', 'command = "external"'))
        with self.assertRaisesRegex(sync.SyncError, "Drift"):
            sync.prepare(self.home, self.registry)

    def test_codex_tables_appended_inside_the_trailing_block_are_moved_out_and_kept(self):
        # The managed block is written last, so Codex appends its own tables (hooks.state) before END.
        sync.apply(self.home, self.registry)
        appended = '[hooks.state."/x/hooks.json:stop:0:0"]\ntrusted_hash = "abc"\n'
        text = self.codex.read_text()
        self.codex.write_text(text.replace(sync.END, appended + sync.END))
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        outside = text[:text.index(sync.BEGIN)] + text[text.index(sync.END) + len(sync.END):]
        self.assertIn(appended, outside)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["hooks"]["state"]["/x/hooks.json:stop:0:0"], {"trusted_hash": "abc"})
        self.assertEqual(parsed["model"], "existing-model")
        self.assertEqual(sorted(parsed["mcp_servers"]), ["node_repl", "sample"])
        sync.apply(self.home, self.registry)
        self.assertEqual(self.codex.read_text(), text)

    def test_codex_keys_before_the_first_table_in_the_block_are_still_refused(self):
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        self.codex.write_text(text.replace(sync.BEGIN, sync.BEGIN + 'stray = "x"\n'))
        with self.assertRaisesRegex(sync.SyncError, "Unexpected settings"):
            sync.prepare(self.home, self.registry)

    def test_registry_update_and_removal_touch_only_owned_entries(self):
        sync.apply(self.home, self.registry)
        self.registry.write_text(json.dumps({"mcpServers": {"sample": {"command": "updated"}}}))
        sync.apply(self.home, self.registry)
        self.assertEqual(self.load(self.claude)["mcpServers"]["sample"]["command"], "updated")
        self.registry.write_text(json.dumps({"mcpServers": {}}))
        sync.apply(self.home, self.registry)
        self.assertEqual(list(self.load(self.claude)["mcpServers"]), ["unmanaged"])
        self.assertEqual(list(tomllib.loads(self.codex.read_text())["mcp_servers"]), ["node_repl"])

    def test_existing_equivalent_claude_server_is_adopted_and_plugin_disable_is_owned(self):
        current = self.load(self.claude)
        current["mcpServers"]["sample"] = {"command": "sample", "args": []}
        self.claude.write_text(json.dumps(current))
        settings = self.home / ".claude/settings.json"
        settings.write_text(json.dumps({"hooks": {"keep": []}, "enabledPlugins": {"context7@official": True, "other": True}}))
        self.registry.write_text(json.dumps({"mcpServers": {"sample": {"command": "sample", "args": []}}, "claudePluginOverrides": {"context7@official": False}}))
        sync.apply(self.home, self.registry)
        self.assertEqual(self.load(settings)["hooks"], {"keep": []})
        self.assertEqual(self.load(settings)["enabledPlugins"], {"context7@official": False, "other": True})
        changed = self.load(settings)
        changed["enabledPlugins"]["context7@official"] = True
        settings.write_text(json.dumps(changed))
        with self.assertRaisesRegex(sync.SyncError, "Drift"):
            sync.prepare(self.home, self.registry)

    def test_failed_write_rolls_back_and_releases_lock(self):
        before = self.claude.read_bytes()
        original = sync.atomic_write
        count = 0

        def fail_once(path, content):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("test failure")
            original(path, content)

        with mock.patch.object(sync, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
            sync.apply(self.home, self.registry)
        self.assertEqual(self.claude.read_bytes(), before)
        self.assertEqual(self.codex.read_text(), self.prefix)
        # A persistent lock file is harmless: the OS lock must have been released.
        sync.apply(self.home, self.registry)

    def test_shared_skill_links_resolve_and_real_folder_collisions_are_refused(self):
        source = self.home / ".agents/skills/example"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: example\ndescription: Example\n---\n")
        link = self.home / ".claude/skills/example"
        self.assertEqual(len(sync.plan_skill_links(self.home)), 1)
        sync.apply(self.home, self.registry)
        self.assertTrue(link.samefile(source))
        self.assertEqual(sync.plan_skill_links(self.home), [])
        sync.remove_created_link(sync.SkillLink(link, source))
        link.mkdir()
        (link / "SKILL.md").write_text("different")
        with self.assertRaisesRegex(sync.SyncError, "collision"):
            sync.apply(self.home, self.registry)
        self.assertEqual((link / "SKILL.md").read_text(), "different")

    def test_parallel_provider_startup_syncs_serialize(self):
        processes = [subprocess.Popen(
            [sys.executable, "-I", "-B", str(MODULE), "sync", "--home", str(self.home)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(6)]
        for process in processes:
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])
        self.assertEqual(len(list((self.home / ".agents/backups").iterdir())), 1)

    def test_provider_wrapper_is_silent_and_replaces_itself_with_the_provider(self):
        launch = self.load_launch()
        (self.home / ".agents/machine.json").write_text(json.dumps({"providers": {"codex": sys.executable}}))
        from io import StringIO
        with mock.patch.object(launch.Path, "home", return_value=self.home), \
             mock.patch.object(launch, "apply", return_value=[]) as apply, \
             mock.patch.object(launch, "sync_generated_adapters", return_value={}) as adapters, \
             mock.patch.object(launch.os, "execv") as execv, \
             mock.patch("sys.stdout", new_callable=StringIO) as stdout:
            launch.main(["codex", "app-server", "--config", 'value with "quotes"'])
        self.assertEqual(stdout.getvalue(), "")
        executable = str(Path(sys.executable).resolve())
        execv.assert_called_once_with(executable, [executable, "app-server", "--config", 'value with "quotes"'])
        apply.assert_called_once()
        adapters.assert_called_once_with(self.home)

    def test_provider_is_found_on_path_but_never_the_wrapper_itself(self):
        launch = self.load_launch()
        bin_dir, wrapper_dir = self.home / "bin", self.home / ".agents/bin"
        for directory in (bin_dir, wrapper_dir):
            directory.mkdir(parents=True, exist_ok=True)
            tool = directory / "claude"
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
        path = os.pathsep.join([str(wrapper_dir), str(bin_dir)])
        with mock.patch.dict(os.environ, {"PATH": path}):
            self.assertEqual(launch.provider_executable(self.home, "claude"), (bin_dir / "claude").resolve())
        with mock.patch.dict(os.environ, {"PATH": str(wrapper_dir)}), self.assertRaisesRegex(launch.SyncError, "No claude"):
            launch.provider_executable(self.home, "claude")

    def test_sync_mode_updates_without_exec_and_works_without_a_registry(self):
        launch = self.load_launch()
        self.registry.unlink()
        source = self.home / ".agents/skills/example"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: example\ndescription: Example\n---\n")
        codex_before = self.codex.read_bytes()
        from io import StringIO
        with mock.patch.object(launch.Path, "home", return_value=self.home), \
             mock.patch.object(launch, "sync_generated_adapters", return_value={}) as adapters, \
             mock.patch.object(launch.os, "execv") as execv, mock.patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(launch.main(["sync"]), 0)
        execv.assert_not_called()
        adapters.assert_called_once_with(self.home)
        self.assertTrue((self.home / ".claude/skills/example").samefile(source))
        self.assertEqual(self.codex.read_bytes(), codex_before)  # no empty managed block is added

    def load_launch(self):
        launch_spec = importlib.util.spec_from_file_location("agent_common_launch", MODULE.with_name("launch.py"))
        launch = importlib.util.module_from_spec(launch_spec)
        launch_spec.loader.exec_module(launch)
        return launch

    def test_startup_regenerates_role_agents_and_hooks_without_stdout(self):
        import shutil
        from io import StringIO
        for name in ("roles", "hooks", "usage"):
            if not (MODULE.parent / name).is_dir():
                continue  # usage/ ships in the private module
            shutil.copytree(MODULE.parent / name, self.home / ".agents" / name,
                            ignore=shutil.ignore_patterns("archive", "state", "backups", "*-state.json", "__pycache__"))
        launch = self.load_launch()
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout, \
             mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            launch.sync_generated_adapters(self.home)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        roles = len(json.loads((MODULE.parent / "roles/catalog.json").read_text())["roles"])
        self.assertEqual(len(list((self.home / ".claude/agents").glob("*.md"))), roles)
        self.assertEqual(len(list((self.home / ".codex/agents").glob("*.toml"))), roles)
        self.assertIn("SessionStart", json.loads((self.home / ".codex/hooks.json").read_text())["hooks"])

    def test_startup_adapter_failure_is_reported_on_stderr_and_not_fatal(self):
        from io import StringIO
        launch = self.load_launch()
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout, \
             mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            launch.sync_generated_adapters(self.home)  # this home has no role sources
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("not refreshed", stderr.getvalue())

    def _add_shared_skill_creator(self):
        skill = self.home / ".agents/skills/skill-creator"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("---\nname: skill-creator\ndescription: When the user asks to write or test a skill.\n---\n")
        return skill

    def test_shared_skill_creator_links_and_reserved_names_stay_rejected(self):
        (self.home / ".gemini").mkdir()
        skill = self._add_shared_skill_creator()
        sync.apply(self.home, self.registry)
        self.assertTrue((self.home / ".claude/skills/skill-creator").samefile(skill))
        self.assertTrue((self.home / ".gemini/config/skills/skill-creator").samefile(skill))
        for reserved in ("synced", "skill-installer"):
            bad = self.home / ".agents/skills" / reserved
            bad.mkdir(parents=True)
            (bad / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n")
            with self.assertRaisesRegex(sync.SyncError, "conflicts with runtime-managed"):
                sync.plan_skill_links(self.home)
            import shutil
            shutil.rmtree(bad)

    def test_claude_skill_creator_override_coexists_with_plugins_adopts_detects_drift_and_removes(self):
        settings = self.home / ".claude/settings.json"
        settings.write_text(json.dumps({
            "hooks": {"keep": []},
            "enabledPlugins": {"context7@official": True, "other": True},
            "skillOverrides": {"custom:keep": "on"},
        }))
        self.registry.write_text(json.dumps({
            "mcpServers": {"sample": {"command": "sample", "args": []}},
            "claudePluginOverrides": {"context7@official": False},
        }))
        skill = self._add_shared_skill_creator()
        sync.apply(self.home, self.registry)
        data = self.load(settings)
        self.assertEqual(data["enabledPlugins"], {"context7@official": False, "other": True})
        self.assertEqual(data["skillOverrides"], {"custom:keep": "on", sync.CLAUDE_SKILL_CREATOR_KEY: "off"})
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])

        # External drift is refused.
        data["skillOverrides"][sync.CLAUDE_SKILL_CREATOR_KEY] = "on"
        settings.write_text(json.dumps(data))
        with self.assertRaisesRegex(sync.SyncError, "Drift"):
            sync.prepare(self.home, self.registry)
        data["skillOverrides"][sync.CLAUDE_SKILL_CREATOR_KEY] = "off"
        settings.write_text(json.dumps(data))

        # Removing shared skill-creator removes only our owned override.
        (skill / "SKILL.md").unlink()
        sync.apply(self.home, self.registry)
        self.assertEqual(self.load(settings)["skillOverrides"], {"custom:keep": "on"})

        # Pre-existing conflicting "on" is refused; identical "off" is adopted.
        (skill / "SKILL.md").write_text("---\nname: skill-creator\ndescription: x\n---\n")
        current = self.load(settings)
        current["skillOverrides"] = {sync.CLAUDE_SKILL_CREATOR_KEY: "on"}
        settings.write_text(json.dumps(current))
        with self.assertRaisesRegex(sync.SyncError, "collides"):
            sync.prepare(self.home, self.registry)
        current["skillOverrides"] = {sync.CLAUDE_SKILL_CREATOR_KEY: "off"}
        settings.write_text(json.dumps(current))
        sync.apply(self.home, self.registry)
        self.assertEqual(self.load(settings)["skillOverrides"], {sync.CLAUDE_SKILL_CREATOR_KEY: "off"})

    def test_codex_skill_creator_disable_block_lifecycle_invariance_and_conflicts(self):
        # Preserve unmanaged [skills] keys and unmanaged [[skills.config]] entries.
        self.codex.write_text(
            self.prefix
            + '\n[skills]\nextra = true\n\n[[skills.config]]\npath = "~/.codex/skills/other/SKILL.md"\nenabled = true\n'
        )
        skill = self._add_shared_skill_creator()
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        self.assertIn(sync.SKILLS_BEGIN, text)
        self.assertIn(sync.BEGIN, text)
        self.assertLess(text.index(sync.SKILLS_BEGIN), text.index(sync.BEGIN))
        parsed = tomllib.loads(text)
        self.assertTrue(parsed["skills"]["extra"])
        self.assertEqual(len(parsed["skills"]["config"]), 2)
        self.assertEqual(sync.prepare(self.home, self.registry)[0], [])

        # Foreign table appended inside SKILLS block is moved out and kept.
        appended = '[hooks.state."/y/hooks.json:stop:0:0"]\ntrusted_hash = "def"\n'
        self.codex.write_text(text.replace(sync.SKILLS_END, appended + sync.SKILLS_END))
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        self.assertIn(appended, text[:text.index(sync.SKILLS_BEGIN)])
        self.assertEqual(tomllib.loads(text)["hooks"]["state"]["/y/hooks.json:stop:0:0"], {"trusted_hash": "def"})

        # Removing skill-creator removes only the SKILLS block and keeps unmanaged [skills].
        (skill / "SKILL.md").unlink()
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        self.assertNotIn(sync.SKILLS_BEGIN, text)
        self.assertIn(sync.BEGIN, text)
        parsed = tomllib.loads(text)
        self.assertTrue(parsed["skills"]["extra"])
        self.assertEqual(parsed["skills"]["config"], [{"path": "~/.codex/skills/other/SKILL.md", "enabled": True}])

        # Also works cleanly when config had NO unmanaged [skills] at all and NO MCP registry.
        self.registry.write_text(json.dumps({"mcpServers": {}}))
        self.codex.write_text(self.prefix)
        (self.home / ".agents/state/sync-state.json").unlink()
        (skill / "SKILL.md").write_text("---\nname: skill-creator\ndescription: x\n---\n")
        sync.apply(self.home, self.registry)
        text = self.codex.read_text()
        self.assertIn(sync.SKILLS_BEGIN, text)
        self.assertNotIn(sync.BEGIN, text)
        (skill / "SKILL.md").unlink()
        sync.apply(self.home, self.registry)
        self.assertEqual(self.codex.read_text(), self.prefix)

        # Unmanaged identical disable (even with expanded home path) is adopted; conflicting enabled=true is refused.
        expanded = str(self.home / ".codex/skills/.system/skill-creator/SKILL.md")
        (skill / "SKILL.md").write_text("---\nname: skill-creator\ndescription: x\n---\n")
        self.codex.write_text(self.prefix + f'\n[[skills.config]]\npath = {json.dumps(expanded)}\nenabled = true\n')
        with self.assertRaisesRegex(sync.SyncError, "collision"):
            sync.prepare(self.home, self.registry)
        self.codex.write_text(self.prefix + f'\n[[skills.config]]\npath = {json.dumps(expanded)}\nenabled = false\n')
        sync.apply(self.home, self.registry)
        self.assertNotIn(sync.SKILLS_BEGIN, self.codex.read_text())

    def test_stale_skill_links_pruned_reported_and_restored_on_rollback(self):
        (self.home / ".gemini/config/skills").mkdir(parents=True)
        (self.home / ".claude/skills").mkdir(parents=True)
        # Missing ~/.agents/skills must never trigger pruning.
        dangling_claude = self.home / ".claude/skills/old-skill"
        dangling_claude.symlink_to(self.home / ".agents/skills/old-skill")
        self.assertEqual(sync.plan_stale_skill_links(self.home), [])

        # Once ~/.agents/skills exists, broken absolute and relative links pointing into it are pruned.
        (self.home / ".agents/skills").mkdir(parents=True)
        dangling_agy = self.home / ".gemini/config/skills/old-rel"
        dangling_agy.symlink_to(Path("../../../.agents/skills/old-rel"))
        unrelated_dangling = self.home / ".claude/skills/external-broken"
        unrelated_dangling.symlink_to(self.home / "somewhere-else/missing")
        real_dir = self.home / ".claude/skills/custom-local"
        real_dir.mkdir()
        live_skill = self.home / ".agents/skills/live-skill"
        live_skill.mkdir()
        (live_skill / "SKILL.md").write_text("---\nname: live-skill\ndescription: x\n---\n")

        planned = sync.plan_stale_skill_links(self.home)
        self.assertEqual({s.path for s in planned}, {dangling_claude, dangling_agy})

        # Rollback restores removedLinks cleanly after removing created links.
        dangling_claude.unlink()
        sync.undo([], [], [sync.StaleSkillLink(dangling_claude, self.home / ".agents/skills/old-skill", "claude")])
        self.assertTrue(os.path.lexists(dangling_claude))
        self.assertTrue(dangling_claude.is_symlink())

        # Full apply prunes owned dangling links, records them in manifest, and leaves unrelated/real entries untouched.
        sync.apply(self.home, self.registry)
        self.assertFalse(os.path.lexists(dangling_claude))
        self.assertFalse(os.path.lexists(dangling_agy))
        self.assertTrue(os.path.lexists(unrelated_dangling))
        self.assertTrue(real_dir.is_dir())
        self.assertTrue((self.home / ".claude/skills/live-skill").samefile(live_skill))
        manifest = self.load(sorted((self.home / ".agents/backups").glob("*/manifest.json"))[-1])
        self.assertEqual({entry["path"] for entry in manifest["removedLinks"]}, {str(dangling_claude), str(dangling_agy)})

    def test_codex_directory_links_created_for_per_file_symlinked_skills_and_pruned_when_stale(self):
        # Simulate arcus per-file symlinks: real directory in ~/.agents/skills/<name>,
        # with SKILL.md symlinked into a dotfiles source directory containing regular files.
        repo_skills = self.home / "dotfiles-skills"
        real_skill = repo_skills / "arcus-skill"
        real_skill.mkdir(parents=True)
        (real_skill / "SKILL.md").write_text("---\nname: arcus-skill\ndescription: Arcus skill.\n---\n")
        deployed = self.home / ".agents/skills/arcus-skill"
        deployed.mkdir(parents=True)
        (deployed / "SKILL.md").symlink_to(real_skill / "SKILL.md")

        # Regular (non-symlinked) SKILL.md is discovered natively by Codex and gets no ~/.codex/skills link.
        regular_skill = self.home / ".agents/skills/regular-skill"
        regular_skill.mkdir(parents=True)
        (regular_skill / "SKILL.md").write_text("---\nname: regular-skill\ndescription: Regular.\n---\n")

        sync.apply(self.home, self.registry)
        codex_link = self.home / ".codex/skills/arcus-skill"
        self.assertTrue(codex_link.is_symlink())
        self.assertTrue(codex_link.samefile(real_skill))
        self.assertFalse(os.path.lexists(self.home / ".codex/skills/regular-skill"))
        self.assertEqual(sync.plan_skill_links(self.home), [])

        # Dangling Codex link pointing into the same resolved dotfiles skills root is pruned.
        stale_codex = self.home / ".codex/skills/renamed-skill"
        stale_codex.symlink_to(repo_skills / "renamed-skill")
        self.assertEqual({s.path for s in sync.plan_stale_skill_links(self.home)}, {stale_codex})
        sync.apply(self.home, self.registry)
        self.assertFalse(os.path.lexists(stale_codex))


if __name__ == "__main__":
    unittest.main()
