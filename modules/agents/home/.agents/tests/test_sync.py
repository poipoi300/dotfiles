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


if __name__ == "__main__":
    unittest.main()
