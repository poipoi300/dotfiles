"""home/.local/bin/update against temporary repositories, with every system tool stubbed.

Two clones of one bare remote stand for two machines. Stubs on PATH replace sudo, pacman,
aurlock, arcus, gitleaks, claude, codex and agy; nothing touches the real system.
Run from the repo root: python3 -I -B -m unittest discover -s modules/aurlock/test
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "home/.local/bin/update"
APPROVED = "modules/aurlock/home/.config/aurlock/approved"
HEADER = "# pkgbase  commit  version  maintainer  approved  network\n"
NOREPLY = "1+t@users.noreply.github.com"
LINE = "{name} " + "a" * 40 + " 1.0-1 someone 2026-11-18 offline\n"

STUBS = {
    "sudo": 'exec "$@"',
    "pacman": 'echo pacman "$@" >> "$STUB_LOG"',
    # aurlock records what it saw, then approves $NEW_APPROVAL (if set) through the symlink.
    "aurlock": textwrap.dedent('''\
        echo aurlock "$@" >> "$STUB_LOG"
        cp "$HOME/.config/aurlock/approved" "$STUB_SEEN"
        if [ -n "${NEW_APPROVAL:-}" ]; then printf '%s\\n' "$NEW_APPROVAL" >> "$HOME/.config/aurlock/approved"; fi
        if [ -n "${AURLOCK_HOOK:-}" ]; then sh -c "$AURLOCK_HOOK"; fi
        exit "${AURLOCK_EXIT:-0}"'''),
    "arcus": 'echo "Nothing to do."',
    "gitleaks": 'echo gitleaks "$@" >> "$STUB_LOG"; exit "${GITLEAKS_EXIT:-0}"',
    "rustup": 'echo rustup "$@" >> "$STUB_LOG"',
    "claude": 'echo claude "$@" >> "$STUB_LOG"',
    "codex": 'echo codex "$@" >> "$STUB_LOG"',
    "agy": 'echo agy "$@" >> "$STUB_LOG"',
}


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="update-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            path = self.bin / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.remote = self.tmp / "remote.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = self.clone("seed")
        (seed / APPROVED).parent.mkdir(parents=True)
        (seed / APPROVED).write_text(HEADER)
        (seed / "README").write_text("dotfiles\n")
        (seed / ".gitattributes").write_text(f"{APPROVED} merge=aurlock-approvals\n")
        driver = seed / "modules/aurlock/merge-approvals"
        shutil.copy(SCRIPT.parents[3] / "merge-approvals", driver)
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-q", "-m", "seed")
        self.git(seed, "push", "-q", "origin", "main")
        self.a = self.clone("a")
        self.b = self.clone("b")

    def env(self, home):
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(home),
            "XDG_RUNTIME_DIR": str(home),
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": NOREPLY,
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": NOREPLY,
            "STUB_LOG": str(home / "log"),
            "STUB_SEEN": str(home / "seen"),
        }

    def git(self, cwd, *args):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                env=self.env(self.tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def clone(self, name):
        work = self.tmp / name
        self.git(self.tmp, "clone", "-q", str(self.remote), str(work))
        if (work / APPROVED).exists():
            home = self.tmp / f"home-{name}"
            (home / ".config/aurlock").mkdir(parents=True)
            (home / ".config/aurlock/approved").symlink_to(work / APPROVED)
        return work

    def run_update(self, work, **extra):
        home = self.tmp / f"home-{work.name}"
        env = self.env(home) | extra
        (home / "log").write_text("")
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)
        self.log = (home / "log").read_text()
        self.seen = (home / "seen").read_text() if (home / "seen").exists() else ""
        return result

    def remote_file(self):
        return self.git(self.a, "show", f"origin/main:{APPROVED}")

    def test_new_approval_is_committed_and_pushed_without_the_hostname(self):
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git(self.a, "fetch", "-q")
        self.assertIn("foo ", self.remote_file())
        self.assertEqual(self.git(self.a, "log", "-1", "--format=%s", "origin/main").strip(), "aurlock: approvals")
        self.assertIn("pacman -Syu", self.log)
        self.assertIn("gitleaks git", self.log)

    def test_the_other_machines_approvals_arrive_before_aurlock_runs(self):
        self.run_update(self.b, NEW_APPROVAL=LINE.format(name="from-b").strip())
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("from-b ", self.seen)

    def test_package_owned_tools_are_not_self_updated(self):
        self.run_update(self.a)
        self.assertIn("claude update", self.log)
        self.assertNotIn("codex", self.log)
        self.assertNotIn("agy", self.log)

    def test_uncommitted_work_elsewhere_is_left_alone(self):
        (self.a / "README").write_text("someone else's edit\n")
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.a / "README").read_text(), "someone else's edit\n")
        self.assertEqual(self.git(self.a, "show", "--name-only", "--format=", "HEAD").strip(), APPROVED)

    def test_gitleaks_failure_keeps_the_commit_local(self):
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip(), GITLEAKS_EXIT="1")
        self.assertIn("gitleaks flagged", result.stderr)
        self.assertEqual(result.returncode, 1)
        self.git(self.a, "fetch", "-q")
        self.assertNotIn("foo ", self.remote_file())
        self.assertIn("foo ", self.git(self.a, "show", f"HEAD:{APPROVED}"))

    def test_an_unexpected_line_is_not_pushed(self):
        result = self.run_update(self.a, NEW_APPROVAL="foo /home/someone/secret")
        self.assertIn("lines aurlock doesn't write", result.stderr)
        self.assertEqual(result.returncode, 1)
        self.git(self.a, "fetch", "-q")
        self.assertNotIn("secret", self.remote_file())

    def test_other_unpushed_commits_block_the_push(self):
        (self.a / "README").write_text("local work\n")
        self.git(self.a, "commit", "-q", "-am", "local work")
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip())
        self.assertIn("isn't an approvals commit", result.stderr)
        self.assertEqual(result.returncode, 1)
        self.git(self.a, "fetch", "-q")
        self.assertNotIn("foo ", self.remote_file())

    def test_diverged_approvals_are_rebased_and_both_pushed(self):
        self.run_update(self.b, NEW_APPROVAL=LINE.format(name="from-b").strip())
        with open(self.a / APPROVED, "a") as handle:
            handle.write(LINE.format(name="zz-from-a"))
        self.git(self.a, "commit", "-q", "-am", "aurlock: approvals")
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git(self.a, "fetch", "-q")
        remote = self.remote_file()
        self.assertIn("from-b ", remote)
        self.assertIn("zz-from-a ", remote)

    def test_both_sides_changing_one_package_stops_before_upgrading(self):
        self.run_update(self.b, NEW_APPROVAL=LINE.format(name="same").strip())
        with open(self.a / APPROVED, "a") as handle:
            handle.write(LINE.format(name="same").replace("1.0-1", "2.0-1").replace("a" * 40, "b" * 40))
        self.git(self.a, "commit", "-q", "-am", "aurlock: approvals")
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflict", result.stderr)
        self.assertNotIn("pacman", self.log)
        self.assertFalse((self.a / ".git/rebase-merge").exists())

    def test_a_dirty_diverged_checkout_stops_before_upgrading(self):
        self.run_update(self.b, NEW_APPROVAL=LINE.format(name="from-b").strip())
        (self.a / "README").write_text("dirty\n")
        with open(self.a / APPROVED, "a") as handle:
            handle.write(LINE.format(name="from-a"))
        self.git(self.a, "commit", "-q", "-m", "aurlock: approvals", "--", APPROVED)
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 1)
        self.assertIn("clean checkout", result.stderr)
        self.assertNotIn("pacman", self.log)

    def test_aurlock_failure_is_the_exit_status_and_approvals_still_push(self):
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip(), AURLOCK_EXIT="3")
        self.assertEqual(result.returncode, 3)
        self.git(self.a, "fetch", "-q")
        self.assertIn("foo ", self.remote_file())


    def test_private_text_cannot_ride_along(self):
        cases = {
            "comment": "# approved on laptop.lan by someone@mail.example",
            "maintainer": "foo " + "a" * 40 + " 1.0-1 someone@mail.example 2026-11-18 offline",
            "network": "foo " + "a" * 40 + " 1.0-1 someone 2026-11-18 maybe",
            "plusplus": "++ host.lan 10.0.0.5",
        }
        for label, line in cases.items():
            with self.subTest(label):
                result = self.run_update(self.a, NEW_APPROVAL=line)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.git(self.a, "fetch", "-q")
                self.assertNotIn(line, self.git(self.a, "log", "-p", "origin/main"))
                self.git(self.a, "reset", "-q", "--hard", "origin/main")

    def test_a_line_added_and_removed_before_the_push_is_still_caught(self):
        with open(self.a / APPROVED, "a") as handle:
            handle.write("# host.lan\n")
        self.git(self.a, "commit", "-q", "-am", "aurlock: approvals")
        content = (self.a / APPROVED).read_text().replace("# host.lan\n", "")
        (self.a / APPROVED).write_text(content + LINE.format(name="foo"))
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 1)
        self.git(self.a, "fetch", "-q")
        self.assertNotIn("host.lan", self.git(self.a, "log", "-p", "origin/main"))

    def test_a_personal_identity_stops_before_upgrading(self):
        result = self.run_update(self.a, GIT_AUTHOR_EMAIL="real@mail.example",
                                 NEW_APPROVAL=LINE.format(name="foo").strip())
        self.assertEqual(result.returncode, 1)
        self.assertIn("noreply", result.stderr)
        self.assertNotIn("pacman", self.log)

    def test_a_feature_branch_is_refused(self):
        self.git(self.a, "remote", "set-head", "origin", "main")
        self.git(self.a, "switch", "-q", "-c", "feat")
        self.git(self.a, "push", "-q", "-u", "origin", "feat")
        result = self.run_update(self.a)
        self.assertEqual(result.returncode, 1)
        self.assertIn("default branch", result.stderr)

    def test_a_push_rejected_mid_run_pulls_and_pushes(self):
        hook = (f"cd {self.b} && printf '%s\\n' '{LINE.format(name='from-b').strip()}' >> {APPROVED} "
                f"&& git commit -q -am 'aurlock: approvals' && git push -q origin main")
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="zz-from-a").strip(), AURLOCK_HOOK=hook)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after pulling", result.stdout)
        self.git(self.a, "fetch", "-q")
        self.assertIn("from-b ", self.remote_file())
        self.assertIn("zz-from-a ", self.remote_file())

    def test_a_rebase_started_during_the_run_leaves_approvals_uncommitted(self):
        hook = f"mkdir {self.a}/.git/rebase-merge"
        result = self.run_update(self.a, NEW_APPROVAL=LINE.format(name="foo").strip(), AURLOCK_HOOK=hook)
        self.assertEqual(result.returncode, 1)
        self.assertIn("left uncommitted", result.stderr)
        self.assertIn("foo ", (self.a / APPROVED).read_text())
        self.assertEqual(self.git(self.a, "rev-list", "--count", "origin/main..HEAD").strip(), "0")


class MergeDriver(unittest.TestCase):
    """merge-approvals on its own: BASE OURS THEIRS, result in OURS."""

    DRIVER = SCRIPT.parents[3] / "merge-approvals"

    def merge(self, base, ours, theirs):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, lines in (("base", base), ("ours", ours), ("theirs", theirs)):
                path = pathlib.Path(directory) / name
                path.write_text(HEADER + "".join(
                    LINE.format(name=n).replace("1.0-1", v).replace("a" * 40, v * 40) for n, v in lines))
                paths.append(str(path))
            before = pathlib.Path(paths[1]).read_text()
            code = subprocess.run([str(self.DRIVER), *paths]).returncode
            after = pathlib.Path(paths[1]).read_text()
        if code:
            self.assertEqual(after, before, "a conflict leaves OURS untouched")
            return None
        return [(line.split()[0], line.split()[2]) for line in after.splitlines() if not line.startswith("#")]

    def test_additions_on_both_sides_keep_both(self):
        self.assertEqual(self.merge([("a", "1")], [("a", "1"), ("b", "1")], [("a", "1"), ("c", "1")]),
                         [("a", "1"), ("b", "1"), ("c", "1")])

    def test_a_one_sided_change_or_removal_wins(self):
        self.assertEqual(self.merge([("a", "1"), ("b", "1")], [("a", "1"), ("b", "1")], [("a", "2")]),
                         [("a", "2")])
        self.assertEqual(self.merge([("a", "1"), ("b", "1")], [("b", "1")], [("a", "1"), ("b", "2")]),
                         [("b", "2")])

    def test_the_same_change_on_both_sides_is_clean(self):
        self.assertEqual(self.merge([("a", "1")], [("a", "2")], [("a", "2")]), [("a", "2")])

    def test_an_empty_base_keeps_both_additions(self):
        with tempfile.TemporaryDirectory() as directory:
            base, ours, theirs = (pathlib.Path(directory) / n for n in ("base", "ours", "theirs"))
            base.write_text("")
            ours.write_text(LINE.format(name="a"))
            theirs.write_text(LINE.format(name="b"))
            self.assertEqual(subprocess.run([str(self.DRIVER), base, ours, theirs]).returncode, 0)
            self.assertEqual([line.split()[0] for line in ours.read_text().splitlines()], ["a", "b"])

    def test_the_same_commit_approved_on_two_dates_is_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            base, ours, theirs = (pathlib.Path(directory) / n for n in ("base", "ours", "theirs"))
            base.write_text(HEADER)
            ours.write_text(HEADER + LINE.format(name="a"))
            theirs.write_text(HEADER + LINE.format(name="a").replace("2026-11-18", "2026-11-19"))
            self.assertEqual(subprocess.run([str(self.DRIVER), base, ours, theirs]).returncode, 0)
            self.assertEqual(ours.read_text().count("a " + "a" * 40), 1)

    def test_divergent_changes_conflict(self):
        self.assertIsNone(self.merge([("a", "1")], [("a", "2")], [("a", "3")]))
        self.assertIsNone(self.merge([], [("a", "2")], [("a", "3")]))
        self.assertIsNone(self.merge([("a", "1")], [], [("a", "3")]))
        self.assertIsNone(self.merge([("a", "1")], [("a", "2")], []))

if __name__ == "__main__":
    unittest.main()
