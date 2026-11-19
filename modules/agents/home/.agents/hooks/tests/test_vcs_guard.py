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
        # Offline, a fork without an upstream remote looks direct: a PR to another repo goes through.
        self.assert_allow("gh pr create --repo upstream-owner/widget", direct)
        self.assert_deny("gh pr create --repo solo-owner/widget", direct, "git remote add upstream")
        self.git("-C", str(direct), "config", "agents.route", "direct")
        self.assert_deny("gh pr create --repo upstream-owner/widget", direct, "takes no PRs")
        self.git("-C", str(direct), "config", "--unset", "agents.route")
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
        # A bad agents.route is denied with the router's error, even for an upstream push.
        self.assert_deny("git push upstream", fork, "agents.route must be direct, pr, upstream, or upstream+carry")
        self.assert_deny("gh pr create", fork, "agents.route must be direct, pr, upstream, or upstream+carry")
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

    def test_pr_route_allows_a_pr_and_a_feature_push_and_denies_the_default_branch(self):
        work = self.repository(fork=False, name="own-pr")
        self.assert_deny("gh pr create", work, "takes no PRs")
        self.git("-C", str(work), "config", "agents.route", "pr")
        self.assert_allow("git push -u origin feature", work)
        self.assert_allow("git push origin HEAD:feature", work)
        self.assert_allow("git push --dry-run origin main", work)
        for command in ("git push origin main", "git push", "git push origin HEAD", "git push origin HEAD:main"):
            reason = self.assert_deny(command, work, "vcs: solo-owner/widget integrates through pull requests, "
                                                     "so never push its main.")
            self.assertIn("git push -u origin HEAD", reason)
            self.assertIn("gh pr create", reason)
        self.git("-C", str(work), "switch", "-c", "feature")
        self.assert_allow("gh pr create", work)
        self.assert_allow("gh pr create --repo solo-owner/widget --fill", work)
        self.assert_allow("git push", work)
        self.assert_allow("git push origin feature", work)
        self.assert_deny("git push origin main", work, "never push its main")
        self.git("-C", str(work), "config", "--unset", "agents.route")
        self.assert_deny("gh pr create", work, "takes no PRs")
        self.git("-C", str(work), "switch", "main")
        self.assert_allow("git push origin main", work)

    def test_pr_route_denies_a_commit_on_the_default_branch_until_a_branch_exists(self):
        work = self.repository(fork=False, name="own-pr-commit")
        self.assert_allow("git commit -m x", work)
        self.git("-C", str(work), "config", "agents.route", "pr")
        reason = self.assert_deny("git commit -m x", work,
                                  "vcs: solo-owner/widget integrates through pull requests, "
                                  "so nothing is committed on its main.")
        self.assertIn("git switch -c <branch>", reason)
        self.assertIn("git push -u origin HEAD", reason)
        self.assertIn("gh pr create", reason)
        self.assert_allow("git commit --dry-run -m x", work)
        self.assert_allow("git switch -c fix && git commit -m x", work)
        self.git("-C", str(work), "switch", "-c", "feature")
        self.assert_allow("git commit -m x", work)

    def pr_repository(self, name, branch="main"):
        work = self.repository(fork=False, name=name, branch=branch)
        self.git("-C", str(work), "config", "agents.route", "pr")
        return work

    def git_dir(self, work):
        return Path(self.git("-C", str(work), "rev-parse", "--absolute-git-dir").stdout.strip())

    def test_pr_route_denies_a_commit_on_the_default_branch_even_mid_merge_or_rebase(self):
        work = self.pr_repository("pr-in-progress")
        git_dir = self.git_dir(work)
        self.git("-C", str(work), "branch", "feature")
        for name in ("MERGE_HEAD", "SQUASH_MSG", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            path = git_dir / name
            if name.startswith("rebase"):
                path.mkdir()
            else:
                path.write_text("0" * 40 + "\n", encoding="utf-8")
            self.assert_deny("git commit --no-edit", work, "so nothing is committed on its main")
            self.git("-C", str(work), "symbolic-ref", "HEAD", "refs/heads/feature")
            self.assert_allow("git commit --no-edit", work)
            self.git("-C", str(work), "symbolic-ref", "HEAD", "refs/heads/main")
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()

    def test_pr_route_denies_operations_that_replay_commits_onto_the_default_branch(self):
        work = self.pr_repository("pr-replay")
        for command in ("git merge feature", "git merge --no-ff feature", "git merge --continue",
                        "git cherry-pick abc123", "git cherry-pick --continue", "git rebase --continue",
                        "git rebase --skip", "git rebase origin/main", "git am patch.mbox", "git am --continue",
                        "git revert HEAD", "git revert --continue", "git pull", "git pull origin main",
                        "git pull --rebase", "git pull --no-ff", "git pull --ff-only --rebase",
                        "git merge --ff-only --no-ff feature", "git -C . merge feature",
                        "cd . && git merge feature", "rtk git merge feature"):
            reason = self.assert_deny(command, work, "vcs: solo-owner/widget integrates through pull requests, "
                                                     "so nothing is merged, cherry-picked, rebased, applied, "
                                                     "reverted or pulled onto its main.")
            self.assertIn("git switch -c <branch>", reason)
            self.assertIn("git pull --ff-only", reason)
        for command in ("git merge --abort", "git cherry-pick --abort", "git rebase --abort", "git am --abort",
                        "git revert --quit", "git rebase --quit", "git am --show-current-patch",
                        "git merge --help", "git pull --ff-only", "git pull --ff-only origin main",
                        "git merge --ff-only origin/main", "git pull --ff-only --no-rebase", "git status",
                        "git log --merge", "git switch -c fix && git merge feature"):
            self.assert_allow(command, work)
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("git merge main", "git cherry-pick abc123", "git rebase main", "git pull",
                        "git revert HEAD", "git am patch.mbox", "git rebase --continue"):
            self.assert_allow(command, work)

    def test_replaying_commits_onto_the_default_branch_stays_allowed_for_the_other_routes(self):
        direct = self.repository(fork=False, name="replay-direct")
        fork = self.repository(ahead=False, name="replay-fork")
        theirs = self.repository(fork=False, name="replay-theirs")
        self.write_hosts_user("other-person")
        for work in (direct, fork, theirs):
            for command in ("git merge feature", "git cherry-pick abc123", "git rebase --continue",
                            "git pull", "git am patch.mbox", "git revert HEAD"):
                self.assert_allow(command, work)

    def test_pr_route_treats_a_rebase_of_the_default_branch_as_being_on_it(self):
        work = self.pr_repository("pr-rebasing")
        git_dir = self.git_dir(work)
        self.git("-C", str(work), "switch", "--detach")
        for name in ("rebase-merge", "rebase-apply"):
            state = git_dir / name
            state.mkdir()
            (state / "head-name").write_text("refs/heads/main\n", encoding="utf-8")
            for command in ("git rebase --continue", "git commit --no-edit"):
                self.assert_deny(command, work, "integrates through pull requests")
            (state / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
            for command in ("git rebase --continue", "git commit --no-edit"):
                self.assert_allow(command, work)
            (state / "head-name").unlink()
            state.rmdir()
        for command in ("git rebase --continue", "git commit --no-edit"):
            self.assert_allow(command, work)

    def test_a_tag_named_like_the_default_branch_does_not_hide_the_branch(self):
        pr = self.pr_repository("tagged-pr")
        fork = self.repository(ahead=False, name="tagged-fork")
        for work, fragment in ((pr, "integrates through pull requests"), (fork, "this is a fork")):
            self.git("-C", str(work), "tag", "main")
            self.assert_deny("git commit -m x", work, fragment)
        self.assert_deny("git push", pr, "never push its main")
        self.git("-C", str(pr), "switch", "-c", "feature")
        self.assert_allow("git commit -m x", pr)

    def test_a_configured_route_denies_mutations_while_the_default_branch_is_unknown(self):
        work = self.pr_repository("pr-no-head", branch="develop")
        self.git("-C", str(work), "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("git commit -m x", "git push -u origin feature", "git push", "gh pr create",
                        "git merge main"):
            reason = self.assert_deny(command, work, "vcs: the default branch of origin is unknown")
            self.assertIn("git remote set-head origin -a", reason)
        for command in ("git push --dry-run origin feature", "git commit --dry-run -m x", "git status",
                        "git merge --abort"):
            self.assert_allow(command, work)
        self.git("-C", str(work), "remote", "set-head", "origin", "develop")
        self.assert_allow("git commit -m x", work)
        self.assert_allow("git push -u origin feature", work)

    def test_unknown_default_stays_open_without_a_route_that_needs_it(self):
        unconfigured = self.repository(fork=False, name="open-no-head", branch="develop")
        direct = self.pr_repository("open-direct", branch="develop")
        self.git("-C", str(direct), "config", "agents.route", "direct")
        for work in (unconfigured, direct):
            self.git("-C", str(work), "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
            self.assert_allow("git commit -m x", work)
            self.assert_allow("git push origin develop", work)

    def test_a_configured_route_does_not_guess_main_when_origin_head_is_missing(self):
        work = self.pr_repository("pr-guess", branch="develop")
        self.git("-C", str(work), "update-ref", "refs/remotes/origin/main", "refs/remotes/origin/develop")
        self.git("-C", str(work), "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        for command in ("git commit -m x", "git push origin develop", "git push origin main", "gh pr create"):
            reason = self.assert_deny(command, work, "vcs: the default branch of origin is unknown")
            self.assertIn("git remote set-head origin -a", reason)
        self.git("-C", str(work), "remote", "set-head", "origin", "develop")
        self.assert_deny("git commit -m x", work, "so nothing is committed on its develop")

    def test_a_local_branch_named_like_the_remote_head_does_not_hide_the_default_branch(self):
        work = self.pr_repository("pr-shadow-head", branch="develop")
        self.git("-C", str(work), "branch", "origin/develop")
        self.assert_deny("git commit -m x", work, "so nothing is committed on its develop")
        self.assert_deny("git push origin develop", work, "so never push its develop")

    def test_pr_route_only_fast_forwards_from_origins_default_branch(self):
        work = self.pr_repository("pr-ff")
        self.git("-C", str(work), "branch", "feature")
        self.git("-C", str(work), "update-ref", "refs/remotes/origin/feature", "feature")
        for command in ("git merge --ff-only feature", "git pull --ff-only . feature",
                        "git pull --ff-only origin feature", "git pull --ff-only upstream main",
                        "git pull --ff-only origin main feature", "git pull --ff-only origin",
                        "git merge --ff-only origin/feature", "git merge --ff-only origin/main feature",
                        "git merge --ff-only -m note origin/main", "git merge --ff-only"):
            self.assert_deny(command, work, "so nothing is merged, cherry-picked, rebased")
        for command in ("git pull --ff-only", "git pull --ff-only origin main", "git pull -q --ff-only origin main",
                        "git pull --ff-only --no-rebase", "git merge --ff-only origin/main",
                        "git merge --ff-only refs/remotes/origin/main", "git merge --ff-only @{u}",
                        "git merge --ff-only @{upstream}"):
            self.assert_allow(command, work)
        self.git("-C", str(work), "branch", "--set-upstream-to=origin/feature")
        self.assert_deny("git merge --ff-only @{u}", work, "so nothing is merged")
        self.git("-C", str(work), "branch", "--unset-upstream")
        self.assert_deny("git merge --ff-only @{u}", work, "so nothing is merged")
        self.git("-C", str(work), "branch", "origin/main")
        self.assert_deny("git merge --ff-only origin/main", work, "so nothing is merged")

    def test_a_bare_pull_is_a_sync_only_when_the_branch_tracks_origins_default(self):
        work = self.pr_repository("pr-bare-pull")
        self.git("-C", str(work), "branch", "feature")
        self.git("-C", str(work), "update-ref", "refs/remotes/origin/feature", "feature")
        bare = ("git pull --ff-only", "git pull -q --ff-only --no-rebase", "git -C . pull --ff-only")
        for command in bare:
            self.assert_allow(command, work)
        # A bare pull follows the branch's own tracking configuration, not origin's default branch.
        self.git("-C", str(work), "branch", "--set-upstream-to=origin/feature")
        for command in bare:
            reason = self.assert_deny(command, work, "so nothing is merged, cherry-picked, rebased")
            self.assertIn("git switch -c <branch>", reason)
        self.git("-C", str(work), "branch", "--set-upstream-to=origin/main")
        self.git("-C", str(work), "config", "branch.main.remote", ".")
        self.git("-C", str(work), "config", "branch.main.merge", "refs/heads/feature")
        self.assert_deny("git pull --ff-only", work, "so nothing is merged")
        self.git("-C", str(work), "config", "branch.main.remote", "origin")
        self.git("-C", str(work), "config", "branch.main.merge", "refs/heads/main")
        self.git("-C", str(work), "config", "--add", "branch.main.merge", "refs/heads/feature")
        self.assert_deny("git pull --ff-only", work, "so nothing is merged")
        self.git("-C", str(work), "config", "--unset-all", "branch.main.merge")
        self.assert_deny("git pull --ff-only", work, "so nothing is merged")
        self.git("-C", str(work), "config", "branch.main.merge", "refs/heads/main")
        self.assert_allow("git pull --ff-only", work)
        self.git("-C", str(work), "config", "--unset", "branch.main.remote")
        self.assert_deny("git pull --ff-only", work, "so nothing is merged")

    def test_a_present_but_empty_route_fails_closed(self):
        work = self.repository(fork=False, name="empty-route")
        self.git("-C", str(work), "config", "agents.route", "")
        for command in ("git commit -m x", "git push origin main", "git merge feature", "gh pr create"):
            reason = self.assert_deny(command, work, "vcs: the configured route can't be applied")
            self.assertIn("agents.route must be direct, pr, upstream, or upstream+carry (got '')", reason)
        self.git("-C", str(work), "config", "--unset", "agents.route")
        self.assert_allow("git commit -m x", work)

    def test_every_mutation_under_a_failed_configured_route_shows_the_routers_error(self):
        fork = self.repository(ahead=False, name="bad-route-upstream-push")
        self.git("-C", str(fork), "config", "agents.route", "prr")
        for command in ("git push upstream", "git push upstream main",
                        "git push https://github.com/upstream-owner/widget.git"):
            reason = self.assert_deny(command, fork, "vcs: the configured route can't be applied")
            self.assertIn("agents.route must be direct, pr, upstream, or upstream+carry (got 'prr')", reason)
            self.assertNotIn("gh pr create", reason)
        self.assert_allow("git push --dry-run upstream", fork)
        # Without a failing route an upstream push keeps its own instruction.
        self.git("-C", str(fork), "config", "--unset", "agents.route")
        self.assert_deny("git push upstream", fork, UPSTREAM_PUSH)

    def test_a_configured_route_that_cannot_be_applied_fails_closed(self):
        work = self.repository(fork=False, name="bad-route")
        self.git("-C", str(work), "config", "agents.route", "prr")
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("git commit -m x", "git push -u origin feature", "git push origin main", "git push",
                        "git merge main", "git cherry-pick abc123", "git pull", "git rebase main",
                        "gh pr create", "git switch -c a && git commit -m x"):
            reason = self.assert_deny(command, work, "vcs: the configured route can't be applied")
            self.assertIn("agents.route must be direct, pr, upstream, or upstream+carry (got 'prr')", reason)
            self.assertNotIn("fix-route:", reason)
        for command in ("git push --dry-run origin feature", "git commit --dry-run -m x", "git merge --abort",
                        "git rebase --abort", "git status", "git log"):
            self.assert_allow(command, work)
        self.git("-C", str(work), "config", "agents.route", "pr")
        self.assert_allow("git commit -m x", work)

    def test_an_upstream_route_without_its_remote_or_refs_fails_closed(self):
        for route in ("upstream", "upstream+carry"):
            lone = self.repository(fork=False, name=f"lone-{route[-5:]}")
            self.git("-C", str(lone), "config", "agents.route", route)
            self.git("-C", str(lone), "switch", "-c", "feature")
            for command in ("git commit -m x", "git push -u origin feature", "gh pr create"):
                reason = self.assert_deny(command, lone, "vcs: the configured route can't be applied")
                self.assertIn(f"agents.route is {route} but this repository has no upstream remote", reason)
            fork = self.repository(ahead=False, name=f"refless-{route[-5:]}")
            self.git("-C", str(fork), "config", "agents.route", route)
            self.git("-C", str(fork), "symbolic-ref", "-d", "refs/remotes/upstream/HEAD")
            self.git("-C", str(fork), "update-ref", "-d", "refs/remotes/upstream/main")
            reason = self.assert_deny("git commit -m x", fork, "vcs: the configured route can't be applied")
            self.assertIn("default branch of upstream", reason)
            self.assert_deny("gh pr create --repo upstream-owner/widget", fork, "can't be applied")
        # Without a configured route the same gap is not a violation.
        open_fork = self.repository(ahead=False, name="refless-open")
        self.git("-C", str(open_fork), "symbolic-ref", "-d", "refs/remotes/upstream/HEAD")
        self.git("-C", str(open_fork), "update-ref", "-d", "refs/remotes/upstream/main")
        self.assert_allow("git commit -m x", open_fork)
        self.assert_allow("gh pr create", open_fork)

    def test_pr_route_rebase_checks_the_branch_being_rebased_not_the_checked_out_one(self):
        work = self.pr_repository("pr-rebase-branch")
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("git rebase feature main", "git rebase -q feature main", "git rebase --onto origin/main feature main",
                        "git rebase --root main", "git rebase -s ours -X theirs feature main"):
            self.assert_deny(command, work, "so nothing is merged, cherry-picked, rebased")
        for command in ("git rebase main", "git rebase main feature", "git rebase origin/main feature",
                        "git rebase --onto origin/main main feature"):
            self.assert_allow(command, work)
        self.git("-C", str(work), "switch", "main")
        for command in ("git rebase main", "git rebase --onto main feature", "git rebase origin/main main",
                        "git rebase --root"):
            self.assert_deny(command, work, "so nothing is merged, cherry-picked, rebased")
        for command in ("git rebase main feature", "git rebase refs/heads/main refs/heads/feature",
                        "git rebase --onto origin/main main feature"):
            self.assert_allow(command, work)

    def test_gh_repo_inside_bundled_short_options_and_after_equals_is_read(self):
        work = self.pr_repository("pr-bundled")
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("gh pr create -fRother-owner/widget", "gh pr create -fR other-owner/widget",
                        "gh pr create -dfRother-owner/widget", "gh pr create -R=other-owner/widget",
                        "gh pr create -fR=other-owner/widget", "gh pr create -Rother-owner/widget",
                        'gh pr create -ft "a title" -Rother-owner/widget', "gh pr create --repo=other-owner/widget"):
            self.assert_deny(command, work, "not to other-owner/widget")
        for command in ("gh pr create -fRsolo-owner/widget", "gh pr create -R=solo-owner/widget",
                        "gh pr create -fR=solo-owner/widget", "gh pr create -fR solo-owner/widget",
                        'gh pr create -ft "other-owner/widget" -Rsolo-owner/widget',
                        'gh pr create -ft "-R other-owner/widget"'):
            self.assert_allow(command, work)
        fork = self.repository(ahead=False, name="bundled-fork")
        self.assert_allow("gh pr create -fRupstream-owner/widget", fork)
        self.assert_allow("gh pr create -R=upstream-owner/widget", fork)
        self.assert_deny("gh pr create -fRorigin-owner/widget", fork, "gh pr create --repo upstream-owner/widget")

    def test_pr_route_pr_creation_must_target_origin_from_a_branch(self):
        work = self.pr_repository("pr-target")
        self.assert_deny("gh pr create", work, "so open the pull request from a branch")
        self.assert_deny("gh pr create --fill", work, "git switch -c <branch>")
        self.assert_allow("gh pr create --head feature --base main", work)
        self.assert_allow("git switch -c feature && gh pr create", work)
        self.git("-C", str(work), "switch", "-c", "feature")
        for command in ("gh pr create", "gh pr create --repo solo-owner/widget", "gh -R solo-owner/widget pr create",
                        "gh pr create -R https://github.com/Solo-Owner/widget.git",
                        "GH_REPO=solo-owner/widget gh pr create", "export GH_REPO=solo-owner/widget && gh pr create",
                        "GH_REPO=other-owner/widget gh pr create --repo solo-owner/widget",
                        "export GH_REPO=other-owner/widget && unset GH_REPO && gh pr create"):
            self.assert_allow(command, work)
        for command in ("gh pr create --repo other-owner/widget", "gh pr create -R other-owner/widget",
                        "gh -R other-owner/widget pr create", "gh pr create --repo=other-owner/widget",
                        "GH_REPO=other-owner/widget gh pr create", "GH_REPO=other-owner/widget rtk gh pr create",
                        "export GH_REPO=other-owner/widget && gh pr create",
                        "GH_REPO=solo-owner/widget gh pr create --repo other-owner/widget"):
            reason = self.assert_deny(command, work, "vcs: solo-owner/widget integrates through pull requests, "
                                                     "so a PR goes to it, not to other-owner/widget.")
            self.assertIn("gh pr create", reason)
        with patch.dict(os.environ, {"GH_REPO": "other-owner/widget"}):
            self.assert_deny("gh pr create", work, "not to other-owner/widget")
            self.assert_allow("gh pr create --repo solo-owner/widget", work)

    def test_pr_route_pr_creation_honours_gh_repo_set_default(self):
        work = self.pr_repository("pr-default")
        self.git("-C", str(work), "switch", "-c", "feature")
        self.git("-C", str(work), "remote", "add", "upstream", "https://github.com/other-owner/widget.git")
        self.assert_allow("gh pr create --repo solo-owner/widget", work)
        self.git("-C", str(work), "config", "remote.origin.gh-resolved", "base")
        self.assert_allow("gh pr create", work)
        self.git("-C", str(work), "config", "--unset", "remote.origin.gh-resolved")
        self.git("-C", str(work), "config", "remote.upstream.gh-resolved", "base")
        self.assert_deny("gh pr create", work, "not to other-owner/widget.")
        self.assert_allow("gh pr create --repo solo-owner/widget", work)
        self.git("-C", str(work), "config", "remote.upstream.gh-resolved", "solo-owner/widget")
        self.assert_allow("gh pr create", work)
        self.git("-C", str(work), "config", "remote.upstream.gh-resolved", "third-owner/widget")
        self.assert_deny("gh pr create", work, "not to third-owner/widget.")

    def test_pr_route_with_several_github_remotes_and_no_gh_default_needs_an_explicit_repo(self):
        work = self.pr_repository("pr-several")
        self.git("-C", str(work), "switch", "-c", "feature")
        self.assert_allow("gh pr create", work)
        self.git("-C", str(work), "remote", "add", "backup", "/srv/not-github.git")
        self.assert_allow("gh pr create", work)
        self.git("-C", str(work), "remote", "add", "mirror", "git@github.com:Solo-Owner/widget.git")
        self.assert_allow("gh pr create", work)
        self.git("-C", str(work), "remote", "add", "upstream", "https://github.com/other-owner/widget.git")
        reason = self.assert_deny("gh pr create --fill", work, "gh may pick another one")
        self.assertIn("gh pr create --repo solo-owner/widget", reason)
        for command in ("gh pr create --repo solo-owner/widget", "gh pr create -R solo-owner/widget",
                        "GH_REPO=solo-owner/widget gh pr create"):
            self.assert_allow(command, work)
        # A stale entry for a remote that no longer exists is not a gh default.
        self.git("-C", str(work), "config", "remote.ghost.gh-resolved", "third-owner/widget")
        self.assert_deny("gh pr create", work, "gh pr create --repo solo-owner/widget")
        self.assert_allow("gh pr create --repo solo-owner/widget", work)
        self.git("-C", str(work), "config", "remote.origin.gh-resolved", "base")
        self.assert_allow("gh pr create", work)

    def test_a_stale_gh_default_for_a_missing_remote_does_not_deny_a_pr_to_origin(self):
        work = self.pr_repository("pr-stale")
        self.git("-C", str(work), "switch", "-c", "feature")
        self.git("-C", str(work), "config", "remote.ghost.gh-resolved", "third-owner/widget")
        self.assert_allow("gh pr create", work)
        self.assert_allow("gh pr create --repo solo-owner/widget", work)

    def test_pr_route_requires_repository_local_config(self):
        work = self.repository(fork=False, name="global-pr")
        self.git("config", "--global", "agents.route", "pr")
        self.assert_allow("git commit -m x", work)
        self.assert_allow("git push origin main", work)
        self.assert_deny("gh pr create", work, "takes no PRs")
        self.git("-C", str(work), "config", "--local", "agents.route", "pr")
        self.assert_deny("git commit -m x", work, "integrates through pull requests")

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


    def test_a_branch_switch_earlier_in_the_script_makes_the_branch_unknown(self):
        fork = self.repository(ahead=False, name="chain-fork")
        work = self.repository(fork=False, name="chain-theirs")
        for command in ("git fetch upstream && git switch -c fix upstream/main && git commit -am x",
                        "git checkout -b fix && git add f && git commit -m x",
                        "git switch -c fix && git push -u origin HEAD",
                        "git switch -c fix && git push",
                        "git stash && git commit -m x"):
            self.assert_allow(command, fork)
        self.assert_deny("git switch -c fix && git push origin main", fork, CARRY)
        self.assert_deny("git status && git commit -m x", fork, "this is a fork")
        self.write_hosts_user("other-person")
        self.assert_allow("git switch -c fix && git commit -m x && git push -u origin HEAD", work)

    def test_messages_name_the_fork_default_branch(self):
        fork = self.repository(ahead=False, name="patched-fork")
        self.git("-C", str(fork), "branch", "-m", "main", "patched")
        self.git("-C", str(fork), "update-ref", "refs/remotes/origin/patched", "HEAD")
        self.git("-C", str(fork), "update-ref", "-d", "refs/remotes/origin/main")
        self.git("-C", str(fork), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/patched")
        self.git("-C", str(fork), "config", "agents.route", "upstream+carry")
        reason = self.assert_deny("git commit -m x", fork, "don't commit on the fork's patched")
        self.assertIn("upstream/main", reason)
        self.assertIn("git switch patched && git merge <branch> && git push origin patched", reason)
        url = "https://github.com/upstream-owner/widget/pull/9"
        context = vcs_guard.remind(self.post("gh pr create --repo upstream-owner/widget", fork, url))
        self.assertIn("git push origin patched", context["hookSpecificOutput"]["additionalContext"])

    def test_squash_merge_and_message_values(self):
        fork = self.repository(ahead=False, name="squash")
        git_dir = Path(self.git("-C", str(fork), "rev-parse", "--absolute-git-dir").stdout.strip())
        (git_dir / "SQUASH_MSG").write_text("squashed\n", encoding="utf-8")
        self.assert_allow("git commit -m x", fork)
        (git_dir / "SQUASH_MSG").unlink()
        self.assert_deny("git commit -m --dry-run", fork, "this is a fork")
        self.assert_deny("git commit -m -h", fork, "this is a fork")
        self.assert_allow("git commit -m x --dry-run", fork)

    def test_reminder_edge_cases(self):
        fork = self.repository(ahead=False, name="remind-edge")
        created = "gh pr create --repo upstream-owner/widget"
        exists = {"stderr": "a pull request for branch \"fix\" into branch \"main\" already exists:\n"
                            "https://github.com/upstream-owner/widget/pull/7"}
        self.assertIsNone(vcs_guard.remind(self.post(created, fork, exists)))
        upper = "https://github.com/Upstream-Owner/Widget/pull/7"
        self.assertIsNotNone(vcs_guard.remind(self.post(created, fork, upper)))
        self.assertIsNotNone(vcs_guard.remind(self.post(f"cd remind-edge && {created}", fork, upper)))
        self.assertIsNotNone(vcs_guard.remind(self.post(f"cd remind-edge && {created}", fork.parent, upper)))
        self.git("-C", str(fork), "config", "agents.route", "upstream")
        self.assertIsNone(vcs_guard.remind(self.post(created, fork, upper)))


if __name__ == "__main__":
    unittest.main()
