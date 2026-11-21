#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Unit and integration tests for scripts/witness.py.

Run: python3 -I -B tests/test_witness.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
WITNESS_SCRIPT = SCRIPTS_DIR / "witness.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import witness

BASE_CALC_PY = '''"""Small calculator fixture module."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def divide(a: float, b: float) -> float:
    """Divide a by b (pre-existing bug: no zero check)."""
    return a / b


def buggy_preexisting(x: int) -> int:
    """Always returns wrong value even on base."""
    return x - 1
'''

CANDIDATE_CALC_PY = '''"""Small calculator fixture module."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers (regression: off-by-one)."""
    return a + b + 1


def divide(a: float, b: float) -> float:
    """Divide a by b (pre-existing bug: no zero check)."""
    return a / b


def buggy_preexisting(x: int) -> int:
    """Always returns wrong value even on base."""
    return x - 1


def multiply(a: int, b: int) -> int:
    """Multiply two integers (new function with bug on zero)."""
    if b == 0:
        return 1
    return a * b
'''

CANDIDATE_EXTRA_PY = '''"""Tracked file modified uncommitted in working tree."""


def greeting(name: str) -> str:
    return f"hello {name}"
'''

CANDIDATE_EXTRA_UNCOMMITTED_PY = '''"""Tracked file modified uncommitted in working tree."""


def greeting(name: str) -> str:
    return f"uncommitted hello {name}"
'''

SPEC_MD = """# Calculator Spec

The add function   must return
the exact sum of a and b.

The multiply function must return 0 when either operand is 0.
"""


