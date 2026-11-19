"""Bash guard: deny a push or pull request that misses the route. No network."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.abspath(__file__)).parents[2]
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent))
import behavior
import fix_route
import native
import test_fix_route
import vcs_guard

CARRY = ("fix-route: this fork doesn't carry changes (fork main matches upstream). "
         "Ask the user; if they want it carried: git config agents.route upstream+carry")
UPSTREAM_PUSH = "fix-route: never push to upstream; push a branch to origin and open a PR."


class GuardTests(test_fix_route.RepoFixture, unittest.TestCase):
    def payload(self, command, cwd):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd),
                "tool_input": {"command": command}}

    def assert_deny(self, command, cwd, fragment):
        decision = vcs_guard.guard(self.payload(command, cwd))
        self.assertIsNotNone(decision, command)
        specific = decision["hookSpecificOutput"]
        self.assertEqual(set(decision), {"hookSpecificOutput"})
        self.assertEqual(set(specific), {"hookEventName", "permissionDecision", "permissionDecisionReason"})
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertIn(fragment, specific["permissionDecisionReason"])
        return specific["permissionDecisionReason"]

    def assert_allow(self, command, cwd):
        self.assertIsNone(vcs_guard.guard(self.payload(command, cwd)), command)

    def test_shell_splitting_keeps_quotes_and_tracks_cd(self):
        base = Path("/work")
        quoted = vcs_guard.parse_script('git commit -m "a && b" && git push origin main', base)
        self.assertEqual(quoted[1], (base, ["git", "push", "origin", "main"]))
        changed = vcs_guard.parse_script('cd "my work" && git push', base)
        self.assertEqual(changed[1][0], base / "my work")
        piped = vcs_guard.parse_script("cd sub | git push", base)
        self.assertEqual(piped[1][0], base)
        redirected = vcs_guard.parse_script("git push origin main 2>&1", base)
        self.assertEqual(redirected[0][1], ["git", "push", "origin", "main"])
        self.assertIsNone(vcs_guard.parse_script('git push "', base))
        self.assertEqual(vcs_guard.parse_script("FOO=bar rtk git push upstream", base)[0][1],
                         ["git", "push", "upstream"])

    def test_deny_rules_and_allowed_pushes(self):
        direct = self.repository(fork=False, name="direct")
        fork = self.repository(ahead=False, name="fork")
        carry = self.repository(ahead=True, name="carry")
        self.assert_deny("gh pr create", direct, "solo-owner/widget takes direct pushes: commit on main and push; no PR.")
        self.assert_deny("gh pr create --repo upstream-owner/widget", direct, "no PR.")
        for command in ("gh pr create", "gh pr create --repo origin-owner/widget",
                        "gh pr create -R origin-owner/widget", "gh pr create --repo=https://github.com/origin-owner/widget"):
            self.assert_deny(command, fork, "gh pr create --repo upstream-owner/widget")
        for command in ("gh pr create --repo upstream-owner/widget", "gh pr create -R upstream-owner/widget",
                        "gh -R upstream-owner/widget pr create",
                        "gh pr create --repo=https://github.com/upstream-owner/widget.git"):
            self.assert_allow(command, fork)
        self.assert_allow("gh pr create --repo upstream-owner/widget", carry)
        for command in ("git push upstream", "git push upstream main",
                        "git push https://github.com/upstream-owner/widget.git",
                        "git push git@github.com:upstream-owner/widget.git",
                        "git push https://github.com/upstream-owner/widget"):
            self.assert_deny(command, fork, UPSTREAM_PUSH)
        self.assert_deny("git push upstream", direct, UPSTREAM_PUSH)
        for command in ("git push origin main", "git push origin HEAD:main", "git push origin x:refs/heads/main",
                        "git push origin +main", "git push", "git push origin HEAD",
                        "git push origin feature:main", 'git push origin "main"', "git push origin main 2>&1"):
            self.assert_deny(command, fork, CARRY)
        for command in ("git push origin feature", "git push -u origin HEAD:feature",
                        "git push origin --delete main", "git push origin refs/tags/v1",
                        "git push origin tag v1", "git push --tags", "git push origin main:feature"):
            self.assert_allow(command, fork)
        self.git("-C", str(fork), "switch", "-c", "feature")
        self.assert_allow("git push", fork)
        self.assert_allow("git push origin feature", fork)
        self.assert_deny("git push origin main", fork, CARRY)
        self.git("-C", str(fork), "switch", "main")
        self.assert_allow("git push origin main", direct)
        self.assert_allow("git push origin main", carry)
        self.assert_allow("git push", carry)

    def test_cd_git_directory_rtk_and_fail_open(self):
        fork = self.repository(ahead=False, name="nested")
        parent = fork.parent
        self.assert_deny("cd nested && git push upstream", parent, UPSTREAM_PUSH)
        self.assert_deny("git -C nested push upstream", parent, UPSTREAM_PUSH)
        self.assert_deny('cd "nested" && git -C . push origin main', parent, CARRY)
        self.assert_deny("rtk git push upstream", fork, UPSTREAM_PUSH)
        self.assert_deny("FOO=bar rtk git push upstream", fork, UPSTREAM_PUSH)
        self.assert_allow("git push origin feature", fork)
        self.assert_allow("ls -la", fork)
        self.assert_allow('echo "git push upstream"', fork)
        self.assert_allow('git push "', fork)
        self.assert_allow("git status", fork)
        self.assert_allow("git push upstream", self.root)
        self.assert_allow("gh pr create", self.root)
        self.git("-C", str(fork), "config", "agents.route", "sideways")
        # A bad agents.route is not a route, but the remote name is still upstream.
        self.assert_deny("git push upstream", fork, UPSTREAM_PUSH)
        self.assert_allow("gh pr create", fork)
        with patch.object(fix_route, "decide", side_effect=RuntimeError("boom")):
            self.assert_deny("git push upstream", fork, UPSTREAM_PUSH)
            self.assert_allow("gh pr create", fork)
            self.assert_allow("git push origin main", fork)

    def test_deny_precedes_rewrite_for_both_providers(self):
        fork = self.repository(ahead=False, name="dispatch")
        payload = self.payload("git push upstream", fork)
        with patch.object(behavior, "rewrite_shell") as rewrite:
            for provider in ("claude", "codex"):
                decision = native.dispatch(provider, payload)
                self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
            rewrite.assert_not_called()
        allowed = self.payload("git status", fork)
        with patch.object(behavior, "rewrite_shell", return_value={"rewritten": True}) as rewrite:
            self.assertEqual(native.dispatch("codex", allowed), {"rewritten": True})
            rewrite.assert_called_once()
            rewrite.reset_mock()
            self.assertEqual(native.dispatch("claude", allowed), {})
            rewrite.assert_not_called()

    def test_unrelated_commands_do_not_spawn_git(self):
        with patch.object(fix_route, "run") as run:
            self.assert_allow("ls -la", self.root)
            self.assert_allow('echo "git push upstream"', self.root)
            self.assert_allow('git commit -m "push the branch"', self.root)
            self.assert_allow('git push "', self.root)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
