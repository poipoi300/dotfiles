"""Route decisions. No network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.abspath(__file__)).parents[2]
sys.path.insert(0, str(ROOT / "hooks"))
import fix_route

BIN = ROOT / "bin" / "fix-route"
ASK = ("Ask the user whether the fork should carry this until upstream merges; "
       "if yes: git config agents.route upstream+carry")


class RepoFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fix-route-")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.gitconfig = self.root / "gitconfig"
        self.gitconfig.write_text("", encoding="utf-8")
        self.gh_dir = self.root / "gh"
        self.gh_dir.mkdir()
        self.xdg = self.root / "xdg"
        self.xdg.mkdir()
        # solo-owner matches the non-fork origin in repository(), so those repos stay direct.
        self.write_hosts_user("solo-owner")
        self.env = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GH_CONFIG_DIR": str(self.gh_dir),
            "XDG_CONFIG_HOME": str(self.xdg),
            "GIT_AUTHOR_NAME": "Route Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Route Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in list(os.environ):
            if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_NAMESPACE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GH_REPO"}:
                os.environ.pop(key, None)
            elif key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                os.environ.pop(key, None)

    def git(self, *args):
        result = subprocess.run(["git", *args], check=False, capture_output=True, text=True, env=os.environ)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def repository(self, branch="main", *, fork=True, ahead=False, name="work"):
        upstream = self.root / f"{name}-upstream.git"
        origin = self.root / f"{name}-origin.git"
        seed = self.root / f"{name}-seed"
        work = self.root / name
        self.git("init", "--bare", "-b", branch, str(upstream))
        self.git("init", "-b", branch, str(seed))
        self.git("-C", str(seed), "config", "user.name", "Route Test")
        self.git("-C", str(seed), "config", "user.email", "test@example.com")
        (seed / "README").write_text("seed\n", encoding="utf-8")
        self.git("-C", str(seed), "add", "README")
        self.git("-C", str(seed), "commit", "-m", "seed")
        self.git("-C", str(seed), "remote", "add", "origin", str(upstream))
        self.git("-C", str(seed), "push", "origin", branch)
        self.git("clone", "--bare", str(upstream), str(origin))
        self.git("clone", str(origin), str(work))
        self.git("-C", str(work), "config", "user.name", "Route Test")
        self.git("-C", str(work), "config", "user.email", "test@example.com")
        if fork:
            self.git("-C", str(work), "remote", "add", "upstream", str(upstream))
            self.git("-C", str(work), "fetch", "upstream")
        if ahead:
            (work / "README").write_text("carry\n", encoding="utf-8")
            self.git("-C", str(work), "commit", "-am", "carry")
            self.git("-C", str(work), "push", "origin", branch)
        origin_url = "https://github.com/origin-owner/widget.git" if fork else "https://github.com/solo-owner/widget.git"
        self.git("-C", str(work), "remote", "set-url", "origin", origin_url)
        if fork:
            self.git("-C", str(work), "remote", "set-url", "upstream", "https://github.com/upstream-owner/widget.git")
        return work

    def write_hosts_user(self, login: str | None) -> None:
        path = self.gh_dir / "hosts.yml"
        if login is None:
            path.unlink(missing_ok=True)
            return
        path.write_text(
            "github.com:\n"
            "    users:\n"
            f"        {login}:\n"
            "            git_protocol: ssh\n"
            "    git_protocol: ssh\n"
            f'    user: "{login}"\n',
            encoding="utf-8",
        )

    def set_github_user(self, login: str | None) -> None:
        if login is None:
            result = subprocess.run(
                ["git", "config", "--global", "--unset", "agents.githubUser"],
                check=False, capture_output=True, text=True, env=os.environ,
            )
            self.assertIn(result.returncode, (0, 5), result.stderr)
            return
        self.git("config", "--global", "agents.githubUser", login)

    def cli(self, directory: Path, *args: str) -> tuple[str, int]:
        stdout, stderr = StringIO(), StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = fix_route.main(["-C", str(directory), *args])
        return (stderr.getvalue() if code else stdout.getvalue()), code


class RouteTests(RepoFixture, unittest.TestCase):
    def test_slugs_cover_https_and_ssh_with_and_without_git_suffix(self):
        for url in ("https://github.com/origin-owner/widget", "https://github.com/origin-owner/widget.git",
                    "https://github.com/origin-owner/widget/", "http://github.com/origin-owner/widget.git",
                    "git@github.com:origin-owner/widget.git", "ssh://git@github.com/origin-owner/widget",
                    "git://github.com/origin-owner/widget.git"):
            self.assertEqual(fix_route.github_slug(url), "origin-owner/widget", url)
        self.assertIsNone(fix_route.github_slug("https://gitlab.com/origin-owner/widget.git"))
        self.assertIsNone(fix_route.github_slug("https://github.com/origin-owner/widget/issues/1"))
        self.assertTrue(fix_route.urls_equal("git@github.com:upstream-owner/widget.git",
                                             "https://github.com/upstream-owner/widget"))
        self.assertTrue(fix_route.same_repo("upstream-owner/widget", "upstream-owner/widget"))
        self.assertTrue(fix_route.same_repo("https://github.com/upstream-owner/widget.git", "upstream-owner/widget"))
        self.assertTrue(fix_route.same_repo("github.com/upstream-owner/widget", "upstream-owner/widget"))
        self.assertTrue(fix_route.same_repo("github.com/upstream-owner/widget.git", "upstream-owner/widget"))
        self.assertTrue(fix_route.same_repo("https://github.com/Upstream-Owner/widget", "upstream-owner/widget"))
        self.assertFalse(fix_route.same_repo("github.com/origin-owner/widget", "upstream-owner/widget"))
        self.assertFalse(fix_route.same_repo("origin-owner/widget", "upstream-owner/widget"))

    def test_offline_direct_json_and_script(self):
        work = self.repository(fork=False)
        calls = []
        real = fix_route.run

        def wrapped(argv, timeout=5):
            calls.append(argv)
            return real(argv, timeout=timeout)

        with patch.object(fix_route, "run", side_effect=wrapped):
            route = fix_route.decide(work, offline=True)
        self.assertFalse(any(argv[0] == "gh" for argv in calls))
        self.assertFalse(any("fetch" in argv or "push" in argv for argv in calls))
        self.assertEqual(route.route, "direct")
        self.assertEqual(route.source, "remotes")
        self.assertEqual(route.default_branch, "main")
        self.assertEqual(route.origin_repo, "solo-owner/widget")
        self.assertIsNone(route.upstream_repo)
        self.assertEqual(route.steps, (
            "Commit on main.",
            "git push origin main",
            "Do not open a pull request.",
        ))
        stdout, code = self.cli(work, "--offline", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), route.as_dict())
        script = subprocess.run(
            [sys.executable, "-I", "-B", str(BIN), "-C", str(work), "--offline", "--json"],
            check=False, capture_output=True, text=True, env=os.environ, timeout=30,
        )
        self.assertEqual(script.returncode, 0, script.stderr)
        self.assertEqual(json.loads(script.stdout), route.as_dict())

    def test_ahead_count_selects_upstream_or_carry_for_main_and_master(self):
        for branch, ahead, expected in (("main", False, "upstream"), ("main", True, "upstream+carry"),
                                        ("master", False, "upstream"), ("master", True, "upstream+carry")):
            with self.subTest(branch=branch, ahead=ahead):
                work = self.repository(branch, ahead=ahead, name=f"{branch}-{int(ahead)}")
                route = fix_route.decide(work, offline=True)
                self.assertEqual(route.route, expected)
                self.assertEqual(route.source, "remotes")
                self.assertEqual(route.default_branch, branch)
                self.assertEqual(route.upstream_repo, "upstream-owner/widget")
                self.assertEqual(route.origin_repo, "origin-owner/widget")
                text = route.format_text()
                self.assertIn("run `git fetch --all` first", text)
                self.assertIn(f"git switch -c <branch> upstream/{branch}", text)
                self.assertIn("gh pr create --repo upstream-owner/widget", text)
                if expected == "upstream":
                    self.assertIn(ASK, route.steps)
                    self.assertNotIn("git merge <branch>", route.steps)
                    self.assertIn(f"Do not merge into the fork's {branch}.", route.steps)
                else:
                    self.assertNotIn(ASK, route.steps)
                    self.assertIn("git merge <branch>", route.steps)
                    self.assertIn(f"git push origin {branch}", route.steps)
                    self.assertIn("Do not open a pull request on the fork.", route.steps)

    def test_config_override_wins_and_an_unknown_value_exits_2(self):
        work = self.repository(ahead=True)
        self.git("-C", str(work), "config", "agents.route", "direct")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source, route.default_branch), ("direct", "config", "main"))
        self.assertIn("(set by git config agents.route)", route.format_text())
        self.assertNotIn("gh pr create", route.format_text())
        self.git("-C", str(work), "config", "agents.route", "upstream+carry")
        forced = fix_route.decide(work, offline=True)
        self.assertEqual((forced.route, forced.source), ("upstream+carry", "config"))
        matched = self.repository(ahead=False, name="matched")
        self.git("-C", str(matched), "config", "agents.route", "upstream")
        self.assertEqual(fix_route.decide(matched, offline=True).source, "config")
        self.git("-C", str(work), "config", "agents.route", "sideways")
        stderr, code = self.cli(work, "--offline")
        self.assertEqual(code, 2)
        self.assertIn("agents.route must be direct, pr, upstream, or upstream+carry", stderr)
        self.assertIn("sideways", stderr)

    def test_pr_is_only_reached_through_config_and_describes_the_pr_workflow(self):
        work = self.repository(fork=False, name="own-pr")
        self.assertEqual(fix_route.decide(work, offline=True).route, "direct")
        self.git("-C", str(work), "config", "agents.route", "pr")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source, route.default_branch), ("pr", "config", "main"))
        self.assertEqual(route.origin_repo, "solo-owner/widget")
        self.assertIsNone(route.upstream_repo)
        self.assertEqual(route.steps, (
            "git switch -c <branch>",
            "Commit on that branch.",
            "git push -u origin HEAD",
            "gh pr create",
            "The PR merges on GitHub.",
            "Do not commit on or push main.",
        ))
        text = route.format_text()
        self.assertTrue(text.startswith("pr (set by git config agents.route)\n"), text)
        stdout, code = self.cli(work, "--offline", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), route.as_dict())
        self.git("-C", str(work), "config", "--unset", "agents.route")
        unset = fix_route.decide(work, offline=True)
        self.assertEqual((unset.route, unset.source), ("direct", "remotes"))
        other = self.repository(fork=False, name="their-pr")
        self.write_hosts_user("other-person")
        self.assertEqual(fix_route.decide(other, offline=True).route, "external")

    def test_pr_comes_only_from_repository_local_config(self):
        work = self.repository(fork=False, name="global-pr")
        self.git("config", "--global", "agents.route", "pr")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source), ("direct", "remotes"))
        self.git("-C", str(work), "config", "--local", "agents.route", "pr")
        self.assertEqual(fix_route.decide(work, offline=True).route, "pr")
        self.git("-C", str(work), "config", "--local", "agents.route", "direct")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source), ("direct", "config"))
        self.git("-C", str(work), "config", "--local", "--unset", "agents.route")
        self.git("config", "--global", "agents.route", "direct")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source), ("direct", "config"))

    def test_a_tag_named_like_the_branch_does_not_change_the_current_branch(self):
        work = self.repository(fork=False, name="tagged")
        self.git("-C", str(work), "tag", "main")
        self.assertEqual(fix_route.local_branch(work), "main")
        self.git("-C", str(work), "switch", "--detach")
        self.assertIsNone(fix_route.local_branch(work))

    def test_a_configured_route_that_needs_the_default_names_the_route_when_it_is_unknown(self):
        work = self.repository(fork=False, name="unknown-default", branch="develop")
        self.git("-C", str(work), "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        with self.assertRaises(fix_route.DefaultBranchUnknown) as plain:
            fix_route.decide(work, offline=True)
        self.assertIsNone(plain.exception.route)
        # direct never consults the default branch, so a missing one is no reason to fail closed.
        for route, expected in (("pr", "pr"), ("upstream", "upstream"), ("upstream+carry", "upstream+carry"),
                                ("direct", None)):
            self.git("-C", str(work), "config", "agents.route", route)
            with self.assertRaises(fix_route.DefaultBranchUnknown) as raised:
                fix_route.decide(work, offline=True)
            self.assertEqual(raised.exception.route, expected)
        stderr, code = self.cli(work, "--offline")
        self.assertEqual(code, 2)
        self.assertIn("git remote set-head origin -a", stderr)

    def test_missing_repo_or_origin_exits_2(self):
        missing, code = self.cli(self.root / "absent", "--offline")
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", missing)
        bare = self.root / "no-origin"
        self.git("init", "-b", "main", str(bare))
        stderr, status = self.cli(bare, "--offline", "--json")
        self.assertEqual(status, 2)
        self.assertIn("no origin remote", stderr)
        self.assertEqual(stderr.strip().count("\n") + 1, 1)

    def test_github_fork_exit_3_non_fork_and_failure(self):
        work = self.repository(fork=False)
        real = fix_route.run

        def respond(payload, status=0):
            def wrapped(argv, timeout=5):
                if argv[:4] == ["gh", "repo", "view", "solo-owner/widget"]:
                    stdout = json.dumps(payload) if status == 0 else ""
                    return subprocess.CompletedProcess(argv, status, stdout=stdout, stderr="")
                return real(argv, timeout=timeout)
            return wrapped

        parent = {"isFork": True, "parent": {"name": "widget", "owner": {"login": "upstream-owner"}}}
        with patch.object(fix_route, "run", side_effect=respond(parent)):
            stderr, code = self.cli(work)
        self.assertEqual(code, 3)
        self.assertEqual(
            stderr.strip(),
            "fork without an `upstream` remote: "
            "git remote add upstream https://github.com/upstream-owner/widget.git && git fetch upstream, then rerun",
        )
        with patch.object(fix_route, "run", side_effect=respond({"isFork": False, "parent": None})):
            route = fix_route.decide(work, offline=False)
        self.assertEqual((route.route, route.source), ("direct", "github"))
        with patch.object(fix_route, "run", side_effect=respond({}, status=1)):
            failed = fix_route.decide(work, offline=False)
        self.assertEqual((failed.route, failed.source), ("direct", "remotes"))

        def explode(argv, timeout=5):
            if argv[0] == "gh":
                raise subprocess.TimeoutExpired(argv, timeout)
            return real(argv, timeout=timeout)

        with patch.object(fix_route, "run", side_effect=explode):
            timed = fix_route.decide(work, offline=False)
        self.assertEqual(timed.route, "direct")

    def test_direct_requires_the_origin_owner_to_be_the_user(self):
        work = self.repository(fork=False, name="theirs")
        self.write_hosts_user("other-person")
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source, route.default_branch), ("external", "remotes", "main"))
        self.assertEqual(route.origin_repo, "solo-owner/widget")
        self.assertIsNone(route.upstream_repo)
        self.assertEqual(route.steps, (
            "git switch -c <branch>",
            "Commit on that branch.",
            "git push -u origin HEAD",
            "If you cannot push, fork the repository and push the branch there.",
            "gh pr create --repo solo-owner/widget",
            "Do not push main.",
        ))
        text = route.format_text()
        self.assertTrue(text.startswith("external\n"), text)
        self.assertIn("gh pr create --repo solo-owner/widget", text)
        stdout, code = self.cli(work, "--offline", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), route.as_dict())

        self.set_github_user("Solo-Owner")
        self.assertEqual(fix_route.decide(work, offline=True).route, "direct")

    def test_github_user_comes_from_config_then_hosts_yml(self):
        self.assertEqual(fix_route.github_user(), "solo-owner")
        xdg_gh = self.xdg / "gh"
        xdg_gh.mkdir()
        (xdg_gh / "hosts.yml").write_text("github.com:\n  user: xdg-user\n", encoding="utf-8")
        self.assertEqual(fix_route.github_user(), "solo-owner")
        self.write_hosts_user(None)
        removed = os.environ.copy()
        removed.pop("GH_CONFIG_DIR", None)
        with patch.dict(os.environ, removed, clear=True):
            self.assertEqual(fix_route.github_user(), "xdg-user")
        self.assertIsNone(fix_route.github_user())
        nested = (
            "github.com:\n"
            "    users:\n"
            "        nested-login:\n"
            "            user: not-this\n"
            "    user: real-login\n"
            "gitlab.com:\n"
            "    user: other\n"
        )
        (self.gh_dir / "hosts.yml").write_text(nested, encoding="utf-8")
        self.assertEqual(fix_route.github_user(), "real-login")
        self.set_github_user("from-config")
        self.assertEqual(fix_route.github_user(), "from-config")

    def test_unknown_login_exits_2(self):
        work = self.repository(fork=False, name="no-login")
        self.write_hosts_user(None)
        route = fix_route.decide(work, offline=True)
        self.assertEqual((route.route, route.source), ("unknown", "login"))
        stderr, code = self.cli(work, "--offline", "--json")
        self.assertEqual(code, 2)
        self.assertIn("git config --global agents.githubUser <login>", stderr)
        self.assertNotIn('"route"', stderr)

    def test_online_fork_check_precedes_the_owner(self):
        work = self.repository(fork=False, name="online-fork")
        self.write_hosts_user("other-person")
        real = fix_route.run
        parent = {"isFork": True, "parent": {"nameWithOwner": "upstream-owner/widget"}}

        def wrapped(argv, timeout=5):
            if argv[:3] == ["gh", "repo", "view"]:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(parent), stderr="")
            return real(argv, timeout=timeout)

        with patch.object(fix_route, "run", side_effect=wrapped):
            stderr, code = self.cli(work)
        self.assertEqual(code, 3)
        self.assertIn("git remote add upstream https://github.com/upstream-owner/widget.git", stderr)

        def not_a_fork(argv, timeout=5):
            if argv[:3] == ["gh", "repo", "view"]:
                payload = {"isFork": False, "parent": None}
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
            return real(argv, timeout=timeout)

        with patch.object(fix_route, "run", side_effect=not_a_fork):
            route = fix_route.decide(work, offline=False)
        self.assertEqual((route.route, route.source), ("external", "github"))

    def test_origin_default_does_not_fall_back_to_the_current_branch(self):
        work = self.repository(fork=False, name="no-head")
        self.git("-C", str(work), "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        self.git("-C", str(work), "update-ref", "-d", "refs/remotes/origin/main")
        self.git("-C", str(work), "switch", "-c", "feature")
        stderr, code = self.cli(work, "--offline")
        self.assertEqual(code, 2)
        self.assertIn("default branch unknown; git fetch origin", stderr)
        self.assertNotIn("feature", stderr)

    def test_ahead_count_ignores_local_refs_with_remote_names(self):
        work = self.repository(ahead=True, name="shadow")
        self.git("-C", str(work), "branch", "upstream/main", "HEAD")
        self.git("-C", str(work), "tag", "origin/main", "HEAD")
        self.assertEqual(fix_route.decide(work, offline=True).route, "upstream+carry")

    def test_push_remote_order_includes_push_default(self):
        work = self.repository(ahead=False, name="push-order")
        self.assertEqual(fix_route.push_remote(work), "origin")
        self.git("-C", str(work), "config", "remote.pushDefault", "upstream")
        self.assertEqual(fix_route.push_remote(work), "upstream")
        self.git("-C", str(work), "config", "branch.main.pushRemote", "origin")
        self.assertEqual(fix_route.push_remote(work), "origin")


if __name__ == "__main__":
    unittest.main()