def run_cmd(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and capture UTF-8 stdout/stderr."""
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=check,
    )


def run_witness_cli(
    args: list[str],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke witness.py via isolated Python subprocess."""
    return run_cmd(
        [sys.executable, "-I", "-B", str(WITNESS_SCRIPT), *args],
        env=env,
        check=False,
    )


def write_json(path: Path, data: Any) -> Path:
    """Write formatted JSON to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def snapshot_repo_state(repo: Path) -> dict[str, Any]:
    """Capture complete git, index, and working-tree state for byte-for-byte comparison."""
    ro_env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    index_sha256 = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
    status = run_cmd(
        ["git", "-C", str(repo), "-c", "diff.autoRefreshIndex=false", "status", "--porcelain"],
        env=ro_env,
    ).stdout
    head = run_cmd(["git", "-C", str(repo), "rev-parse", "HEAD"], env=ro_env).stdout.strip()
    stash = run_cmd(["git", "-C", str(repo), "stash", "list"], env=ro_env).stdout
    cached = run_cmd(
        ["git", "-C", str(repo), "-c", "diff.autoRefreshIndex=false", "diff", "--cached"],
        env=ro_env,
    ).stdout
    files: dict[str, tuple[str, bytes | str]] = {}
    for root_str, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        root_path = Path(root_str)
        for name in sorted(filenames):
            full = root_path / name
            rel = full.relative_to(repo).as_posix()
            if full.is_symlink():
                files[rel] = ("symlink", os.readlink(full))
            else:
                files[rel] = ("file", full.read_bytes())
    return {
        "index_sha256": index_sha256,
        "status": status,
        "head": head,
        "stash": stash,
        "cached": cached,
        "files": files,
    }


def strip_duration(obj: Any) -> Any:
    """Recursively remove 'duration_s' keys for determinism comparisons."""
    if isinstance(obj, dict):
        return {k: strip_duration(v) for k, v in obj.items() if k != "duration_s"}
    if isinstance(obj, list):
        return [strip_duration(v) for v in obj]
    return obj


def create_fixture_repo(root: Path) -> Path:
    """Create a temp git repo with base commit, candidate commit, uncommitted edit, and untracked files."""
    repo = root / "repo"
    repo.mkdir()
    run_cmd(["git", "init", "-b", "main", str(repo)])
    run_cmd(["git", "-C", str(repo), "config", "user.name", "Test User"])
    run_cmd(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
    run_cmd(["git", "-C", str(repo), "config", "commit.gpgsign", "false"])

    # Base commit.
    (repo / ".gitignore").write_text(".setup_cache/\n.cache/\n*.pyc\n__pycache__/\n", encoding="utf-8")
    (repo / "calc.py").write_text(BASE_CALC_PY, encoding="utf-8")
    (repo / "extra.py").write_text(CANDIDATE_EXTRA_PY, encoding="utf-8")
    (repo / "spec.md").write_text(SPEC_MD, encoding="utf-8")
    run_cmd(["git", "-C", str(repo), "add", "-A"])
    run_cmd(["git", "-C", str(repo), "commit", "-m", "base commit"])
    run_cmd(["git", "-C", str(repo), "tag", "base-tag"])

    # Candidate commit: introduces regression in add() and new buggy function multiply().
    (repo / "calc.py").write_text(CANDIDATE_CALC_PY, encoding="utf-8")
    (repo / "new_mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    run_cmd(["git", "-C", str(repo), "add", "-A"])
    run_cmd(["git", "-C", str(repo), "commit", "-m", "candidate commit"])

    # Uncommitted staged + unstaged tracked edit, plus untracked regular file, executable, and symlink.
    (repo / "staged.txt").write_text("staged tracked content\n", encoding="utf-8")
    run_cmd(["git", "-C", str(repo), "add", "staged.txt"])
    (repo / "extra.py").write_text(CANDIDATE_EXTRA_UNCOMMITTED_PY, encoding="utf-8")
    (repo / "untracked.py").write_text("UNTRACKED_FLAG = 'present'\n", encoding="utf-8")
    script_path = repo / "untracked_tool.sh"
    script_path.write_text("#!/bin/sh\necho tool-ok\n", encoding="utf-8")
    script_path.chmod(0o755)
    os.symlink("untracked.py", repo / "untracked_link.py")

    return repo


class MaterializeAndCleanupTests(unittest.TestCase):
    """Tests for materialize and cleanup subcommands."""

    def test_materialize_preserves_repo_and_cleanup_removes_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = create_fixture_repo(tmp_path)
            out_dir = tmp_path / "trees"

            # Touch a clean tracked file so its cached stat info in .git/index goes stale.
            spec_path = repo / "spec.md"
            spec_stat = spec_path.stat()
            os.utime(spec_path, (spec_stat.st_atime + 60, spec_stat.st_mtime + 60))

            before = snapshot_repo_state(repo)
            worktrees_before = run_cmd(
                ["git", "-C", str(repo), "worktree", "list", "--porcelain"]
            ).stdout

            poisoned_index = tmp_path / "inherited_git_index"
            poisoned_env = {
                **os.environ,
                "GIT_INDEX_FILE": str(poisoned_index),
                "GIT_DIR": str(tmp_path / "nonexistent_git_dir"),
                "GIT_WORK_TREE": str(tmp_path / "nonexistent_work_tree"),
                "GIT_OBJECT_DIRECTORY": str(tmp_path / "nonexistent_objects"),
                "GIT_COMMON_DIR": str(tmp_path / "nonexistent_common"),
            }
            proc = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(out_dir)],
                env=poisoned_env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(poisoned_index.exists())

            # User repo (including .git/index bytes) must be byte-for-byte untouched.
            after = snapshot_repo_state(repo)
            self.assertEqual(before, after)

            # trees.json fields present and accurate.
            trees_json_path = out_dir / "trees.json"
            self.assertTrue(trees_json_path.is_file())
            trees_data = json.loads(trees_json_path.read_text(encoding="utf-8"))
            expected_keys = {
                "version",
                "repo",
                "base_ref",
                "base_sha",
                "head_sha",
                "patch_sha256",
                "untracked",
                "candidate_id",
                "base_snapshot",
                "candidate_snapshot",
            }
            self.assertEqual(set(trees_data.keys()), expected_keys)
            self.assertEqual(trees_data["version"], 1)
            self.assertEqual(trees_data["base_ref"], "base-tag")
            self.assertEqual(trees_data["base_snapshot"], trees_data["base_sha"])
            self.assertEqual(
                trees_data["untracked"],
                ["untracked.py", "untracked_link.py", "untracked_tool.sh"],
            )
            self.assertTrue(trees_data["candidate_id"].startswith(f"{trees_data['head_sha'][:12]}+"))
            self.assertEqual(len(trees_data["candidate_id"]), 25)

            # Base tree has base commit only.
            base_dir = out_dir / "base"
            self.assertIn("return a + b\n", (base_dir / "calc.py").read_text(encoding="utf-8"))
            self.assertFalse((base_dir / "new_mod.py").exists())
            self.assertFalse((base_dir / "untracked.py").exists())

            # Candidate tree has committed, staged, unstaged, and untracked changes with modes/symlinks.
            cand_dir = out_dir / "candidate"
            self.assertIn("return a + b + 1\n", (cand_dir / "calc.py").read_text(encoding="utf-8"))
            self.assertEqual(
                (cand_dir / "staged.txt").read_text(encoding="utf-8"),
                "staged tracked content\n",
            )
            self.assertIn(
                "uncommitted hello", (cand_dir / "extra.py").read_text(encoding="utf-8")
            )
            self.assertEqual(
                (cand_dir / "untracked.py").read_text(encoding="utf-8"),
                "UNTRACKED_FLAG = 'present'\n",
            )
            cand_tool_mode = stat.S_IMODE((cand_dir / "untracked_tool.sh").lstat().st_mode)
            self.assertTrue(cand_tool_mode & stat.S_IXUSR)
            self.assertTrue((cand_dir / "untracked_link.py").is_symlink())
            self.assertEqual(os.readlink(cand_dir / "untracked_link.py"), "untracked.py")

            # Add an unrelated file to out_dir to ensure cleanup only removes files it created.
            extra_note = out_dir / "caller_note.txt"
            extra_note.write_text("keep me\n", encoding="utf-8")

            clean_proc = run_witness_cli(["cleanup", "--out", str(out_dir)])
            self.assertEqual(clean_proc.returncode, 0, clean_proc.stderr)
            self.assertFalse((out_dir / "base").exists())
            self.assertFalse((out_dir / "candidate").exists())
            self.assertFalse((out_dir / "trees.json").exists())
            self.assertTrue(extra_note.is_file())

            worktrees_after = run_cmd(
                ["git", "-C", str(repo), "worktree", "list", "--porcelain"]
            ).stdout
            self.assertEqual(worktrees_before, worktrees_after)
            self.assertEqual(before, snapshot_repo_state(repo))

    def test_materialize_refuses_out_inside_repo_and_nonempty_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = create_fixture_repo(tmp_path)

            inside = repo / "nested_out"
            proc_inside = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(inside)]
            )
            self.assertEqual(proc_inside.returncode, 3)
            self.assertFalse(inside.exists())

            proc_root = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(repo)]
            )
            self.assertEqual(proc_root.returncode, 3)

            nonempty = tmp_path / "nonempty"
            nonempty.mkdir()
            (nonempty / "already.txt").write_text("hi\n", encoding="utf-8")
            proc_nonempty = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(nonempty)]
            )
            self.assertEqual(proc_nonempty.returncode, 3)

            # Cleanup without trees.json must also exit 3.
            proc_clean = run_witness_cli(["cleanup", "--out", str(tmp_path / "missing_trees")])
            self.assertEqual(proc_clean.returncode, 3)

    def test_materialize_disables_repository_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = create_fixture_repo(tmp_path)
            out_dir = tmp_path / "trees"
            marker = tmp_path / "hook_marker.txt"
            hooks_dir = repo / ".git" / "hooks"

            for hook_name in ("post-checkout", "prepare-commit-msg"):
                hook_path = hooks_dir / hook_name
                hook_path.write_text(
                    f"#!/bin/sh\necho {hook_name} >> '{marker}'\nexit 1\n",
                    encoding="utf-8",
                )
                hook_path.chmod(0o755)

            proc = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(out_dir)]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists(), "repository hook executed during materialize")
            self.assertTrue((out_dir / "trees.json").is_file())

            clean_proc = run_witness_cli(["cleanup", "--out", str(out_dir)])
            self.assertEqual(clean_proc.returncode, 0, clean_proc.stderr)
            self.assertFalse(marker.exists(), "repository hook executed during cleanup")


class StatusAndExecutionTests(unittest.TestCase):
    """Tests for witness execution, statuses, isolation, setup, waivers, gate, and reports."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.repo = create_fixture_repo(self.tmp_path)
        self.trees_dir = self.tmp_path / "trees"
        proc = run_witness_cli(
            [
                "materialize",
                "--repo",
                str(self.repo),
                "--base",
                "base-tag",
                "--out",
                str(self.trees_dir),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self) -> None:
        run_witness_cli(["cleanup", "--out", str(self.trees_dir)])
        self._tmp.cleanup()

    def _default_seats(self, *, second_status: str = "completed") -> Path:
        return write_json(
            self.tmp_path / "seats.json",
            {
                "seats": [
                    {
                        "name": "seat-a",
                        "required": True,
                        "status": "completed",
                        "provider": "prov-a",
                        "model": "model-a",
                    },
                    {
                        "name": "seat-b",
                        "required": True,
                        "status": second_status,
                        "provider": "prov-b",
                        "model": "model-b",
                    },
                ]
            },
        )

    def _valid_intent(self) -> dict[str, str]:
        return {
            "file": "spec.md",
            # Deliberately varies whitespace/newlines to test whitespace normalization.
            "quote": "The add function must return the exact sum of a and b.",
        }

    def test_all_core_statuses_and_expectations(self) -> None:
        counter_file = self.tmp_path / "flaky_counter.txt"
        seats_file = self._default_seats()

        # Patch that applies only to new_mod.py (missing in base -> base n/a -> introduced).
        new_mod_patch = (
            "diff --git a/new_mod.py b/new_mod.py\n"
            "--- a/new_mod.py\n"
            "+++ b/new_mod.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 42\n"
            "+VALUE = 99\n"
        )

        # Patch that never applies on candidate.
        bad_patch = (
            "diff --git a/calc.py b/calc.py\n"
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1 +1 @@\n"
            "-THIS LINE DOES NOT EXIST\n"
            "+REPLACEMENT\n"
        )

        seat_a_entries = write_json(
            self.tmp_path / "entries_a.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "reg-add",
                        "type": "finding",
                        "claim": "add() returns a + b + 1 instead of a + b",
                        "severity": "blocker",
                        "cause": "./calc.py:4",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                "import calc; assert calc.add(2, 3) == 5, f'got {calc.add(2, 3)}'",
                            ],
                        },
                    },
                    {
                        "id": "new-mod-patch",
                        "type": "finding",
                        "claim": "new_mod value fails patched invariant on new code",
                        "severity": "should",
                        "cause": "new_mod.py:1",
                        "intent": self._valid_intent(),
                        "witness": {
                            "patch": new_mod_patch,
                            "run": [
                                sys.executable,
                                "-c",
                                "import new_mod; assert new_mod.VALUE == 42",
                            ],
                        },
                    },
                    {
                        "id": "new-fn-missing-in-base",
                        "type": "finding",
                        "claim": "multiply(5, 0) must return 0",
                        "severity": "should",
                        "cause": "calc.py:19",
                        "intent": {
                            "file": "spec.md",
                            "quote": "The multiply function must return 0 when either operand is 0.",
                        },
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import calc, sys\n"
                                    "if not hasattr(calc, 'multiply'): sys.exit(127)\n"
                                    "assert calc.multiply(5, 0) == 0\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "preexisting-bug",
                        "type": "finding",
                        "claim": "buggy_preexisting fails on both base and candidate",
                        "severity": "blocker",
                        "cause": "calc.py:14",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                "import calc; assert calc.buggy_preexisting(5) == 5",
                            ],
                        },
                    },
                    {
                        "id": "flaky-case",
                        "type": "finding",
                        "claim": "alternates pass and fail across runs",
                        "severity": "should",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import pathlib, sys\n"
                                    f"p = pathlib.Path({str(counter_file)!r})\n"
                                    "n = int(p.read_text()) if p.exists() else 0\n"
                                    "p.write_text(str(n + 1))\n"
                                    "sys.exit(0 if n % 2 == 0 else 1)\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "not-repro",
                        "type": "finding",
                        "claim": "false alarm: uncommitted extra.py greeting actually passes",
                        "severity": "should",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import extra, untracked; "
                                    "assert extra.greeting('x') == 'uncommitted hello x'; "
                                    "assert untracked.UNTRACKED_FLAG == 'present'"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "held-ok",
                        "type": "held",
                        "claim": "divide handles positive denominator",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                "import calc; assert calc.divide(6, 2) == 3.0",
                            ],
                        },
                    },
                    {
                        "id": "held-fails",
                        "type": "held",
                        "claim": "thought add(1, 1) held, actually broken",
                        "cause": "calc.py:4",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                "import calc; assert calc.add(1, 1) == 2",
                            ],
                        },
                    },
                    {
                        "id": "expect-nonzero-and-regex",
                        "type": "held",
                        "claim": "divide by zero raises ZeroDivisionError with nonzero exit and regex match",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import calc, sys\n"
                                    "try:\n"
                                    "    calc.divide(1, 0)\n"
                                    "except ZeroDivisionError:\n"
                                    "    print('HEADER\\nMATCH_TOKEN_123\\nFOOTER')\n"
                                    "    sys.exit(2)\n"
                                ),
                            ],
                            "expect": {
                                "exit": "nonzero",
                                "stdout_regex": "^MATCH_TOKEN_\\d+$",
                            },
                        },
                    },
                    {
                        "id": "adv-note",
                        "type": "advisory",
                        "claim": "Consider documenting divide edge cases",
                        "cause": "calc.py:9",
                    },
                ],
            },
        )

        seat_b_entries = write_json(
            self.tmp_path / "entries_b.json",
            {
                "seat": "seat-b",
                "in_scope": True,
                "entries": [
                    {
                        "id": "broken-127",
                        "type": "finding",
                        "claim": "missing command exits 127",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "import sys; sys.exit(127)"],
                        },
                    },
                    {
                        "id": "broken-build",
                        "type": "finding",
                        "claim": "failing build marks broken witness",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "build": [sys.executable, "-c", "import sys; sys.exit(1)"],
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "broken-unknown-key",
                        "type": "finding",
                        "claim": "entry with typo key",
                        "severity": "blocker",
                        "typo_field": True,
                        "intent": self._valid_intent(),
                        "witness": {"run": [sys.executable, "-c", "pass"]},
                    },
                    {
                        "id": "broken-missing-citation",
                        "type": "finding",
                        "claim": "quote does not exist in spec.md",
                        "severity": "blocker",
                        "intent": {
                            "file": "spec.md",
                            "quote": "This sentence is definitely not in the spec.",
                        },
                        "witness": {"run": [sys.executable, "-c", "pass"]},
                    },
                    {
                        "id": "broken-dotdot-files",
                        "type": "finding",
                        "claim": "path traversal in files",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "files": {"../escape.py": "x = 1\n"},
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "broken-dotdot-cwd",
                        "type": "finding",
                        "claim": "path traversal in cwd",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "cwd": "../outside",
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "broken-dotdot-patch",
                        "type": "finding",
                        "claim": "path traversal in patch",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "patch": (
                                "diff --git a/../escape.py b/../escape.py\n"
                                "--- a/../escape.py\n"
                                "+++ b/../escape.py\n"
                                "@@ -1 +1 @@\n"
                                "-a\n"
                                "+b\n"
                            ),
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "broken-bad-patch",
                        "type": "finding",
                        "claim": "patch does not apply on candidate",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "patch": bad_patch,
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "broken-witness-unknown-key",
                        "type": "finding",
                        "claim": "typo inside witness object",
                        "severity": "should",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "pass"],
                            "timeout_sec": 10,
                        },
                    },
                    {
                        "id": "broken-expect-unknown-key",
                        "type": "finding",
                        "claim": "typo inside expect object",
                        "severity": "should",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "pass"],
                            "expect": {"exit_code": 0},
                        },
                    },
                    {
                        "id": "broken-nonzero-127",
                        "type": "finding",
                        "claim": "exit 127 is broken even when expect.exit is nonzero",
                        "severity": "should",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "import sys; sys.exit(127)"],
                            "expect": {"exit": "nonzero"},
                        },
                    },
                    {
                        "id": "broken-advisory-with-witness",
                        "type": "advisory",
                        "claim": "advisory must not have a witness",
                        "witness": {"run": [sys.executable, "-c", "pass"]},
                    },
                    {
                        "id": "regex-mismatch-introduced",
                        "type": "finding",
                        "claim": "stdout_regex mismatch fails candidate while base matches",
                        "severity": "nit",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import calc, sys; print(f'SUM={calc.add(2, 3)}'); "
                                    "print('ERR_TAG', file=sys.stderr)"
                                ),
                            ],
                            "expect": {
                                "exit": 0,
                                "stdout_regex": "^SUM=5$",
                                "stderr_regex": "^ERR_TAG$",
                            },
                        },
                    },
                ],
            },
        )

        report_json = self.tmp_path / "report.json"
        report_md = self.tmp_path / "report.md"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(seat_a_entries),
                "--entries",
                str(seat_b_entries),
                "--runs",
                "2",
                "--report-json",
                str(report_json),
                "--report-md",
                str(report_md),
            ]
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)

        report = json.loads(report_json.read_text(encoding="utf-8"))
        self.assertEqual(report["gate"], "fail")
        by_id = {e["id"]: e for e in report["entries"]}

        self.assertEqual(by_id["seat-a:reg-add"]["status"], "introduced")
        self.assertEqual(
            [r["outcome"] for r in by_id["seat-a:reg-add"]["runs"]["base"]],
            ["pass", "pass"],
        )

        self.assertEqual(by_id["seat-a:new-mod-patch"]["status"], "introduced")
        self.assertEqual(
            [r["outcome"] for r in by_id["seat-a:new-mod-patch"]["runs"]["base"]],
            ["n/a", "n/a"],
        )

        self.assertEqual(by_id["seat-a:new-fn-missing-in-base"]["status"], "introduced")
        self.assertEqual(
            [r["outcome"] for r in by_id["seat-a:new-fn-missing-in-base"]["runs"]["base"]],
            ["n/a", "n/a"],
        )

        self.assertEqual(by_id["seat-a:preexisting-bug"]["status"], "pre-existing")
        self.assertEqual(by_id["seat-a:flaky-case"]["status"], "flaky")
        self.assertEqual(by_id["seat-a:not-repro"]["status"], "not reproduced")
        self.assertEqual(by_id["seat-a:held-ok"]["status"], "held")
        self.assertEqual(by_id["seat-a:held-fails"]["status"], "introduced")
        self.assertEqual(by_id["seat-a:held-fails"]["effective_severity"], "should")
        self.assertEqual(by_id["seat-a:expect-nonzero-and-regex"]["status"], "held")
        self.assertEqual(by_id["seat-a:adv-note"]["status"], "advisory")

        # Broken witness checks.
        for bid in (
            "seat-b:broken-127",
            "seat-b:broken-build",
            "seat-b:broken-unknown-key",
            "seat-b:broken-missing-citation",
            "seat-b:broken-dotdot-files",
            "seat-b:broken-dotdot-cwd",
            "seat-b:broken-dotdot-patch",
            "seat-b:broken-bad-patch",
            "seat-b:broken-witness-unknown-key",
            "seat-b:broken-expect-unknown-key",
            "seat-b:broken-nonzero-127",
            "seat-b:broken-advisory-with-witness",
        ):
            self.assertEqual(by_id[bid]["status"], "broken witness", bid)
            self.assertTrue(by_id[bid]["reason"], bid)

        self.assertIn("typo_field", by_id["seat-b:broken-unknown-key"]["reason"])
        self.assertIn("timeout_sec", by_id["seat-b:broken-witness-unknown-key"]["reason"])
        self.assertIn("exit_code", by_id["seat-b:broken-expect-unknown-key"]["reason"])
        self.assertEqual(
            by_id["seat-b:broken-missing-citation"]["reason"],
            "citation not found: spec.md",
        )
        self.assertEqual(by_id["seat-b:regex-mismatch-introduced"]["status"], "introduced")
        self.assertEqual(
            [r["outcome"] for r in by_id["seat-b:regex-mismatch-introduced"]["runs"]["base"]],
            ["pass", "pass"],
        )

        # Verify strict entry ordering by status priority then namespaced id.
        sort_keys = [witness.entry_sort_key(e) for e in report["entries"]]
        self.assertEqual(sort_keys, sorted(sort_keys))

        # Groups: seat-a:reg-add (./calc.py:4) and seat-a:held-fails (calc.py:4) share normalized cause.
        calc4_groups = [g for g in report["groups"] if g["cause"] == "calc.py:4"]
        self.assertEqual(len(calc4_groups), 1)
        self.assertEqual(calc4_groups[0]["ids"], ["seat-a:held-fails", "seat-a:reg-add"])

        # Markdown contains all required sections in exact order.
        md = report_md.read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Council gate: FAIL"))
        section_headers = [
            "## Coverage",
            "## Blocking",
            "## Waived or overridden",
            "## Unverified",
            "## Pre-existing",
            "## Held",
            "## Not reproduced",
            "## Advisory",
        ]
        positions = [md.index(header) for header in section_headers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("candidate fail 2/2 (exit 1); base pass 2/2", md)
        self.assertIn("`seat-a:not-repro`", md.split("## Not reproduced")[1].split("## Advisory")[0])

    def test_timeouts_both_ways_and_process_group_kill(self) -> None:
        seats_file = self._default_seats()
        child_pid_file = self.tmp_path / "grandchild.pid"
        spawn_sleeper_code = (
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))\n"
            "time.sleep(30)\n"
        )
        entries_file = write_json(
            self.tmp_path / "timeout_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "timeout-broken",
                        "type": "finding",
                        "claim": "slow command with timeout_is_failure=false is broken witness",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", spawn_sleeper_code],
                            "timeout_s": 1,
                            "timeout_is_failure": False,
                        },
                    },
                    {
                        "id": "timeout-failure",
                        "type": "finding",
                        "claim": "hang on candidate only with timeout_is_failure=true is introduced",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import os, time\n"
                                    "if os.environ['COUNCIL_ROLE'] == 'candidate':\n"
                                    "    time.sleep(10)\n"
                                ),
                            ],
                            "timeout_s": 1,
                            "timeout_is_failure": True,
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "timeout_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:timeout-broken"]["status"], "broken witness")
        self.assertEqual(by_id["seat-a:timeout-failure"]["status"], "introduced")
        self.assertEqual(by_id["seat-a:timeout-failure"]["runs"]["candidate"][0]["outcome"], "fail")
        self.assertEqual(by_id["seat-a:timeout-failure"]["runs"]["base"][0]["outcome"], "pass")

        # Verify grandchild process in the witness process group was terminated.
        self.assertTrue(child_pid_file.is_file())
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_isolation_and_setup_preserves_ignored_cache(self) -> None:
        seats_file = self._default_seats()
        external_doc = self.tmp_path / "external_contract.txt"
        external_doc.write_text("External   contract\nline two.\n", encoding="utf-8")
        entries_file = write_json(
            self.tmp_path / "isolation_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "z-witness-a-mutates",
                        "type": "held",
                        "claim": "writes stray file, mutates tracked file, and checks setup cache",
                        "intent": self._valid_intent(),
                        "citations": [
                            {"file": str(external_doc), "quote": "External contract line two."},
                        ],
                        "witness": {
                            "files": {"helper_from_a.py": "HELPER = 1\n"},
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                    "Path('stray_from_a.txt').write_text('dirty')\n"
                                    "Path('calc.py').write_text('# overwritten by A\\n')\n"
                                    "Path('spec.md').write_text('# clobbered by A\\n')\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "a-witness-b-asserts-clean",
                        "type": "held",
                        "claim": "asserts stray file and tracked mutation from A are gone while setup cache survives",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path\n"
                                    "import os\n"
                                    "assert not Path('stray_from_a.txt').exists()\n"
                                    "assert not Path('helper_from_a.py').exists()\n"
                                    "assert 'return a + b + 1' in Path('calc.py').read_text()\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                    "assert os.environ['COUNCIL_ROLE'] == 'candidate'\n"
                                    "assert Path(os.environ['COUNCIL_BASE_TREE']).is_dir()\n"
                                    "assert Path(os.environ['COUNCIL_CANDIDATE_TREE']).is_dir()\n"
                                ),
                            ],
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "iso_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "2",
                "--setup",
                "mkdir -p .setup_cache && echo ok > .setup_cache/ready.txt",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:z-witness-a-mutates"]["status"], "held")
        self.assertEqual(by_id["seat-a:a-witness-b-asserts-clean"]["status"], "held")

        # Failing setup exits 3 and mentions tree role and output.
        bad_setup = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--setup",
                "echo setup_boom_marker >&2; exit 7",
            ]
        )
        self.assertEqual(bad_setup.returncode, 3)
        self.assertIn("base", bad_setup.stderr)
        self.assertIn("setup_boom_marker", bad_setup.stderr)

    def test_waivers_gate_subcommand_required_seat_nit_and_determinism(self) -> None:
        seats_ok = self._default_seats(second_status="completed")
        entries_file = write_json(
            self.tmp_path / "gate_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "bug-blocker",
                        "type": "finding",
                        "claim": "add regression blocker",
                        "severity": "blocker",
                        "cause": "calc.py:4",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "import calc; assert calc.add(2, 3) == 5"],
                        },
                    },
                    {
                        "id": "bug-should",
                        "type": "finding",
                        "claim": "multiply zero should",
                        "severity": "should",
                        "cause": "calc.py:19",
                        "intent": {
                            "file": "spec.md",
                            "quote": "The multiply function must return 0 when either operand is 0.",
                        },
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import calc, sys\n"
                                    "if not hasattr(calc, 'multiply'): sys.exit(127)\n"
                                    "assert calc.multiply(3, 0) == 0\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "bug-nit",
                        "type": "finding",
                        "claim": "introduced nit does not block alone",
                        "severity": "nit",
                        "cause": "calc.py:4",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "import calc; assert calc.add(0, 0) == 0"],
                        },
                    },
                ],
            },
        )

        # Two consecutive runs produce identical JSON once duration_s is stripped.
        rep1_path = self.tmp_path / "rep1.json"
        rep2_path = self.tmp_path / "rep2.json"
        p1 = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_ok),
                "--entries",
                str(entries_file),
                "--runs",
                "2",
                "--report-json",
                str(rep1_path),
            ]
        )
        p2 = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_ok),
                "--entries",
                str(entries_file),
                "--runs",
                "2",
                "--report-json",
                str(rep2_path),
            ]
        )
        self.assertEqual(p1.returncode, 1)
        self.assertEqual(p2.returncode, 1)
        rep1 = json.loads(rep1_path.read_text(encoding="utf-8"))
        rep2 = json.loads(rep2_path.read_text(encoding="utf-8"))
        self.assertEqual(strip_duration(rep1), strip_duration(rep2))

        # Waive bug-blocker + downgrade bug-should to nit -> only introduced nit remains -> PASS (0).
        waivers_pass = write_json(
            self.tmp_path / "waivers_pass.json",
            {
                "seat-a:bug-blocker": {
                    "action": "waive",
                    "reason": "Tracked in follow-up issue #123",
                },
                "seat-a:bug-should": {
                    "action": "severity",
                    "severity": "nit",
                    "reason": "Edge case accepted for v1",
                },
            },
        )
        run_waived_json = self.tmp_path / "run_waived.json"
        p_waived = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_ok),
                "--entries",
                str(entries_file),
                "--waivers",
                str(waivers_pass),
                "--runs",
                "2",
                "--report-json",
                str(run_waived_json),
            ]
        )
        self.assertEqual(p_waived.returncode, 0, p_waived.stderr)

        # gate subcommand reproduces the exact same report + exit code from rep1 + waivers_pass.
        gate_json = self.tmp_path / "gate_out.json"
        gate_md = self.tmp_path / "gate_out.md"
        p_gate = run_witness_cli(
            [
                "gate",
                "--report-json",
                str(rep1_path),
                "--waivers",
                str(waivers_pass),
                "--report-json-out",
                str(gate_json),
                "--report-md",
                str(gate_md),
            ]
        )
        self.assertEqual(p_gate.returncode, 0, p_gate.stderr)
        gate_rep = json.loads(gate_json.read_text(encoding="utf-8"))
        run_waived_rep = json.loads(run_waived_json.read_text(encoding="utf-8"))
        self.assertEqual(strip_duration(gate_rep), strip_duration(run_waived_rep))

        # Original severity is preserved while effective_severity is updated.
        gate_by_id = {e["id"]: e for e in gate_rep["entries"]}
        self.assertEqual(gate_by_id["seat-a:bug-should"]["severity"], "should")
        self.assertEqual(gate_by_id["seat-a:bug-should"]["effective_severity"], "nit")
        self.assertEqual(gate_by_id["seat-a:bug-blocker"]["waiver"]["action"], "waive")
        self.assertIn("Tracked in follow-up issue #123", gate_md.read_text(encoding="utf-8"))
        self.assertIn("Edge case accepted for v1", gate_md.read_text(encoding="utf-8"))

        # Upgrading bug-nit to should makes gate FAIL (1) again.
        waivers_upgrade = write_json(
            self.tmp_path / "waivers_upgrade.json",
            {
                "seat-a:bug-blocker": {"action": "waive", "reason": "Waived"},
                "seat-a:bug-should": {"action": "waive", "reason": "Waived"},
                "seat-a:bug-nit": {
                    "action": "severity",
                    "severity": "should",
                    "reason": "Actually matters for callers",
                },
            },
        )
        p_upgrade = run_witness_cli(
            ["gate", "--report-json", str(rep1_path), "--waivers", str(waivers_upgrade)]
        )
        self.assertEqual(p_upgrade.returncode, 1)

        # Unknown waiver id exits 3.
        waivers_unknown = write_json(
            self.tmp_path / "waivers_unknown.json",
            {"seat-a:does-not-exist": {"action": "waive", "reason": "typo"}},
        )
        self.assertEqual(
            run_witness_cli(
                ["gate", "--report-json", str(rep1_path), "--waivers", str(waivers_unknown)]
            ).returncode,
            3,
        )

        # Empty waiver reason exits 3.
        waivers_empty_reason = write_json(
            self.tmp_path / "waivers_empty.json",
            {"seat-a:bug-blocker": {"action": "waive", "reason": "   "}},
        )
        self.assertEqual(
            run_witness_cli(
                ["gate", "--report-json", str(rep1_path), "--waivers", str(waivers_empty_reason)]
            ).returncode,
            3,
        )

        # Introduced nit alone passes.
        nit_only = write_json(
            self.tmp_path / "nit_only.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "only-nit",
                        "type": "finding",
                        "claim": "minor nit",
                        "severity": "nit",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", "import calc; assert calc.add(1, 1) == 2"],
                        },
                    }
                ],
            },
        )
        self.assertEqual(
            run_witness_cli(
                [
                    "run",
                    "--trees",
                    str(self.trees_dir),
                    "--seats",
                    str(seats_ok),
                    "--entries",
                    str(nit_only),
                    "--runs",
                    "2",
                ]
            ).returncode,
            0,
        )

        # Required seat with status 'failed' makes gate 'unverified' (exit 2).
        seats_failed = self._default_seats(second_status="failed")
        p_unverified = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_failed),
                "--entries",
                str(nit_only),
                "--runs",
                "2",
            ]
        )
        self.assertEqual(p_unverified.returncode, 2)

    def test_input_errors_exit_3(self) -> None:
        seats_file = self._default_seats()

        # Entries file referencing unknown seat -> exit 3.
        bad_seat = write_json(
            self.tmp_path / "bad_seat.json",
            {"seat": "ghost-seat", "in_scope": True, "entries": []},
        )
        self.assertEqual(
            run_witness_cli(
                [
                    "run",
                    "--trees",
                    str(self.trees_dir),
                    "--seats",
                    str(seats_file),
                    "--entries",
                    str(bad_seat),
                ]
            ).returncode,
            3,
        )

        # Duplicate namespaced id -> exit 3.
        dup_entries = write_json(
            self.tmp_path / "dup.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {"id": "same", "type": "advisory", "claim": "first"},
                    {"id": "same", "type": "advisory", "claim": "second"},
                ],
            },
        )
        self.assertEqual(
            run_witness_cli(
                [
                    "run",
                    "--trees",
                    str(self.trees_dir),
                    "--seats",
                    str(seats_file),
                    "--entries",
                    str(dup_entries),
                ]
            ).returncode,
            3,
        )

        # Malformed top-level JSON -> exit 3.
        bad_json = self.tmp_path / "bad.json"
        bad_json.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(
            run_witness_cli(
                [
                    "run",
                    "--trees",
                    str(self.trees_dir),
                    "--seats",
                    str(seats_file),
                    "--entries",
                    str(bad_json),
                ]
            ).returncode,
            3,
        )

        # Invalid CLI argument exits 3 (never collides with exit 2 = unverified).
        self.assertEqual(run_witness_cli(["run", "--nonexistent-flag"]).returncode, 3)

    def test_nested_git_dir_removed_between_runs(self) -> None:
        seats_file = self._default_seats()
        entries_file = write_json(
            self.tmp_path / "nested_git_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "create-nested-repo",
                        "type": "held",
                        "claim": "nested git repository created by a witness is cleaned before the next run",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import subprocess\n"
                                    "from pathlib import Path\n"
                                    "nested = Path('nested_repo')\n"
                                    "assert not nested.exists(), 'nested_repo leaked from previous run'\n"
                                    "subprocess.run(['git', 'init', '-q', str(nested)], check=True)\n"
                                    "(nested / 'artifact.txt').write_text('stale', encoding='utf-8')\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "verify-no-nested-repo",
                        "type": "held",
                        "claim": "subsequent witness does not see nested git directory",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; assert not Path('nested_repo').exists()",
                            ],
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "nested_git_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "2",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:create-nested-repo"]["status"], "held")
        self.assertEqual(by_id["seat-a:verify-no-nested-repo"]["status"], "held")

    def test_background_writer_killed_and_grandchild_stdout_returns_promptly(self) -> None:
        seats_file = self._default_seats()
        bg_pid_file = self.tmp_path / "bg_writer.pid"
        gc_pid_file = self.tmp_path / "gc_stdout.pid"

        bg_writer_code = (
            "import pathlib, subprocess, sys\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c',\n"
            "     \"import pathlib, time; time.sleep(0.25); pathlib.Path('bg_leaked.txt').write_text('leaked')\"],\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            ")\n"
            f"pathlib.Path({str(bg_pid_file)!r}).write_text(str(child.pid))\n"
        )
        grandchild_stdout_code = (
            "import pathlib, subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(gc_pid_file)!r}).write_text(str(child.pid))\n"
            "print('DIRECT_EXIT_OK', flush=True)\n"
            "sys.exit(7)\n"
        )

        entries_file = write_json(
            self.tmp_path / "process_group_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "bg-writer",
                        "type": "held",
                        "claim": "background writer spawned before exit 0 is killed on exit",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", bg_writer_code],
                            "timeout_s": 5,
                        },
                    },
                    {
                        "id": "bg-check-no-leak",
                        "type": "held",
                        "claim": "next run never observes file from killed background writer",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import time\n"
                                    "from pathlib import Path\n"
                                    "time.sleep(0.45)\n"
                                    "assert not Path('bg_leaked.txt').exists(), 'background writer leaked file'\n"
                                ),
                            ],
                            "timeout_s": 5,
                        },
                    },
                    {
                        "id": "grandchild-inherits-stdout",
                        "type": "held",
                        "claim": "direct child returns promptly with its real exit code even when grandchild holds stdout",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [sys.executable, "-c", grandchild_stdout_code],
                            "expect": {"exit": 7, "stdout_regex": "^DIRECT_EXIT_OK$"},
                            "timeout_s": 2,
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "pg_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:bg-writer"]["status"], "held")
        self.assertEqual(by_id["seat-a:bg-check-no-leak"]["status"], "held")
        self.assertEqual(by_id["seat-a:grandchild-inherits-stdout"]["status"], "held")
        gc_run = by_id["seat-a:grandchild-inherits-stdout"]["runs"]["candidate"][0]
        self.assertEqual(gc_run["exit"], 7)
        self.assertLess(gc_run["duration_s"], 1.5)

        for pid_file in (bg_pid_file, gc_pid_file):
            self.assertTrue(pid_file.is_file())
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_ignored_counter_pruned_while_setup_ignored_survives(self) -> None:
        seats_file = self._default_seats()
        entries_file = write_json(
            self.tmp_path / "ignored_counter_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "ignored-counter",
                        "type": "held",
                        "claim": "writes under gitignored .cache are pruned between runs while setup cache stays",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "import sys\n"
                                    "from pathlib import Path\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                    "extra = Path('.setup_cache/tmp_sub/stray.txt')\n"
                                    "assert not extra.exists()\n"
                                    "extra.parent.mkdir(parents=True, exist_ok=True)\n"
                                    "extra.write_text('stray')\n"
                                    "counter = Path('.cache/sub/n')\n"
                                    "n = int(counter.read_text()) if counter.exists() else 0\n"
                                    "counter.parent.mkdir(parents=True, exist_ok=True)\n"
                                    "counter.write_text(str(n + 1))\n"
                                    "sys.exit(0 if n == 0 else 1)\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "verify-empty-ignored-dirs-pruned",
                        "type": "held",
                        "claim": "empty ignored directories left after pruning are removed",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path\n"
                                    "assert not Path('.cache').exists()\n"
                                    "assert not Path('.setup_cache/tmp_sub').exists()\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                ),
                            ],
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "ignored_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "3",
                "--setup",
                "mkdir -p .setup_cache && echo ok > .setup_cache/ready.txt",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:ignored-counter"]["status"], "held")
        self.assertEqual(by_id["seat-a:verify-empty-ignored-dirs-pruned"]["status"], "held")

    def test_citation_path_containment_and_external_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = create_fixture_repo(tmp_path)
            trees_dir = tmp_path / "trees"

            # Place outside.md directly beside candidate/ so '../outside.md' resolves to it.
            outside_doc = tmp_path / "outside_target.md"
            outside_doc.write_text("Outside secret quote.\n", encoding="utf-8")
            fake_home = tmp_path / "fake_home"
            fake_home.mkdir()
            (fake_home / "rules.md").write_text("Home rule quote.\n", encoding="utf-8")

            # Add tracked symlinks: one escaping candidate tree, one staying inside.
            os.symlink(str(outside_doc), repo / "escape_link.md")
            os.symlink("spec.md", repo / "inside_link.md")
            run_cmd(["git", "-C", str(repo), "add", "escape_link.md", "inside_link.md"])
            run_cmd(["git", "-C", str(repo), "commit", "-m", "add citation symlinks"])

            mat_proc = run_witness_cli(
                ["materialize", "--repo", str(repo), "--base", "base-tag", "--out", str(trees_dir)]
            )
            self.assertEqual(mat_proc.returncode, 0, mat_proc.stderr)
            try:
                (trees_dir / "outside_sibling.md").write_text(
                    "Sibling outside quote.\n", encoding="utf-8"
                )
                seats_file = write_json(
                    tmp_path / "seats.json",
                    {
                        "seats": [
                            {
                                "name": "seat-a",
                                "required": True,
                                "status": "completed",
                                "provider": "prov-a",
                                "model": "model-a",
                            }
                        ]
                    },
                )
                entries_file = write_json(
                    tmp_path / "cit_entries.json",
                    {
                        "seat": "seat-a",
                        "in_scope": True,
                        "entries": [
                            {
                                "id": "rel-traversal",
                                "type": "held",
                                "claim": "relative traversal citation must be broken witness",
                                "intent": {
                                    "file": "../outside_sibling.md",
                                    "quote": "Sibling outside quote.",
                                },
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                            {
                                "id": "symlink-escape",
                                "type": "held",
                                "claim": "tracked symlink escaping candidate tree must be broken witness",
                                "intent": {
                                    "file": "escape_link.md",
                                    "quote": "Outside secret quote.",
                                },
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                            {
                                "id": "dot-git-citation",
                                "type": "held",
                                "claim": "citation into .git must be broken witness",
                                "intent": {"file": ".git", "quote": "gitdir:"},
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                            {
                                "id": "abs-path-ok",
                                "type": "held",
                                "claim": "absolute citation path outside tree keeps working",
                                "intent": {
                                    "file": str(outside_doc),
                                    "quote": "Outside secret quote.",
                                },
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                            {
                                "id": "tilde-path-ok",
                                "type": "held",
                                "claim": "tilde citation path keeps working",
                                "intent": {
                                    "file": "~/rules.md",
                                    "quote": "Home rule quote.",
                                },
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                            {
                                "id": "in-tree-symlink-ok",
                                "type": "held",
                                "claim": "symlink staying inside candidate tree keeps working",
                                "intent": {
                                    "file": "inside_link.md",
                                    "quote": "The add function must return the exact sum of a and b.",
                                },
                                "witness": {"run": [sys.executable, "-c", "pass"]},
                            },
                        ],
                    },
                )
                report_json = tmp_path / "cit_report.json"
                proc = run_witness_cli(
                    [
                        "run",
                        "--trees",
                        str(trees_dir),
                        "--seats",
                        str(seats_file),
                        "--entries",
                        str(entries_file),
                        "--runs",
                        "1",
                        "--report-json",
                        str(report_json),
                    ],
                    env={**os.environ, "HOME": str(fake_home)},
                )
                self.assertEqual(proc.returncode, 2, proc.stderr)
                report = json.loads(report_json.read_text(encoding="utf-8"))
                by_id = {e["id"]: e for e in report["entries"]}
                for broken_id in (
                    "seat-a:rel-traversal",
                    "seat-a:symlink-escape",
                    "seat-a:dot-git-citation",
                ):
                    self.assertEqual(by_id[broken_id]["status"], "broken witness", broken_id)
                    self.assertTrue(by_id[broken_id]["reason"], broken_id)
                for ok_id in (
                    "seat-a:abs-path-ok",
                    "seat-a:tilde-path-ok",
                    "seat-a:in-tree-symlink-ok",
                ):
                    self.assertEqual(by_id[ok_id]["status"], "held", ok_id)
            finally:
                run_witness_cli(["cleanup", "--out", str(trees_dir)])

    def test_witness_files_oserror_and_malformed_patch_escape_record_broken_witness(self) -> None:
        seats_file = self._default_seats()
        entries_file = write_json(
            self.tmp_path / "broken_io_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "file-parent-is-file",
                        "type": "finding",
                        "claim": "writing under an existing regular file must record broken witness",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "files": {"calc.py/nested.py": "x = 1\n"},
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                    {
                        "id": "malformed-patch-escape",
                        "type": "finding",
                        "claim": "malformed C escape in quoted diff path must record broken witness",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "patch": (
                                'diff --git "a/bad\\x.py" "b/bad\\x.py"\n'
                                '--- "a/bad\\x.py"\n'
                                '+++ "b/bad\\x.py"\n'
                                "@@ -1 +1 @@\n"
                                "-a\n"
                                "+b\n"
                            ),
                            "run": [sys.executable, "-c", "pass"],
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "broken_io_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertTrue(report_json.is_file())
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        self.assertEqual(by_id["seat-a:file-parent-is-file"]["status"], "broken witness")
        self.assertIn("calc.py/nested.py", by_id["seat-a:file-parent-is-file"]["reason"])
        self.assertEqual(by_id["seat-a:malformed-patch-escape"]["status"], "broken witness")
        self.assertTrue(by_id["seat-a:malformed-patch-escape"]["reason"])

    def test_unexpected_internal_error_exits_3(self) -> None:
        # Real CLI path: writing report JSON to an existing directory triggers an unexpected OSError.
        seats_file = self._default_seats()
        entries_file = write_json(
            self.tmp_path / "simple_entries.json",
            {"seat": "seat-a", "in_scope": True, "entries": []},
        )
        dir_as_report = self.tmp_path / "report_dir_conflict"
        dir_as_report.mkdir()
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--report-json",
                str(dir_as_report),
            ]
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn("Traceback", proc.stderr)

        # Direct main() path: synthetic unexpected RuntimeError prints traceback and returns 3.
        stderr_buf = io.StringIO()
        with (
            mock.patch.object(witness, "cleanup", side_effect=RuntimeError("forced internal boom")),
            contextlib.redirect_stderr(stderr_buf),
        ):
            rc = witness.main(["cleanup", "--out", str(self.tmp_path)])
        self.assertEqual(rc, 3)
        self.assertIn("Traceback", stderr_buf.getvalue())
        self.assertIn("RuntimeError: forced internal boom", stderr_buf.getvalue())

    def test_unbounded_output_capped_and_marked_broken(self) -> None:
        seats_file = self._default_seats()
        setup_limit_file = self.tmp_path / "setup_fsize_limit.txt"
        entries_file = write_json(
            self.tmp_path / "yes_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "infinite-stdout",
                        "type": "finding",
                        "claim": "witness printing forever hits RLIMIT_FSIZE and is broken",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": ["yes"],
                            "expect": {"exit": "nonzero"},
                            "timeout_s": 30,
                        },
                    },
                    {
                        "id": "infinite-build",
                        "type": "finding",
                        "claim": "build printing forever hits RLIMIT_FSIZE and is broken",
                        "severity": "blocker",
                        "intent": self._valid_intent(),
                        "witness": {
                            "build": ["yes"],
                            "run": [sys.executable, "-c", "pass"],
                            "timeout_s": 30,
                        },
                    },
                ],
            },
        )
        setup_cmd = (
            f"{sys.executable} -c "
            f"\"import pathlib, resource; s, h = resource.getrlimit(resource.RLIMIT_FSIZE); "
            f"pathlib.Path({str(setup_limit_file)!r}).write_text(f'{{s}},{{h}}')\""
        )
        report_json = self.tmp_path / "yes_report.json"
        parent_soft, parent_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        start = time.monotonic()
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "1",
                "--setup",
                setup_cmd,
                "--report-json",
                str(report_json),
            ]
        )
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertLess(elapsed, 20.0, "infinite output witness took too long to terminate")

        # --setup commands must keep the parent process's RLIMIT_FSIZE (not capped).
        self.assertEqual(
            setup_limit_file.read_text(encoding="utf-8").strip(),
            f"{parent_soft},{parent_hard}",
        )

        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        for eid in ("seat-a:infinite-stdout", "seat-a:infinite-build"):
            entry = by_id[eid]
            self.assertEqual(entry["status"], "broken witness", eid)
            self.assertEqual(entry["reason"], "output or file size limit exceeded", eid)
            run_rec = entry["runs"]["candidate"][0]
            self.assertEqual(run_rec["outcome"], "broken", eid)
            self.assertEqual(run_rec["exit"], -signal.SIGXFSZ, eid)
            self.assertLessEqual(len(run_rec["stdout_tail"]), witness.TAIL_MAX_CHARS)

    def test_signal_termination_cleans_process_group_tree_and_temp_dirs(self) -> None:
        seats_file = self._default_seats()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                sub_tmp = self.tmp_path / f"sub_tmp_{sig.name}"
                sub_tmp.mkdir()
                ready_file = self.tmp_path / f"ready_{sig.name}.txt"
                ready_tmp = self.tmp_path / f"ready_{sig.name}.tmp"
                sleeper_code = (
                    "import os, pathlib, subprocess, sys, time\n"
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    "pathlib.Path('dirty_during_sleep.txt').write_text('dirty', encoding='utf-8')\n"
                    f"tmp_p = pathlib.Path({str(ready_tmp)!r})\n"
                    f"ready_p = pathlib.Path({str(ready_file)!r})\n"
                    "tmp_p.write_text(f'{os.getpid()} {child.pid}', encoding='utf-8')\n"
                    "os.replace(tmp_p, ready_p)\n"
                    "time.sleep(60)\n"
                )
                entries_file = write_json(
                    self.tmp_path / f"sig_entries_{sig.name}.json",
                    {
                        "seat": "seat-a",
                        "in_scope": True,
                        "entries": [
                            {
                                "id": "sleeper",
                                "type": "held",
                                "claim": "long sleeping witness",
                                "intent": self._valid_intent(),
                                "witness": {
                                    "run": [sys.executable, "-c", sleeper_code],
                                    "timeout_s": 60,
                                },
                            }
                        ],
                    },
                )
                env = {**os.environ, "TMPDIR": str(sub_tmp)}
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(WITNESS_SCRIPT),
                        "run",
                        "--trees",
                        str(self.trees_dir),
                        "--seats",
                        str(seats_file),
                        "--entries",
                        str(entries_file),
                        "--runs",
                        "1",
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                witness_pid: int | None = None
                grandchild_pid: int | None = None
                try:
                    deadline = time.monotonic() + 10.0
                    while not ready_file.is_file() and time.monotonic() < deadline:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertTrue(
                        ready_file.is_file(),
                        f"witness did not start sleeping (rc={proc.poll()})",
                    )
                    parts = ready_file.read_text(encoding="utf-8").split()
                    witness_pid, grandchild_pid = int(parts[0]), int(parts[1])

                    proc.send_signal(sig)
                    _, stderr_out = proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, 3, stderr_out)

                    for pid in (witness_pid, grandchild_pid):
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(witness_pid, 0)

                    self.assertFalse(
                        (self.trees_dir / "candidate" / "dirty_during_sleep.txt").exists(),
                        "candidate tree was left dirty after signal termination",
                    )
                    leaked_dirs = [
                        p.name
                        for p in sub_tmp.iterdir()
                        if p.name.startswith(("council-runtime-", "council-empty-hooks-"))
                    ]
                    self.assertEqual(leaked_dirs, [], f"leaked temp dirs: {leaked_dirs}")
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
                    for pid in (witness_pid, grandchild_pid):
                        if pid is not None:
                            with contextlib.suppress(ProcessLookupError):
                                os.kill(pid, signal.SIGKILL)
                    if witness_pid is not None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(witness_pid, signal.SIGKILL)

        # Normal exit must also leave no council-empty-hooks-* or council-runtime-* dirs behind.
        normal_tmp = self.tmp_path / "sub_tmp_normal"
        normal_tmp.mkdir()
        quick_entries = write_json(
            self.tmp_path / "quick_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "quick",
                        "type": "held",
                        "claim": "quick pass cleans up temp dirs",
                        "intent": self._valid_intent(),
                        "witness": {"run": [sys.executable, "-c", "pass"]},
                    }
                ],
            },
        )
        normal_proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(quick_entries),
                "--runs",
                "1",
            ],
            env={**os.environ, "TMPDIR": str(normal_tmp)},
        )
        self.assertEqual(normal_proc.returncode, 0, normal_proc.stderr)
        leaked_normal = [
            p.name
            for p in normal_tmp.iterdir()
            if p.name.startswith(("council-runtime-", "council-empty-hooks-"))
        ]
        self.assertEqual(leaked_normal, [], f"leaked temp dirs on normal exit: {leaked_normal}")

    def test_readonly_untracked_and_ignored_dirs_cleaned_between_runs(self) -> None:
        seats_file = self._default_seats()
        entries_file = write_json(
            self.tmp_path / "readonly_entries.json",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "ro-untracked",
                        "type": "held",
                        "claim": "read-only untracked tree is cleaned between runs",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                "sh",
                                "-c",
                                (
                                    "test ! -e ro && "
                                    "mkdir -p ro/sub && echo x > ro/sub/f && chmod -R a-w ro"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "verify-after-ro-untracked",
                        "type": "held",
                        "claim": "next entry starts clean after read-only untracked tree",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path\n"
                                    "assert not Path('ro').exists()\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "ro-ignored",
                        "type": "held",
                        "claim": "read-only ignored tree under .cache is cleaned between runs",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                "sh",
                                "-c",
                                (
                                    "test ! -e .cache/ro && "
                                    "mkdir -p .cache/ro/sub && echo x > .cache/ro/sub/f && "
                                    "chmod -R a-w .cache/ro"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "ro-inside-setup-cache",
                        "type": "held",
                        "claim": "read-only ignored subdir inside baseline .setup_cache is cleaned",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                "sh",
                                "-c",
                                (
                                    "test ! -e .setup_cache/ro && "
                                    "mkdir -p .setup_cache/ro/sub && "
                                    "echo x > .setup_cache/ro/sub/f && "
                                    "chmod -R a-w .setup_cache/ro"
                                ),
                            ],
                        },
                    },
                    {
                        "id": "verify-after-ro-ignored",
                        "type": "held",
                        "claim": "next entry sees clean ignored dirs and surviving setup cache",
                        "intent": self._valid_intent(),
                        "witness": {
                            "run": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path\n"
                                    "assert not Path('.cache').exists()\n"
                                    "assert not Path('.setup_cache/ro').exists()\n"
                                    "assert Path('.setup_cache/ready.txt').read_text().strip() == 'ok'\n"
                                ),
                            ],
                        },
                    },
                ],
            },
        )
        report_json = self.tmp_path / "readonly_report.json"
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats_file),
                "--entries",
                str(entries_file),
                "--runs",
                "2",
                "--setup",
                "mkdir -p .setup_cache && echo ok > .setup_cache/ready.txt",
                "--report-json",
                str(report_json),
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(report_json.read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in report["entries"]}
        for eid in (
            "seat-a:ro-untracked",
            "seat-a:verify-after-ro-untracked",
            "seat-a:ro-ignored",
            "seat-a:ro-inside-setup-cache",
            "seat-a:verify-after-ro-ignored",
        ):
            self.assertEqual(by_id[eid]["status"], "held", eid)
        cand = self.trees_dir / "candidate"
        self.assertFalse((cand / "ro").exists())
        self.assertFalse((cand / ".cache").exists())
        self.assertFalse((cand / ".setup_cache" / "ro").exists())
        self.assertEqual((cand / ".setup_cache" / "ready.txt").read_text(encoding="utf-8").strip(), "ok")


