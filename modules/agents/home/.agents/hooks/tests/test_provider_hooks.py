"""Grok and Antigravity hook payloads, replies and registrations.

The payload fixtures are trimmed copies of what Grok Build 1.0.77 and Antigravity CLI 1.5.13 sent
to a logging hook, including from sessions T3 Code started.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.abspath(__file__)).parents[2]
sys.path.insert(0, str(ROOT / "hooks"))
import model_guard
import native
import register

needs_rtk = unittest.skipUnless(shutil.which("rtk"), "rtk is not installed")
DENY = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                               "permissionDecisionReason": "fixture reason"}}


def grok(tool: str, tool_input: dict, event: str = "PreToolUse", **extra) -> dict:
    snake = {"PreToolUse": "pre_tool_use", "PostToolUse": "post_tool_use"}[event]
    return {"hookEventName": snake, "hook_event_name": event, "sessionId": "s", "session_id": "s",
            "cwd": "/tmp", "permissionMode": "bypassPermissions", "toolName": tool, "tool_name": tool,
            "toolInput": tool_input, "tool_input": tool_input, "toolInputTruncated": False, **extra}


def antigravity(name: str, args: dict, event: str = "PreToolUse") -> dict:
    return {"conversationId": "c", "cwd": "/tmp", "hookEventName": event, "modelName": "gemini-4-neon-high",
            "workspacePaths": ["/tmp"], "stepIdx": 2, "toolCall": {"id": "call_1", "name": name, "args": args}}


class GrokTests(unittest.TestCase):
    def test_shell_calls_reach_the_route_guard_as_bash(self):
        payload = grok("run_terminal_command", {"command": "git push origin main", "description": "push"})
        with patch.object(native.vcs_guard, "guard", return_value=DENY) as guard:
            self.assertEqual(native.dispatch("grok", payload), DENY)
        seen = guard.call_args.args[0]
        self.assertEqual(seen["tool_name"], "Bash")
        self.assertEqual(seen["tool_input"]["command"], "git push origin main")

    def test_the_route_guard_judges_the_directory_the_command_runs_in(self):
        payload = grok("run_terminal_command", {"command": "git push origin main", "cwd": "repo"})
        with patch.object(native.vcs_guard, "guard", return_value=None) as guard:
            native.dispatch("grok", payload)
        self.assertEqual(guard.call_args.args[0]["cwd"], "/tmp/repo")
        with patch.object(native.vcs_guard, "guard", return_value=None) as guard:
            native.dispatch("grok", grok("run_terminal_command", {"command": "git status"}))
        self.assertEqual(guard.call_args.args[0]["cwd"], "/tmp")

    def test_claude_registrations_run_by_grok_do_nothing(self):
        # Grok's Claude compatibility runs ~/.claude/settings.json hooks too; it has its own.
        payload = grok("run_terminal_command", {"command": "git push origin main"})
        with patch.object(native.vcs_guard, "guard", return_value=DENY) as guard:
            self.assertEqual(native.dispatch("claude", payload), {})
            with patch.dict(os.environ, {"GROK_HOOK_EVENT": "pre_tool_use"}):
                self.assertEqual(native.dispatch("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                            "tool_input": {"command": "x"}}), {})
        guard.assert_not_called()

    def test_fable_delegates_are_denied_through_use_tool_nesting(self):
        # Grok calls a deferred MCP tool with its arguments nested one level down.
        nested = {"tool_name": "t3-code__delegate_task",
                  "tool_input": {"task": "x", "target": {"providerInstanceId": "claudeAgent", "model": "claude-fable-5-2"}}}
        output = native.dispatch("grok", grok("t3-code__delegate_task", nested))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(output["hookSpecificOutput"]["permissionDecisionReason"], model_guard.REASON)
        output = native.dispatch("grok", grok("spawn_subagent", {"prompt": "x", "model": "fable"}))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        allowed = {**nested, "tool_input": {"task": "x", "target": {"model": "claude-haiku-5-5"}}}
        output = native.dispatch("grok", grok("t3-code__delegate_task", allowed))
        self.assertNotIn("permissionDecision", output.get("hookSpecificOutput", {}))

    @needs_rtk
    def test_rtk_rewrite_keeps_grok_arguments(self):
        payload = grok("run_terminal_command", {"command": "git status", "description": "status"})
        output = native.dispatch("grok", payload)["hookSpecificOutput"]
        self.assertEqual(output["updatedInput"], {"command": "rtk git status", "description": "status"})

    def test_truncated_shell_input_is_denied_because_its_end_is_unseen(self):
        payload = {**grok("run_terminal_command", {"command": "cat <<EOF\n... [truncated]"}), "toolInputTruncated": True}
        output = native.dispatch("grok", payload)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertEqual(output["permissionDecisionReason"], native.GROK_TRUNCATED)
        # Without the field nothing is known: no denial, and no rewrite either.
        unknown = grok("run_terminal_command", {"command": "git status"})
        del unknown["toolInputTruncated"]
        with patch.object(native.vcs_guard, "guard", return_value=None):
            self.assertNotIn("permissionDecision", native.dispatch("grok", unknown).get("hookSpecificOutput", {}))
        nested = {"tool_name": "t3-code__delegate_task", "tool_input": {"task": "x" * 5000, "target": {"model": "fable"}}}
        denied = native.dispatch("grok", {**grok("t3-code__delegate_task", nested), "toolInputTruncated": True})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecisionReason"], model_guard.REASON)

    def test_route_reminder_after_a_shell_call(self):
        payload = grok("run_terminal_command", {"command": "gh pr create"}, "PostToolUse", tool_response="url")
        context = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "carry it"}}
        with patch.object(native.vcs_guard, "remind", return_value=context) as remind:
            self.assertEqual(native.dispatch("grok", payload), context)
        self.assertEqual(remind.call_args.args[0]["tool_name"], "Bash")
        self.assertEqual(native.dispatch("grok", grok("read_file", {"path": "x"}, "PostToolUse")), {})

    def test_grok_usage_injected_once_per_main_session_after_guards_pass(self):
        with tempfile.TemporaryDirectory(prefix="grok-usage-") as td:
            root = Path(td)
            startup = unittest.mock.Mock(return_value="[Budget fixture]")
            with patch.object(native, "ROOT", root), \
                 patch.object(native, "budget", types.SimpleNamespace(startup=startup)), \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WPO_SESSION", None)
                # Denied call must NOT consume the one-shot usage marker.
                denied_call = grok("spawn_subagent", {"prompt": "x", "model": "fable"}, session_id="s-1")
                denied = native.dispatch("grok", denied_call)
                self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                startup.assert_not_called()

                # Subagents skip usage injection.
                subagent = grok("read_file", {"path": "x"}, session_id="s-sub", subagentType="explore")
                self.assertEqual(native.dispatch("grok", subagent), {})
                startup.assert_not_called()

                # First allowed call in main session injects usage; second call does not.
                first = native.dispatch("grok", grok("read_file", {"path": "x"}, session_id="s-1"))
                self.assertEqual(first["hookSpecificOutput"]["hookEventName"], "PreToolUse")
                self.assertEqual(first["hookSpecificOutput"]["additionalContext"], "[Budget fixture]")
                startup.assert_called_once()

                second = native.dispatch("grok", grok("read_file", {"path": "y"}, session_id="s-1"))
                self.assertEqual(second, {})
                startup.assert_called_once()

                # WPO sessions skip injection.
                with patch.dict(os.environ, {"WPO_SESSION": "1"}):
                    self.assertEqual(native.dispatch("grok", grok("read_file", {"path": "z"}, session_id="s-wpo")), {})
                startup.assert_called_once()

                # Interrupted startup (e.g. timeout kill) does not permanently consume the marker.
                flaky = unittest.mock.Mock(side_effect=[KeyboardInterrupt("killed"), "[Budget recovered]"])
                with patch.object(native, "budget", types.SimpleNamespace(startup=flaky)):
                    with self.assertRaises(KeyboardInterrupt):
                        native.dispatch("grok", grok("read_file", {"path": "a"}, session_id="s-retry"))
                    recovered = native.dispatch("grok", grok("read_file", {"path": "b"}, session_id="s-retry"))
                self.assertEqual(recovered["hookSpecificOutput"]["additionalContext"], "[Budget recovered]")
                self.assertEqual(flaky.call_count, 2)

    @needs_rtk
    def test_grok_first_shell_call_combines_rtk_rewrite_and_usage_context(self):
        with tempfile.TemporaryDirectory(prefix="grok-rtk-usage-") as td:
            with patch.object(native, "ROOT", Path(td)), \
                 patch.object(native, "budget", types.SimpleNamespace(startup=lambda cwd: "[Budget fixture]")):
                out = native.dispatch("grok", grok("run_terminal_command", {"command": "git status"}, session_id="s-rtk"))
            specific = out["hookSpecificOutput"]
            self.assertEqual(specific["updatedInput"]["command"], "rtk git status")
            self.assertEqual(specific["additionalContext"], "[Budget fixture]")


class AntigravityTests(unittest.TestCase):
    def run_cli(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-B", str(ROOT / "hooks/native.py"), "antigravity"],
                              check=False, input=stdin, capture_output=True, text=True, timeout=10)

    def test_denials_use_antigravity_decisions(self):
        payload = antigravity("run_command", {"CommandLine": "git push origin main", "WaitMsBeforeAsync": 5000})
        with patch.object(native.vcs_guard, "guard", return_value=DENY) as guard:
            self.assertEqual(native.dispatch("antigravity", payload), {"decision": "deny", "reason": "fixture reason"})
        seen = guard.call_args.args[0]
        self.assertEqual((seen["tool_name"], seen["tool_input"], seen["cwd"]), ("Bash", {"command": "git push origin main"}, "/tmp"))
        with patch.object(native.vcs_guard, "guard", return_value=None) as guard:
            native.dispatch("antigravity", antigravity("run_command", {"CommandLine": "git push", "Cwd": "/srv/repo"}))
        self.assertEqual(guard.call_args.args[0]["cwd"], "/srv/repo")
        delegate = antigravity("mcp_t3-code_delegate_task", {"task": "x", "toolSummary": "s",
                                                             "target": {"model": "claude-fable-5-2"}})
        self.assertEqual(native.dispatch("antigravity", delegate), {"decision": "deny", "reason": model_guard.REASON})

    def test_no_opinion_prints_nothing_because_an_empty_object_denies(self):
        result = self.run_cli(json.dumps(antigravity("run_command", {"CommandLine": "echo hi"})))
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        result = self.run_cli("secret-fixture-not-json")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertNotIn("secret-fixture", result.stderr)

    def test_session_start_injects_usage_as_a_system_message(self):
        start = {**antigravity("x", {}, "SessionStart"), "toolCall": None}
        with patch.object(native, "budget", types.SimpleNamespace(startup=lambda cwd: "Usage: fixture")):
            self.assertEqual(native.dispatch("antigravity", start),
                             {"injectSteps": [{"systemMessage": {"systemMessage": "Usage: fixture"}}]})
        with patch.object(native, "budget", None):
            self.assertEqual(native.dispatch("antigravity", start), {})


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="provider-hooks-")
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        (self.home / ".gemini/config").mkdir(parents=True)
        (self.home / ".grok").mkdir()

    def test_grok_file_is_generated_and_owned(self):
        self.assertIn("grok", register.synchronize(self.home, apply=True))
        path = self.home / ".grok/hooks/agents.json"
        hooks = json.loads(path.read_text())["hooks"]
        self.assertEqual([group["matcher"] for group in hooks["PreToolUse"]], ["*"])
        self.assertEqual(hooks["PreToolUse"][0]["hooks"][0]["timeout"], 20)
        self.assertTrue(hooks["PreToolUse"][0]["hooks"][0]["command"].endswith(" grok || true"))
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], register.GROK_SHELL)
        self.assertEqual(register.synchronize(self.home), [])
        path.write_text(json.dumps({"hooks": {}}))
        with self.assertRaisesRegex(ValueError, "drift"):
            register.synchronize(self.home, apply=True)

    def test_foreign_files_are_not_replaced(self):
        grok_file = self.home / ".grok/hooks/agents.json"
        grok_file.parent.mkdir()
        grok_file.write_text('{"hooks": {"Stop": []}}')
        with self.assertRaisesRegex(ValueError, "not written by this script"):
            register.synchronize(self.home, apply=True)
        grok_file.unlink()
        agy_file = self.home / ".gemini/config/hooks.json"
        agy_file.write_text(json.dumps({register.ANTIGRAVITY_HOOK: {"Stop": []}}))
        with self.assertRaisesRegex(ValueError, "not written by this script"):
            register.synchronize(self.home, apply=True)

    def test_one_providers_refusal_leaves_the_others_registered(self):
        grok_file = self.home / ".grok/hooks/agents.json"
        grok_file.parent.mkdir()
        grok_file.write_text('{"hooks": {"Stop": []}}')
        problems = {}
        self.assertEqual(set(register.synchronize(self.home, apply=True, problems=problems)),
                         {"claude", "codex", "antigravity"})
        self.assertEqual(set(problems), {"grok"})
        self.assertEqual(grok_file.read_text(), '{"hooks": {"Stop": []}}')
        self.assertIn("SessionStart", json.loads((self.home / ".claude/settings.json").read_text())["hooks"])

    def test_malformed_settings_stay_inside_their_provider(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text('{"hooks": []}')
        problems = {}
        register.synchronize(self.home, apply=True, problems=problems)
        self.assertEqual(set(problems), {"claude"})
        self.assertTrue((self.home / ".grok/hooks/agents.json").is_file())

    def test_an_unwritable_destination_fails_only_its_provider(self):
        (self.home / ".grok/hooks").write_text("a file where the folder belongs")
        problems = {}
        self.assertEqual(set(register.synchronize(self.home, apply=True, problems=problems)),
                         {"claude", "codex", "antigravity"})
        self.assertEqual(set(problems), {"grok"})
        state = json.loads((self.home / ".agents/hooks/state/registrations.json").read_text())
        self.assertEqual(set(state), {"claude", "codex", "antigravity"})
        with self.assertRaises(OSError):
            register.synchronize(self.home, apply=True)  # strict

    def test_a_value_json_cannot_write_refuses_before_anything_is_written(self):
        (self.home / ".gemini/config/hooks.json").write_text('{"foreign": NaN}')
        with self.assertRaises(ValueError):
            register.synchronize(self.home, apply=True)
        self.assertFalse((self.home / ".claude/settings.json").exists())
        problems = {}
        register.synchronize(self.home, apply=True, problems=problems)
        self.assertEqual(set(problems), {"antigravity"})
        self.assertTrue((self.home / ".claude/settings.json").exists())

    def test_a_symlinked_hook_file_stays_a_link_through_writes_and_rollback(self):
        target = self.home / "managed-hooks.json"
        target.write_text('{"lint": {}}')
        (self.home / ".gemini/config/hooks.json").symlink_to(target)
        (self.home / ".grok/hooks").write_text("a file where the folder belongs")
        with self.assertRaises(OSError):
            register.synchronize(self.home, apply=True)  # strict: Grok fails, everything is undone
        self.assertTrue((self.home / ".gemini/config/hooks.json").is_symlink())
        self.assertEqual(target.read_text(), '{"lint": {}}')
        (self.home / ".grok/hooks").unlink()
        register.synchronize(self.home, apply=True)
        self.assertTrue((self.home / ".gemini/config/hooks.json").is_symlink())
        self.assertIn(register.ANTIGRAVITY_HOOK, json.loads(target.read_text()))

    def test_rollback_through_a_dangling_link_removes_only_what_it_wrote(self):
        target = self.home / "managed-hooks.json"
        (self.home / ".gemini/config/hooks.json").symlink_to(target)  # dangling
        (self.home / ".grok/hooks").write_text("a file where the folder belongs")
        with self.assertRaises(OSError):
            register.synchronize(self.home, apply=True)
        self.assertTrue((self.home / ".gemini/config/hooks.json").is_symlink())
        self.assertFalse(target.exists())

    def test_rollback_continues_past_a_destination_it_cannot_read(self):
        first, second = self.home / "first.json", self.home / "second.json"
        first.write_bytes(b"new-1")
        second.mkdir()  # replaced by a folder after the write
        register.restore([("claude", first, b"old-1", b"new-1"), ("grok", second, b"old-2", b"new-2")])
        self.assertEqual(first.read_bytes(), b"old-1")

    def test_rollback_keeps_a_save_made_after_this_write(self):
        real = register.atomic_json
        saved = b'{"hooks": {}, "saved": "by the harness"}'

        def harness_saves_then_state_fails(path, value):
            if path.name == "registrations.json":
                (self.home / ".grok/hooks/agents.json").write_bytes(saved)
                raise OSError("disk full")
            real(path, value)

        with patch.object(register, "atomic_json", side_effect=harness_saves_then_state_fails), self.assertRaises(OSError):
            register.synchronize(self.home, apply=True)
        self.assertEqual((self.home / ".grok/hooks/agents.json").read_bytes(), saved)
        self.assertFalse((self.home / ".gemini/config/hooks.json").exists())

    def test_hook_backups_stay_private_whatever_the_umask(self):
        path = self.home / ".gemini/config/hooks.json"
        path.write_text('{"lint": {}}')
        for folder in (".agents/hooks/state", ".claude", ".codex", ".grok/hooks"):
            (self.home / folder).mkdir(parents=True)  # these exist on a real machine
        previous = os.umask(0o177)
        try:
            register.synchronize(self.home, apply=True)
        finally:
            os.umask(previous)
        backups = self.home / ".agents/hooks/backups"
        for item in [backups, *backups.rglob("*")]:
            if item.name == "manifest.json":
                continue
            self.assertEqual(item.stat().st_mode & 0o777, 0o700 if item.is_dir() else 0o600, item)

    def test_a_failed_state_write_rolls_the_registrations_back(self):
        real = register.atomic_json

        def fail_on_state(path, value):
            if path.name == "registrations.json":
                raise OSError("disk full")
            real(path, value)

        with patch.object(register, "atomic_json", side_effect=fail_on_state), self.assertRaises(OSError):
            register.synchronize(self.home, apply=True)
        self.assertFalse((self.home / ".grok/hooks/agents.json").exists())
        self.assertFalse((self.home / ".gemini/config/hooks.json").exists())
        self.assertEqual(set(register.synchronize(self.home, apply=True)), {"claude", "codex", "antigravity", "grok"})

    def test_antigravity_entry_sits_beside_other_named_hooks(self):
        path = self.home / ".gemini/config/hooks.json"
        other = {"lint": {"PostToolUse": [{"matcher": "edit_file", "hooks": [{"command": "./lint.sh"}]}]}}
        path.write_text(json.dumps(other))
        register.synchronize(self.home, apply=True)
        config = json.loads(path.read_text())
        self.assertEqual(config["lint"], other["lint"])
        entry = config[register.ANTIGRAVITY_HOOK]
        self.assertEqual([group["matcher"] for group in entry["PreToolUse"]], ["run_command", "mcp_t3-code_delegate_task"])
        self.assertTrue(entry["SessionStart"][0]["command"].endswith(" antigravity || true"))
        self.assertEqual(register.synchronize(self.home), [])

    def test_antigravity_command_passes_through_when_the_hook_script_is_missing(self):
        command = register.handlers(Path.home(), "antigravity")["SessionStart"][0]["command"]
        result = subprocess.run(["sh", "-c", command], check=False, input="{}", capture_output=True, text=True,
                                timeout=10, env={**os.environ, "HOME": str(self.home)})
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_absent_providers_get_no_registration(self):
        shutil.rmtree(self.home / ".gemini")
        shutil.rmtree(self.home / ".grok")
        self.assertEqual(register.synchronize(self.home, apply=True), ["claude", "codex"])
        self.assertFalse((self.home / ".grok").exists())


if __name__ == "__main__":
    unittest.main()
