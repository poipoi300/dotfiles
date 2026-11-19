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

    def test_external_allows_a_pr_and_denies_the_default_branch(self):
        work = self.repository(fork=False, name="theirs")
        self.write_hosts_user("other-person")
        reason = self.assert_deny(
            "git push origin main", work,
            "fix-route: solo-owner/widget isn't the user's; push a branch and open a PR.",
        )
        self.assertEqual(reason, "fix-route: solo-owner/widget isn't the user's; push a branch and open a PR.")
        self.assert_deny("git push", work, "isn't the user's")
        self.assert_allow("gh pr create", work)
        self.assert_allow("gh pr create --repo solo-owner/widget", work)
        self.assert_allow("git push -u origin feature", work)
        self.git("-C", str(work), "switch", "-c", "feature")
        self.assert_allow("git push", work)
        self.assert_allow("git push origin feature", work)

    def test_unknown_login_allows_every_command(self):
        work = self.repository(fork=False, name="no-login")
        self.write_hosts_user(None)
        self.assert_allow("gh pr create", work)
        self.assert_allow("git push origin main", work)
        self.assert_allow("git push", work)
        self.assert_allow("git push upstream", work)

    def test_heredoc_bodies_are_not_commands(self):
        fork = self.repository(ahead=False, name="heredoc")
        self.assert_allow("git commit -F - <<EOF\ngit push origin main\nEOF", fork)
        self.assert_allow("cat > f <<'EOF'\ngit push upstream main\nEOF", fork)
        self.assert_allow("cat > f <<-'EOF'\n\tgit push upstream main\n\tEOF", fork)
        self.assert_allow("git commit -F - <<EOF\ngit push origin main", fork)
        self.assert_deny("git commit -F - <<EOF\nnote\nEOF\ngit push origin main", fork, CARRY)
        self.assert_deny('echo "<<" && git push origin main', fork, CARRY)

    def test_unknown_origin_default_allows_a_feature_push(self):
        fork = self.repository(ahead=False, name="no-default")
        self.git("-C", str(fork), "switch", "-c", "feature")
        upstream = fix_route.Route(
            "upstream", "config", "main", "upstream-owner/widget", "origin-owner/widget", (),
        )
        with patch.object(fix_route, "decide", return_value=upstream), \
                patch.object(fix_route, "remote_default", return_value=None):
            self.assert_allow("git push", fork)
            self.assert_allow("git push origin feature", fork)
            self.assert_allow("git push origin HEAD", fork)

    def test_dry_run_push_is_allowed(self):
        fork = self.repository(ahead=False, name="dry")
        for command in ("git push --dry-run origin main", "git push -n origin main",
                        "git push origin main --dry-run", "git push -nu origin main",
                        "git push --dry-run upstream"):
            self.assert_allow(command, fork)
        self.assert_deny("git push origin main", fork, CARRY)

    def test_git_dir_and_pushd_make_the_directory_unknown(self):
        fork = self.repository(ahead=False, name="located")
        parent = fork.parent
        for command in ("GIT_DIR=/x git push origin main",
                        "GIT_WORK_TREE= git push upstream",
                        "GIT_WORK_TREE=/x git push origin main",
                        "FOO=bar GIT_DIR=/x git push origin main",
                        "pushd /x && git push origin main",
                        "pushd /x && git push upstream",
                        "popd && git push origin main",
                        "GIT_DIR=/x rtk git push origin main",
                        "rtk GIT_DIR=/tmp git push upstream"):
            self.assert_allow(command, fork)
        self.assert_deny("cd located && git push origin main", parent, CARRY)
        self.assert_deny("FOO=bar git push origin main", fork, CARRY)

    def test_host_owner_repo_counts_as_upstream(self):
        fork = self.repository(ahead=False, name="host-form")
        self.assert_allow("gh pr create --repo github.com/upstream-owner/widget", fork)
        self.assert_allow("gh pr create --repo https://github.com/upstream-owner/widget", fork)
        self.assert_deny("gh pr create --repo github.com/origin-owner/widget", fork,
                         "gh pr create --repo upstream-owner/widget")

    def test_explicit_direct_config_allows_an_upstream_push(self):
        fork = self.repository(ahead=False, name="forced-direct")
        self.assert_deny("git push upstream main", fork, UPSTREAM_PUSH)
        self.git("-C", str(fork), "config", "agents.route", "direct")
        self.assert_allow("git push upstream", fork)
        self.assert_allow("git push upstream main", fork)
        self.assert_deny("gh pr create", fork, "no PR.")
        with patch.object(fix_route, "decide", side_effect=RuntimeError("boom")):
            self.assert_allow("git push upstream", fork)

    def test_bare_push_respects_remote_push_default(self):
        fork = self.repository(ahead=False, name="push-default")
        self.git("-C", str(fork), "switch", "-c", "feature")
        self.git("-C", str(fork), "config", "remote.pushDefault", "upstream")
        self.assert_deny("git push", fork, UPSTREAM_PUSH)
        self.git("-C", str(fork), "config", "branch.feature.pushRemote", "origin")
        self.assert_allow("git push", fork)
        self.git("-C", str(fork), "switch", "main")
        self.git("-C", str(fork), "remote", "add", "other", "https://github.com/other/widget.git")
        self.git("-C", str(fork), "config", "remote.pushDefault", "other")
        self.assert_allow("git push", fork)

    def test_unrelated_commands_do_not_spawn_git(self):
        with patch.object(fix_route, "run") as run:
            self.assert_allow("ls -la", self.root)
            self.assert_allow('echo "git push upstream"', self.root)
            self.assert_allow('git commit -m "push the branch"', self.root)
            self.assert_allow('git push "', self.root)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
