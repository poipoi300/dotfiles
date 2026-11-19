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

CARRY = ("Ask the user whether the fork should carry it until upstream merges; if yes: "
         "git config agents.route upstream+carry, then merge the branch into main and push it.")
UPSTREAM_PUSH = ("vcs: never push to upstream. Push your branch to origin (git push -u origin HEAD) "
                 "and open a PR: gh pr create --repo")


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
        self.assert_deny("gh pr create", direct, "vcs: solo-owner/widget is the user's own and takes no PRs. Commit on main and push it: git push origin main.")
        self.assert_deny("gh pr create --repo upstream-owner/widget", direct, "takes no PRs")
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
            "vcs: solo-owner/widget isn't the user's, so never push its main.",
        )
        self.assertIn("git push -u origin HEAD", reason)
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
        self.git("-C", str(fork), "switch", "-c", "feature")
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
        self.assert_deny("gh pr create", fork, "takes no PRs")
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
            self.assert_allow('git log --grep "push the branch"', self.root)
            self.assert_allow('git log --grep "commit the branch"', self.root)
            self.assert_allow('git push "', self.root)
            run.assert_not_called()


    def test_commit_on_a_fork_default_branch_is_denied_with_the_workflow(self):
        fork = self.repository(ahead=False, name="commit-fork")
        carry = self.repository(ahead=True, name="commit-carry")
        direct = self.repository(fork=False, name="commit-direct")
        reason = self.assert_deny('git commit -m "Fix it"', fork, "vcs: this is a fork of upstream-owner/widget")
        self.assertIn("git switch -c <branch> upstream/main", reason)
        self.assertIn("gh pr create --repo upstream-owner/widget", reason)
        self.assertIn("ask the user whether the fork should carry it", reason)
        reason = self.assert_deny("git commit -am x", carry, "gh pr create --repo upstream-owner/widget")
        self.assertIn("git merge <branch> && git push origin main", reason)
        self.assert_deny("git -C commit-fork commit -m x", fork.parent, "this is a fork")
        self.assert_deny("cd commit-fork && rtk git commit --amend --no-edit", fork.parent, "this is a fork")
        for command in ("git commit --dry-run -m x", "git commit -h", "git commit-tree HEAD^{tree}",
                        "GIT_DIR=/x git commit -m x", 'echo "git commit"'):
            self.assert_allow(command, fork)
        self.assert_allow("git commit -m x", direct)
        self.git("-C", str(fork), "switch", "-c", "feature")
        self.assert_allow("git commit -m x", fork)

    def test_commit_during_a_merge_or_rebase_is_allowed(self):
        fork = self.repository(ahead=False, name="commit-merge")
        git_dir = Path(self.git("-C", str(fork), "rev-parse", "--absolute-git-dir").stdout.strip())
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            path = git_dir / name
            if name.startswith("rebase"):
                path.mkdir()
            else:
                path.write_text("0" * 40 + "\n", encoding="utf-8")
            self.assert_allow("git commit --no-edit", fork)
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        self.assert_deny("git commit --no-edit", fork, "this is a fork")

    def test_commit_on_someone_elses_default_branch_is_denied(self):
        work = self.repository(fork=False, name="commit-theirs")
        self.write_hosts_user("other-person")
        reason = self.assert_deny("git commit -m x", work, "vcs: solo-owner/widget isn't the user's")
        self.assertIn("git switch -c <branch>", reason)
        self.git("-C", str(work), "switch", "-c", "feature")
        self.assert_allow("git commit -m x", work)
        self.write_hosts_user(None)
        self.git("-C", str(work), "switch", "main")
        self.assert_allow("git commit -m x", work)

    def post(self, command, cwd, response):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(cwd),
                "tool_input": {"command": command}, "tool_response": response}

    def test_an_opened_upstream_pr_reminds_about_carrying(self):
        fork = self.repository(ahead=False, name="remind-fork")
        carry = self.repository(ahead=True, name="remind-carry")
        direct = self.repository(fork=False, name="remind-direct")
        created = "gh pr create --repo upstream-owner/widget --fill"
        url = "https://github.com/upstream-owner/widget/pull/7\n"
        for response in ({"stdout": url, "stderr": "", "interrupted": False}, url):
            context = vcs_guard.remind(self.post(created, carry, response))
            specific = context["hookSpecificOutput"]
            self.assertEqual(specific["hookEventName"], "PostToolUse")
            self.assertIn("git switch main && git merge <this branch> && git push origin main",
                          specific["additionalContext"])
        context = vcs_guard.remind(self.post(created, fork, {"stdout": url}))
        self.assertIn("Ask the user whether the fork should carry it",
                      context["hookSpecificOutput"]["additionalContext"])
        failed = {"stdout": "", "stderr": "GraphQL: rate limit exceeded"}
        self.assertIsNone(vcs_guard.remind(self.post(created, carry, failed)))
        self.assertIsNone(vcs_guard.remind(self.post("git status", carry, {"stdout": url})))
        self.assertIsNone(vcs_guard.remind(self.post(created, direct, {"stdout": url})))
        other = "https://github.com/origin-owner/widget/pull/3"
        self.assertIsNone(vcs_guard.remind(self.post("gh pr create --repo origin-owner/widget", carry, other)))
        with patch.object(behavior, "design_hook") as design:
            for provider in ("claude", "codex"):
                output = native.dispatch(provider, self.post(created, carry, {"stdout": url}))
                self.assertIn("additionalContext", output["hookSpecificOutput"])
                self.assertEqual(native.dispatch(provider, self.post("ls", carry, "")), {})
            design.assert_not_called()

    def test_bash_post_tool_use_is_registered_for_both_providers(self):
        import register
        for provider in ("claude", "codex"):
            groups = register.handlers(Path.home(), provider)["PostToolUse"]
            self.assertEqual([group["matcher"] for group in groups], ["^(Edit|Write|apply_patch)$", "^Bash$"])


if __name__ == "__main__":
    unittest.main()