class UnitHelperTests(unittest.TestCase):
    """Focused unit tests for pure helpers (tails, patch parser, cause normalization)."""

    def test_truncate_tail_respects_lines_and_chars(self) -> None:
        many_lines = "".join(f"line-{i}\n" for i in range(100))
        tailed = witness.truncate_tail(many_lines)
        self.assertEqual(len(tailed.splitlines()), 40)
        self.assertTrue(tailed.startswith("line-60\n"))

        huge_line = "x" * 10000
        self.assertEqual(len(witness.truncate_tail(huge_line)), 4000)

    def test_validate_patch_targets_ignores_hunk_body_dashes(self) -> None:
        # Removing a Markdown '--- ' line inside a hunk must not be mistaken for a diff header.
        patch = (
            "diff --git a/doc.md b/doc.md\n"
            "--- a/doc.md\n"
            "+++ b/doc.md\n"
            "@@ -1,3 +1,2 @@\n"
            " title\n"
            "--- /etc/passwd\n"
            " end\n"
        )
        self.assertIsNone(witness.validate_patch_targets(patch))

        bad_abs = "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertIsNotNone(witness.validate_patch_targets(bad_abs))

        bad_git = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n"
        self.assertIsNotNone(witness.validate_patch_targets(bad_git))

    def test_unquote_git_path_and_quoted_patch_targets(self) -> None:
        # Multi-byte UTF-8 octal escapes decode as UTF-8 bytes, not Latin-1 code points.
        self.assertEqual(witness._unquote_git_path('"caf\\303\\251.py"'), "café.py")
        self.assertEqual(witness._unquote_git_path('"a\\tb\\n\\"\\\\.py"'), 'a\tb\n"\\.py')
        self.assertEqual(witness._unquote_git_path("plain.py"), "plain.py")

        utf8_patch = (
            'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
            '--- "a/caf\\303\\251.py"\n'
            '+++ "b/caf\\303\\251.py"\n'
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        self.assertIsNone(witness.validate_patch_targets(utf8_patch))

        # Octal-encoded path traversal ('../escape.py') must still be rejected.
        octal_traversal = (
            '--- "a/\\056\\056\\057escape.py"\n'
            '+++ "b/\\056\\056\\057escape.py"\n'
        )
        self.assertIsNotNone(witness.validate_patch_targets(octal_traversal))

        # Malformed escapes, invalid octal, unterminated quotes, and bad UTF-8 raise ValueError
        # and surface as a broken-witness error string from validate_patch_targets.
        bad_tokens = (
            '"a/bad\\x.py"',
            '"a/bad\\u12.py"',
            '"a/bad\\400.py"',
            '"a/bad\\12.py"',
            '"a/unterminated',
            '"a/dangling\\"',
            '"a/bad_utf8_\\377\\377.py"',
        )
        for token in bad_tokens:
            with self.assertRaises(ValueError, msg=token):
                witness._unquote_git_path(token)
            err = witness.validate_patch_targets(f"--- {token}\n+++ {token}\n")
            self.assertIsNotNone(err, token)

    def test_exec_process_reads_only_tail_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Emit well over CAPTURE_TAIL_BYTES so exec_process must seek instead of reading all.
            emit_code = "import sys; sys.stdout.write('A' * 200000 + 'TAIL_MARKER\\n')"
            res = witness.exec_process(
                [sys.executable, "-c", emit_code],
                tmp_path,
                os.environ,
                10,
            )
            self.assertEqual(res.exit_code, 0)
            self.assertFalse(res.fsize_exceeded)
            self.assertLessEqual(len(res.stdout), witness.CAPTURE_TAIL_BYTES)
            self.assertTrue(res.stdout.endswith("TAIL_MARKER\n"))

            # Capping with a small RLIMIT_FSIZE stops `yes` immediately with SIGXFSZ.
            capped = witness.exec_process(
                ["yes"],
                tmp_path,
                os.environ,
                10,
                max_file_bytes=512 * 1024,
            )
            self.assertTrue(capped.fsize_exceeded)
            self.assertEqual(capped.exit_code, -signal.SIGXFSZ)
            self.assertLessEqual(len(capped.stdout), witness.CAPTURE_TAIL_BYTES)

    def test_prune_extra_ignored_raises_input_error_when_retry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = create_fixture_repo(tmp_path)
            ignored_file = repo / ".cache" / "stuck.txt"
            ignored_file.parent.mkdir(parents=True)
            ignored_file.write_text("x\n", encoding="utf-8")
            with (
                mock.patch.object(os, "unlink", side_effect=PermissionError("still denied")),
                self.assertRaises(witness.InputError),
            ):
                witness._prune_extra_ignored(repo, frozenset())


class EnvironmentAndValidateTests(unittest.TestCase):
    """Witness environment isolation and the validate subcommand."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.repo = create_fixture_repo(self.tmp_path)
        self.trees_dir = self.tmp_path / "trees"
        proc = run_witness_cli(
            ["materialize", "--repo", str(self.repo), "--base", "base-tag", "--out", str(self.trees_dir)]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self) -> None:
        run_witness_cli(["cleanup", "--out", str(self.trees_dir)])
        self._tmp.cleanup()

    def test_witness_env_drops_session_and_secret_variables(self) -> None:
        user_runtime = self.tmp_path / "user-runtime"
        user_runtime.mkdir()
        uv_cache = str(self.tmp_path / "uv-cache")
        setup_marker = self.tmp_path / "setup_ambient.txt"
        env = {
            **os.environ,
            "DISPLAY": ":99",
            "WAYLAND_DISPLAY": "wayland-9",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",
            "PULSE_RUNTIME_PATH": str(user_runtime / "pulse"),
            "PIPEWIRE_RUNTIME_DIR": str(user_runtime / "pipewire"),
            "VSCODE_IPC_HOOK_CLI": "/tmp/vscode.sock",
            "KITTY_LISTEN_ON": "unix:/tmp/kitty.sock",
            "GITHUB_TOKEN": "zqx",
            "MY_API_KEY": "zqx",
            "CONVEX_DEPLOY_KEY": "zqx",
            "STRIPE_KEY": "zqx",
            "DATABASE_URL": "postgres://u:p@host/db",
            "SENTRY_DSN": "https://abc@sentry.invalid/1",
            "MYSQL_PWD": "zqx",
            "DB_PASS": "zqx",
            "NETLIFY_AUTH": "zqx",
            "NPM_CONFIG__AUTH": "zqx",
            "PGPASSFILE": "/tmp/pgpass",
            "UV_INDEX_PRIVATE_PASSWORD": "zqx",
            "CARGO_REGISTRIES_PRIVATE_TOKEN": "zqx",
            "AMBIENT_UNLISTED": "1",
            "UV_CACHE_DIR": uv_cache,
            "XDG_RUNTIME_DIR": str(user_runtime),
        }
        dropped = (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "PULSE_RUNTIME_PATH",
            "PIPEWIRE_RUNTIME_DIR",
            "VSCODE_IPC_HOOK_CLI",
            "KITTY_LISTEN_ON",
            "GITHUB_TOKEN",
            "MY_API_KEY",
            "CONVEX_DEPLOY_KEY",
            "STRIPE_KEY",
            "DATABASE_URL",
            "SENTRY_DSN",
            "MYSQL_PWD",
            "DB_PASS",
            "NETLIFY_AUTH",
            "NPM_CONFIG__AUTH",
            "PGPASSFILE",
            "UV_INDEX_PRIVATE_PASSWORD",
            "CARGO_REGISTRIES_PRIVATE_TOKEN",
            "AMBIENT_UNLISTED",
        )
        probe = (
            "import os, stat, sys\n"
            "e = os.environ\n"
            f"leaked = [k for k in {dropped!r} if k in e]\n"
            "rd = e['XDG_RUNTIME_DIR']\n"
            "print('RUNTIME=' + rd)\n"
            f"kept_ok = bool(e.get('PATH')) and bool(e.get('HOME')) and e.get('UV_CACHE_DIR') == {uv_cache!r}\n"
            "kept_ok = kept_ok and e.get('COUNCIL_ROLE') == 'candidate'\n"
            f"ok = not leaked and kept_ok and rd != {str(user_runtime)!r}\n"
            "ok = ok and stat.S_IMODE(os.stat(rd).st_mode) == 0o700\n"
            "if not ok:\n"
            "    print('LEAKED=' + repr(leaked), file=sys.stderr)\n"
            "sys.exit(0 if ok else 1)\n"
        )
        entries = write_json(self.tmp_path / "entries" / "seat-a.json", {
            "seat": "seat-a",
            "in_scope": True,
            "entries": [{
                "id": "E1",
                "type": "held",
                "claim": "witness environment uses allowlist and drops secrets",
                "intent": {"file": "spec.md", "quote": "The add function must return the exact sum of a and b."},
                "witness": {"files": {"probe.py": probe}, "run": [sys.executable, "probe.py"]},
            }],
        })
        seats = write_json(self.tmp_path / "seats.json", {"seats": [
            {"name": "seat-a", "required": True, "status": "completed", "provider": "p", "model": "m"},
        ]})
        report = self.tmp_path / "report.json"
        setup_cmd = (
            f"{sys.executable} -c \"import os, pathlib; "
            f"pathlib.Path({str(setup_marker)!r}).write_text(os.environ.get('AMBIENT_UNLISTED', ''))\""
        )
        proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats),
                "--entries",
                str(entries),
                "--runs",
                "1",
                "--setup",
                setup_cmd,
                "--report-json",
                str(report),
                "--report-md",
                str(self.tmp_path / "r.md"),
            ],
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # --setup still receives the full environment.
        self.assertEqual(setup_marker.read_text(encoding="utf-8"), "1")
        entry = json.loads(report.read_text(encoding="utf-8"))["entries"][0]
        self.assertEqual(entry["status"], "held")
        runtime = entry["runs"]["candidate"][0]["stdout_tail"].strip().removeprefix("RUNTIME=")
        self.assertFalse(Path(runtime).exists(), "per-run runtime dir must be removed")

    def _entries_file(self, name: str, data: Any) -> Path:
        return write_json(self.tmp_path / "entries" / f"{name}.json", data)

    def _validate(self, path: Path, *, trees: bool = False) -> subprocess.CompletedProcess[str]:
        extra = ["--trees", str(self.trees_dir)] if trees else []
        return run_witness_cli(["validate", *extra, "--entries", str(path)])

    def test_validate_accepts_a_well_formed_file(self) -> None:
        path = self._entries_file("seat-a", {"seat": "seat-a", "in_scope": True, "entries": [
            {"id": "A1", "type": "advisory", "claim": "consider a helper"},
        ]})
        proc = self._validate(path, trees=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_validate_reports_each_problem_and_exits_3(self) -> None:
        cases = {
            "seat name differs from file name": (
                {"seat": "seat-b", "in_scope": True, "entries": []}, "does not match"),
            "non-boolean in_scope": (
                {"seat": "seat-a", "in_scope": "yes", "entries": []}, "in_scope"),
            "unknown entry key": (
                {"seat": "seat-a", "in_scope": True, "entries": [
                    {"id": "A1", "type": "advisory", "claim": "c", "severty": "nit"}]}, "seat-a:A1"),
            "missing citation": (
                {"seat": "seat-a", "in_scope": True, "entries": [
                    {"id": "A2", "type": "advisory", "claim": "c",
                     "intent": {"file": "spec.md", "quote": "no such sentence zqx"}}]}, "citation not found"),
        }
        for label, (data, needle) in cases.items():
            with self.subTest(label):
                path = self._entries_file("seat-a", data)
                proc = self._validate(path, trees=True)
                self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                self.assertIn(needle, proc.stdout + proc.stderr)

    def test_unknown_tilde_user_citation_reported_in_run_and_validate(self) -> None:
        bad_tilde_path = "~no_such_user_council_zqx/x"
        entries_path = self._entries_file(
            "seat-a",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "T1",
                        "type": "held",
                        "claim": "unknown tilde user must not crash runner or validate",
                        "intent": {"file": bad_tilde_path, "quote": "anything"},
                        "witness": {"run": [sys.executable, "-c", "pass"]},
                    }
                ],
            },
        )
        val_proc = self._validate(entries_path, trees=True)
        self.assertEqual(val_proc.returncode, 3, val_proc.stdout + val_proc.stderr)
        self.assertIn("invalid citation path:", val_proc.stdout + val_proc.stderr)
        self.assertNotIn("Traceback", val_proc.stdout + val_proc.stderr)

        seats = write_json(
            self.tmp_path / "tilde_seats.json",
            {
                "seats": [
                    {
                        "name": "seat-a",
                        "required": True,
                        "status": "completed",
                        "provider": "p",
                        "model": "m",
                    }
                ]
            },
        )
        report = self.tmp_path / "tilde_report.json"
        run_proc = run_witness_cli(
            [
                "run",
                "--trees",
                str(self.trees_dir),
                "--seats",
                str(seats),
                "--entries",
                str(entries_path),
                "--runs",
                "1",
                "--report-json",
                str(report),
            ]
        )
        self.assertEqual(run_proc.returncode, 2, run_proc.stdout + run_proc.stderr)
        self.assertNotIn("Traceback", run_proc.stderr)
        entry = json.loads(report.read_text(encoding="utf-8"))["entries"][0]
        self.assertEqual(entry["status"], "broken witness")
        self.assertTrue(entry["reason"].startswith("invalid citation path:"), entry["reason"])

    def test_validate_resets_dirty_candidate_tree_before_checking_citations(self) -> None:
        cand = self.trees_dir / "candidate"
        # Simulate leftovers from a previous run: clobbered tracked file, untracked file, and setup cache.
        (cand / "spec.md").write_text("clobbered by previous witness\n", encoding="utf-8")
        (cand / "leftover.txt").write_text("Leftover untracked quote.\n", encoding="utf-8")
        setup_file = cand / ".setup_cache" / "ready.txt"
        setup_file.parent.mkdir(parents=True, exist_ok=True)
        setup_file.write_text("ok\n", encoding="utf-8")

        real_entries = self._entries_file(
            "seat-a",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "V1",
                        "type": "advisory",
                        "claim": "cites snapshot spec.md after tree reset",
                        "intent": {
                            "file": "spec.md",
                            "quote": "The add function must return the exact sum of a and b.",
                        },
                    }
                ],
            },
        )
        ok_proc = self._validate(real_entries, trees=True)
        self.assertEqual(ok_proc.returncode, 0, ok_proc.stdout + ok_proc.stderr)
        self.assertFalse((cand / "leftover.txt").exists())
        self.assertTrue(setup_file.is_file())

        # Re-create an untracked file and verify validate cleans it so a citation to it fails.
        (cand / "leftover.txt").write_text("Leftover untracked quote.\n", encoding="utf-8")
        stale_entries = self._entries_file(
            "seat-a",
            {
                "seat": "seat-a",
                "in_scope": True,
                "entries": [
                    {
                        "id": "V2",
                        "type": "advisory",
                        "claim": "cites untracked leftover that must be cleaned",
                        "intent": {
                            "file": "leftover.txt",
                            "quote": "Leftover untracked quote.",
                        },
                    }
                ],
            },
        )
        stale_proc = self._validate(stale_entries, trees=True)
        self.assertEqual(stale_proc.returncode, 3, stale_proc.stdout + stale_proc.stderr)
        self.assertIn("citation not found: leftover.txt", stale_proc.stdout + stale_proc.stderr)
        self.assertFalse((cand / "leftover.txt").exists())



class RoundThreeTests(unittest.TestCase):
    """Round-3 review findings: R16 (parallel validate), R17 (nohup), R18 (late signal), R20."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.repo = create_fixture_repo(self.tmp_path)
        self.trees_dir = self.tmp_path / "trees"
        proc = run_witness_cli(
            ["materialize", "--repo", str(self.repo), "--base", "base-tag", "--out", str(self.trees_dir)]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.seats = write_json(self.tmp_path / "seats.json", {"seats": [
            {"name": "seat-a", "required": True, "status": "completed", "provider": "p", "model": "m"},
        ]})

    def tearDown(self) -> None:
        run_witness_cli(["cleanup", "--out", str(self.trees_dir)])
        self._tmp.cleanup()

    def _held(self, run_argv: list[str]) -> Path:
        return write_json(self.tmp_path / "entries" / "seat-a.json", {
            "seat": "seat-a",
            "in_scope": True,
            "entries": [{
                "id": "H1",
                "type": "held",
                "claim": "holds",
                "intent": {"file": "spec.md", "quote": "The add function must return the exact sum of a and b."},
                "witness": {"run": run_argv},
            }],
        })

    def test_parallel_validates_do_not_collide(self) -> None:
        paths = []
        for name in "abcdef":
            paths.append(write_json(self.tmp_path / "entries" / f"seat-{name}.json", {
                "seat": f"seat-{name}", "in_scope": True, "entries": [{
                    "id": "A1", "type": "advisory", "claim": "c",
                    "intent": {"file": "spec.md", "quote": "The add function must return the exact sum of a and b."},
                }],
            }))
        for _ in range(3):
            procs = [
                subprocess.Popen(
                    [sys.executable, "-I", "-B", str(WITNESS_SCRIPT), "validate",
                     "--trees", str(self.trees_dir), "--entries", str(path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                for path in paths
            ]
            results = [(proc.wait(timeout=60), proc.stderr.read()) for proc in procs]
            for proc in procs:
                proc.stdout.close()
                proc.stderr.close()
            self.assertEqual([code for code, _ in results], [0] * len(paths), results)

    def test_read_only_trees_dir_still_validates(self) -> None:
        path = write_json(self.tmp_path / "entries" / "seat-a.json", {
            "seat": "seat-a", "in_scope": True, "entries": [{"id": "A1", "type": "advisory", "claim": "c"}],
        })
        mode = stat.S_IMODE(self.trees_dir.stat().st_mode)
        self.trees_dir.chmod(0o555)
        try:
            proc = run_witness_cli(["validate", "--trees", str(self.trees_dir), "--entries", str(path)])
        finally:
            self.trees_dir.chmod(mode)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in self.trees_dir.iterdir()), ["base", "candidate", "trees.json"])

    def test_inherited_sighup_ignore_survives(self) -> None:
        entries = self._held(["python3", "-c", "import time; time.sleep(2)"])
        runner = [sys.executable, "-I", "-B", str(WITNESS_SCRIPT), "run", "--trees", str(self.trees_dir),
                  "--seats", str(self.seats), "--entries", str(entries), "--runs", "1",
                  "--report-md", str(self.tmp_path / "r.md")]
        # `trap '' HUP` is inherited across exec as SIG_IGN, as with nohup.
        proc = subprocess.Popen(
            ["sh", "-c", 'trap "" HUP; exec "$@"', "sh", *runner],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.8)
        proc.send_signal(signal.SIGHUP)
        _, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)

    def test_signal_during_error_reporting_exits_3(self) -> None:
        class SignallingStderr(io.StringIO):
            fired = False

            def write(self, text: str) -> int:
                if not SignallingStderr.fired:
                    SignallingStderr.fired = True
                    os.kill(os.getpid(), signal.SIGTERM)
                return super().write(text)

        with mock.patch.object(sys, "stderr", SignallingStderr()):
            code = witness.main(["cleanup", "--out", str(self.tmp_path / "no-such-trees")])
        self.assertTrue(SignallingStderr.fired)
        self.assertEqual(code, witness.EXIT_ERROR)

    def test_unreadable_dir_in_runtime_dir_is_removed(self) -> None:
        private_tmp = self.tmp_path / "private-tmp"
        private_tmp.mkdir()
        entries = self._held(["sh", "-c", 'mkdir -p "$XDG_RUNTIME_DIR/d/e" && chmod 000 "$XDG_RUNTIME_DIR/d"'])
        proc = run_witness_cli(
            ["run", "--trees", str(self.trees_dir), "--seats", str(self.seats), "--entries", str(entries),
             "--runs", "1", "--report-md", str(self.tmp_path / "r.md")],
            env={**os.environ, "TMPDIR": str(private_tmp)},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in private_tmp.glob("council-runtime-*")), [])


if __name__ == "__main__":
    unittest.main()
