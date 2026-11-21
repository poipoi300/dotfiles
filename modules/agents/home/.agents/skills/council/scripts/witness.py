#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Deterministic witness runner for the adversarial council skill.

Materializes isolated base and candidate git worktrees without touching the
source repository, runs each seat's witness commands against both trees, and
computes per-claim statuses, waivers, and an overall merge gate.
"""

from __future__ import annotations

import argparse
import atexit
import errno
import fcntl
import hashlib
import json
import os
import posixpath
import re
import resource
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNVERIFIED = 2
EXIT_ERROR = 3

SCHEMA_VERSION = 1
DEFAULT_RUNS = 3
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 900
DEFAULT_BROKEN_EXIT: tuple[int, ...] = (126, 127)
TAIL_MAX_LINES = 40
TAIL_MAX_CHARS = 4000
CAPTURE_TAIL_BYTES = 64 * 1024
WITNESS_MAX_FILE_BYTES = 256 * 1024 * 1024
FSIZE_EXCEEDED_REASON = "output or file size limit exceeded"

VALID_SEAT_STATUSES = frozenset({"completed", "failed", "missing"})
VALID_ENTRY_TYPES = frozenset({"finding", "held", "advisory"})
VALID_SEVERITIES = frozenset({"blocker", "should", "nit"})
BLOCKING_SEVERITIES = frozenset({"blocker", "should"})
VALID_WAIVER_ACTIONS = frozenset({"waive", "severity"})

ENTRY_KEYS = frozenset(
    {"id", "type", "claim", "severity", "cause", "intent", "citations", "witness"}
)
CITATION_KEYS = frozenset({"file", "quote"})
WITNESS_KEYS = frozenset(
    {
        "files",
        "patch",
        "build",
        "run",
        "cwd",
        "expect",
        "timeout_s",
        "timeout_is_failure",
        "broken_exit",
    }
)
EXPECT_KEYS = frozenset({"exit", "stdout_regex", "stderr_regex"})
WAIVER_KEYS = frozenset({"action", "severity", "reason"})

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class InputError(Exception):
    """Raised on CLI, input schema, or environment errors (exit code 3)."""


class TerminatedBySignal(BaseException):
    """Raised by termination signal handlers so cleanup finally blocks execute."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"terminated by signal {signum}")
        self.signum = signum


class CouncilArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that exits with EXIT_ERROR (3) instead of 2."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


@dataclass(frozen=True)
class ProcessResult:
    """Result of executing a single subprocess with process-group timeout."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    spawn_error: str | None = None
    fsize_exceeded: bool = False


@dataclass(frozen=True)
class RunDetail:
    """Outcome of a single witness execution inside one tree."""

    record: dict[str, Any]
    broken_reason: str | None = None


def truncate_tail(
    text: str,
    max_lines: int = TAIL_MAX_LINES,
    max_chars: int = TAIL_MAX_CHARS,
) -> str:
    """Return the last max_lines lines of text, capped at max_chars characters."""
    if not text:
        return ""
    lines = text.splitlines(keepends=True)
    if len(lines) > max_lines:
        text = "".join(lines[-max_lines:])
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse all runs of whitespace to a single ASCII space."""
    return " ".join(text.split())


def normalize_cause(cause: str | None) -> str | None:
    """Normalize a cause string (typically path:line) for deterministic grouping."""
    if cause is None:
        return None
    cleaned = cause.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("\\", "/")
    path_part, sep, suffix = cleaned.rpartition(":")
    if sep and path_part and re.fullmatch(r"\d+(?:-\d+)?", suffix):
        norm_path = posixpath.normpath(path_part)
        return f"{norm_path}:{suffix}"
    return posixpath.normpath(cleaned)


def is_exact_int(value: Any) -> bool:
    """Return True if value is an int and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_relpath(raw: Any, *, allow_dot: bool = False) -> tuple[str | None, str | None]:
    """Validate a relative path stays within a tree root without '..' or '.git'.

    Returns:
        A tuple of (normalized_posix_path, error_message). Exactly one is non-None.
    """
    if not isinstance(raw, str) or not raw:
        return None, "path must be a non-empty string"
    if "\x00" in raw:
        return None, f"path contains NUL byte: {raw!r}"
    if raw.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(raw):
        return None, f"absolute path is not allowed: {raw}"
    normalized_raw = raw.replace("\\", "/")
    parts = [part for part in normalized_raw.split("/") if part]
    if ".." in parts:
        return None, f"path traversal ('..') is not allowed: {raw}"
    if ".git" in parts:
        return None, f"path inside '.git' is not allowed: {raw}"
    norm = posixpath.normpath(normalized_raw)
    if norm == ".." or norm.startswith("../"):
        return None, f"path traversal ('..') is not allowed: {raw}"
    if norm == ".":
        if allow_dot:
            return ".", None
        return None, f"path must name a file inside the tree: {raw}"
    return norm, None


_GIT_C_ESCAPES: dict[str, int] = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    '"': 0x22,
    "\\": 0x5C,
}


def _unquote_git_path(token: str) -> str:
    """Decode a C-quoted git diff path into a UTF-8 string.

    Raises:
        ValueError: If a quoted path has an invalid escape, unterminated quote,
            or non-UTF-8 byte sequence.
    """
    if not token.startswith('"'):
        return token
    if len(token) < 2 or not token.endswith('"'):
        raise ValueError(f"unterminated quoted git path: {token!r}")

    inner = token[1:-1]
    raw = bytearray()
    idx = 0
    length = len(inner)
    while idx < length:
        ch = inner[idx]
        if ch == '"':
            raise ValueError(f"unescaped quote in git path: {token!r}")
        if ch != "\\":
            raw.extend(ch.encode("utf-8"))
            idx += 1
            continue
        idx += 1
        if idx >= length:
            raise ValueError(f"dangling backslash in git path: {token!r}")
        esc = inner[idx]
        if esc in _GIT_C_ESCAPES:
            raw.append(_GIT_C_ESCAPES[esc])
            idx += 1
        elif esc in "0123":
            octal = inner[idx : idx + 3]
            if len(octal) != 3 or any(digit not in "01234567" for digit in octal):
                raise ValueError(f"invalid octal escape in git path: {token!r}")
            raw.append(int(octal, 8))
            idx += 3
        else:
            raise ValueError(f"unsupported escape '\\{esc}' in git path: {token!r}")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"quoted git path is not valid UTF-8: {token!r}") from exc


def _parse_diff_git_paths(rest: str) -> list[str]:
    """Extract the two paths from the remainder of a 'diff --git ' line."""
    if rest.startswith('"'):
        tokens: list[str] = []
        idx = 0
        while idx < len(rest):
            while idx < len(rest) and rest[idx] == " ":
                idx += 1
            if idx >= len(rest):
                break
            if rest[idx] == '"':
                end = idx + 1
                while end < len(rest):
                    if rest[end] == "\\" and end + 1 < len(rest):
                        end += 2
                        continue
                    if rest[end] == '"':
                        end += 1
                        break
                    end += 1
                if end < len(rest) and rest[end] != " ":
                    raise ValueError(f"unexpected characters after quoted path: {rest[idx:]!r}")
                tokens.append(_unquote_git_path(rest[idx:end]))
                idx = end
            elif len(tokens) == 1:
                tokens.append(_unquote_git_path(rest[idx:]))
                break
            else:
                end = rest.find(" ", idx)
                if end == -1:
                    end = len(rest)
                tokens.append(_unquote_git_path(rest[idx:end]))
                idx = end
        return tokens

    # Unquoted left path paired with a C-quoted right path (e.g. rename to non-ASCII).
    quoted_marker = ' "b/'
    if rest.startswith("a/") and rest.count(quoted_marker) == 1:
        left, right = rest.split(quoted_marker, 1)
        return [left, _unquote_git_path(f'"b/{right}')]

    # Standard unquoted 'a/<path1> b/<path2>' form handles spaces in filenames.
    marker = " b/"
    if rest.startswith("a/") and rest.count(marker) == 1:
        left, right = rest.split(marker, 1)
        return [left, f"b/{right}"]
    return [_unquote_git_path(tok) for tok in rest.split(" ", 1)]


def _strip_diff_prefix(path: str, prefix: str) -> str:
    """Strip a single leading git diff prefix (such as 'a/' or 'b/')."""
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


def _strip_header_timestamp(rest: str) -> str:
    """Strip a trailing tab-separated timestamp from a --- / +++ header path."""
    if rest.startswith('"'):
        # Find closing unescaped quote.
        end = 1
        while end < len(rest):
            if rest[end] == "\\" and end + 1 < len(rest):
                end += 2
                continue
            if rest[end] == '"':
                return _unquote_git_path(rest[: end + 1])
            end += 1
        return _unquote_git_path(rest)
    return rest.split("\t", 1)[0]


def validate_patch_targets(patch: str) -> str | None:
    """Validate that all target paths in a unified diff are safe relative paths.

    Returns:
        An error string if any target path is invalid, otherwise None.
    """
    old_rem = 0
    new_rem = 0
    in_binary = False

    for line in patch.splitlines():
        if in_binary:
            if line.startswith("diff --git "):
                in_binary = False
            else:
                continue

        if old_rem > 0 or new_rem > 0:
            if line.startswith("-"):
                old_rem = max(0, old_rem - 1)
                continue
            if line.startswith("+"):
                new_rem = max(0, new_rem - 1)
                continue
            if line.startswith(" ") or line == "":
                old_rem = max(0, old_rem - 1)
                new_rem = max(0, new_rem - 1)
                continue
            if line.startswith("\\"):
                continue
            # Unexpected header line terminates an incomplete hunk cleanly.
            old_rem = 0
            new_rem = 0

        if line == "GIT binary patch":
            in_binary = True
            continue

        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            old_rem = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            new_rem = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
            continue

        candidates: list[str] = []
        try:
            if line.startswith("diff --git "):
                raw_paths = _parse_diff_git_paths(line[len("diff --git ") :])
                if len(raw_paths) >= 1:
                    candidates.append(_strip_diff_prefix(raw_paths[0], "a/"))
                if len(raw_paths) >= 2:
                    candidates.append(_strip_diff_prefix(raw_paths[1], "b/"))
            elif line.startswith("--- "):
                target = _strip_header_timestamp(line[4:])
                if target != "/dev/null":
                    candidates.append(_strip_diff_prefix(target, "a/"))
            elif line.startswith("+++ "):
                target = _strip_header_timestamp(line[4:])
                if target != "/dev/null":
                    candidates.append(_strip_diff_prefix(target, "b/"))
            elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
                target = _unquote_git_path(line.split(" ", 2)[2])
                candidates.append(target)
        except ValueError as exc:
            return f"invalid patch target ({exc})"

        for candidate in candidates:
            _, err = validate_relpath(candidate, allow_dot=False)
            if err is not None:
                return f"invalid patch target ({err})"

    return None


_STRIPPED_GIT_ENV_VARS: tuple[str, ...] = (
    "GIT_INDEX_FILE",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)
_EMPTY_HOOKS_HOLDER: list[tempfile.TemporaryDirectory[str]] = []


def _cleanup_empty_hooks_dir() -> None:
    """Remove the process-scoped empty hooks directory if one was created."""
    while _EMPTY_HOOKS_HOLDER:
        temp_dir = _EMPTY_HOOKS_HOLDER.pop()
        temp_dir.cleanup()


atexit.register(_cleanup_empty_hooks_dir)


def _get_empty_hooks_dir() -> Path:
    """Return a process-scoped empty temporary directory to disable git hooks."""
    if _EMPTY_HOOKS_HOLDER and Path(_EMPTY_HOOKS_HOLDER[0].name).is_dir():
        return Path(_EMPTY_HOOKS_HOLDER[0].name)
    _cleanup_empty_hooks_dir()
    temp_dir = tempfile.TemporaryDirectory(prefix="council-empty-hooks-")
    _EMPTY_HOOKS_HOLDER.append(temp_dir)
    return Path(temp_dir.name)


def _git_env() -> dict[str, str]:
    """Build a sanitized environment for tool-owned git invocations."""
    env = os.environ.copy()
    for var in _STRIPPED_GIT_ENV_VARS:
        env.pop(var, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(
    repo_dir: Path,
    args: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Execute a git command in repo_dir with hooks disabled and isolated git env."""
    hooks_dir = _get_empty_hooks_dir()
    cmd = [
        "git",
        "-C",
        str(repo_dir),
        "-c",
        f"core.hooksPath={hooks_dir}",
        "-c",
        "diff.autoRefreshIndex=false",
        *args,
    ]
    proc = subprocess.run(
        cmd,
        input=input_bytes,
        env=_git_env(),
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr_tail = truncate_tail(proc.stderr.decode("utf-8", errors="replace"))
        raise InputError(
            f"git command failed (exit {proc.returncode}): {shlex.join(cmd)}\n{stderr_tail}"
        )
    return proc


def _resolve_symlink_contained(tree_root: Path, relpath: str) -> Path | None:
    """Verify no existing symlink component along tree_root/relpath escapes tree_root."""
    resolved_root = tree_root.resolve()
    current = tree_root
    parts = Path(relpath).parts
    for part in parts[:-1]:
        current = current / part
        if (current.exists() or current.is_symlink()) and not current.resolve().is_relative_to(
            resolved_root
        ):
            return None
    target = tree_root / relpath
    if target.parent.exists() and not target.parent.resolve().is_relative_to(resolved_root):
        return None
    return target


def _compute_candidate_id(
    head_sha: str,
    patch_bytes: bytes,
    repo_root: Path,
    untracked_paths: Sequence[str],
) -> str:
    """Compute the deterministic candidate_id from HEAD, patch, and untracked files."""
    hasher = hashlib.sha256()
    hasher.update(b"patch\x00")
    hasher.update(str(len(patch_bytes)).encode("ascii"))
    hasher.update(b"\x00")
    hasher.update(patch_bytes)

    for rel in untracked_paths:
        src = repo_root / rel
        rel_bytes = rel.encode("utf-8")
        if src.is_symlink():
            target_bytes = os.fsencode(os.readlink(src))
            hasher.update(b"symlink\x00")
            hasher.update(rel_bytes)
            hasher.update(b"\x00")
            hasher.update(str(len(target_bytes)).encode("ascii"))
            hasher.update(b"\x00")
            hasher.update(target_bytes)
        else:
            content = src.read_bytes()
            mode = stat.S_IMODE(src.lstat().st_mode)
            hasher.update(b"file\x00")
            hasher.update(rel_bytes)
            hasher.update(b"\x00")
            hasher.update(f"{mode:o}".encode("ascii"))
            hasher.update(b"\x00")
            hasher.update(str(len(content)).encode("ascii"))
            hasher.update(b"\x00")
            hasher.update(content)

    return f"{head_sha[:12]}+{hasher.hexdigest()[:12]}"


def materialize(repo: Path, base_ref: str, out_dir: Path) -> dict[str, Any]:
    """Create detached base and candidate worktrees and write trees.json."""
    if not repo.exists() or not repo.is_dir():
        raise InputError(f"--repo does not exist or is not a directory: {repo}")
    if not base_ref or base_ref.startswith("-"):
        raise InputError(f"invalid --base ref: {base_ref!r}")

    repo_root_str = run_git(repo, ["rev-parse", "--show-toplevel"]).stdout.decode("utf-8").strip()
    repo_root = Path(repo_root_str).resolve()
    resolved_out = out_dir.expanduser().resolve()

    if resolved_out == repo_root or resolved_out.is_relative_to(repo_root):
        raise InputError(f"--out must be outside the repository root ({repo_root}): {resolved_out}")
    if repo_root.is_relative_to(resolved_out):
        raise InputError(f"--out cannot be a parent of the repository root ({repo_root}): {resolved_out}")

    if resolved_out.exists() or resolved_out.is_symlink():
        if not resolved_out.is_dir():
            raise InputError(f"--out exists and is not an empty directory: {resolved_out}")
        if any(resolved_out.iterdir()):
            raise InputError(f"--out must be absent or empty: {resolved_out}")
    else:
        resolved_out.mkdir(parents=True, exist_ok=True)

    head_sha = run_git(repo_root, ["rev-parse", "--verify", "HEAD"]).stdout.decode("utf-8").strip()
    base_commit = (
        run_git(repo_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        .stdout.decode("utf-8")
        .strip()
    )
    base_sha = (
        run_git(repo_root, ["merge-base", base_commit, head_sha]).stdout.decode("utf-8").strip()
    )

    patch_bytes = run_git(
        repo_root,
        [
            "-c",
            "diff.noprefix=false",
            "-c",
            "diff.mnemonicPrefix=false",
            "diff",
            "--no-ext-diff",
            "--binary",
            "HEAD",
        ],
    ).stdout

    untracked_raw = run_git(
        repo_root,
        ["ls-files", "-o", "--exclude-standard", "-z"],
    ).stdout
    untracked_paths: list[str] = []
    for raw_entry in untracked_raw.split(b"\x00"):
        if not raw_entry:
            continue
        try:
            rel = raw_entry.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputError(f"untracked path is not valid UTF-8: {raw_entry!r}") from exc
        src = repo_root / rel
        if not src.is_symlink() and not src.is_file():
            raise InputError(f"unsupported untracked entry (not a file or symlink): {rel}")
        untracked_paths.append(rel)
    untracked_paths.sort()

    base_dir = resolved_out / "base"
    candidate_dir = resolved_out / "candidate"
    created_worktrees: list[Path] = []

    try:
        run_git(repo_root, ["worktree", "add", "--detach", str(base_dir), base_sha])
        created_worktrees.append(base_dir)

        run_git(repo_root, ["worktree", "add", "--detach", str(candidate_dir), head_sha])
        created_worktrees.append(candidate_dir)

        if patch_bytes:
            # --index preserves files that were force-added despite .gitignore.
            run_git(
                candidate_dir,
                ["apply", "--binary", "--index", "--whitespace=nowarn", "-"],
                input_bytes=patch_bytes,
            )

        for rel in untracked_paths:
            src = repo_root / rel
            dst = candidate_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            if src.is_symlink():
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst)

        run_git(candidate_dir, ["add", "-A"])
        run_git(
            candidate_dir,
            [
                "-c",
                "user.name=council",
                "-c",
                "user.email=council@localhost",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--no-verify",
                "--allow-empty",
                "-q",
                "-m",
                "council candidate snapshot",
            ],
        )
        candidate_snapshot = (
            run_git(candidate_dir, ["rev-parse", "HEAD"]).stdout.decode("utf-8").strip()
        )
    except BaseException:
        for worktree_path in reversed(created_worktrees):
            run_git(repo_root, ["worktree", "remove", "--force", str(worktree_path)], check=False)
        run_git(repo_root, ["worktree", "prune"], check=False)
        raise

    patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    candidate_id = _compute_candidate_id(head_sha, patch_bytes, repo_root, untracked_paths)

    trees_data: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "repo": str(repo_root),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "patch_sha256": patch_sha256,
        "untracked": untracked_paths,
        "candidate_id": candidate_id,
        "base_snapshot": base_sha,
        "candidate_snapshot": candidate_snapshot,
    }
    trees_path = resolved_out / "trees.json"
    trees_path.write_text(json.dumps(trees_data, indent=2) + "\n", encoding="utf-8")
    return trees_data


def load_json_file(path: Path, label: str) -> Any:
    """Read and parse a JSON file, raising InputError on failure."""
    if not path.is_file():
        raise InputError(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"failed to parse {label} ({path}): {exc}") from exc


def load_trees(trees_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    """Load and validate trees.json and verify base/candidate directories exist."""
    resolved_dir = trees_dir.expanduser().resolve()
    trees_path = resolved_dir / "trees.json"
    data = load_json_file(trees_path, "trees.json")
    if not isinstance(data, dict):
        raise InputError(f"trees.json must be a JSON object: {trees_path}")
    if data.get("version") != SCHEMA_VERSION:
        raise InputError(f"unsupported trees.json version: {data.get('version')!r}")
    for key in ("repo", "base_snapshot", "candidate_snapshot", "candidate_id"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise InputError(f"trees.json missing valid '{key}': {trees_path}")

    base_dir = resolved_dir / "base"
    candidate_dir = resolved_dir / "candidate"
    if not base_dir.is_dir() or not candidate_dir.is_dir():
        raise InputError(f"base or candidate worktree missing under: {resolved_dir}")
    return data, base_dir, candidate_dir


@contextmanager
def trees_lock(trees_dir: Path) -> Iterator[None]:
    """Hold an exclusive lock on the council trees while a command resets or reads them.

    ``validate`` runs once per seat as seats finish, often in parallel, and each
    reset of the shared candidate worktree would otherwise race on git's index lock.
    The lock is taken on ``trees.json``, which always exists and can be locked
    through a read-only descriptor, so a read-only trees directory still works.
    """
    with (trees_dir / "trees.json").open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def cleanup(out_dir: Path) -> None:
    """Remove materialized worktrees and trees.json created in out_dir."""
    resolved_out = out_dir.expanduser().resolve()
    trees_path = resolved_out / "trees.json"
    data = load_json_file(trees_path, "trees.json")
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise InputError(f"invalid trees.json: {trees_path}")
    repo_str = data.get("repo")
    if not isinstance(repo_str, str) or not repo_str:
        raise InputError(f"trees.json missing 'repo': {trees_path}")
    repo_root = Path(repo_str)
    if not repo_root.is_dir():
        raise InputError(f"source repository no longer exists: {repo_root}")

    for role in ("base", "candidate"):
        tree_dir = resolved_out / role
        if tree_dir.exists() or tree_dir.is_symlink():
            run_git(repo_root, ["worktree", "remove", "--force", str(tree_dir)])
    run_git(repo_root, ["worktree", "prune"])
    trees_path.unlink(missing_ok=True)


def load_seats(path: Path) -> list[dict[str, Any]]:
    """Load and validate the seats JSON file."""
    data = load_json_file(path, "seats file")
    if not isinstance(data, dict) or not isinstance(data.get("seats"), list):
        raise InputError(f"seats file must be an object with a 'seats' list: {path}")

    seats: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for idx, raw in enumerate(data["seats"]):
        if not isinstance(raw, dict):
            raise InputError(f"seats[{idx}] must be an object")
        name = raw.get("name")
        required = raw.get("required")
        status = raw.get("status")
        provider = raw.get("provider")
        model = raw.get("model")
        note = raw.get("note")

        if not isinstance(name, str) or not name:
            raise InputError(f"seats[{idx}].name must be a non-empty string")
        if name in seen_names:
            raise InputError(f"duplicate seat name: {name}")
        if not isinstance(required, bool):
            raise InputError(f"seats[{idx}].required must be a boolean")
        if status not in VALID_SEAT_STATUSES:
            raise InputError(f"seats[{idx}].status must be one of {sorted(VALID_SEAT_STATUSES)}")
        if not isinstance(provider, str):
            raise InputError(f"seats[{idx}].provider must be a string")
        if not isinstance(model, str):
            raise InputError(f"seats[{idx}].model must be a string")
        if note is not None and not isinstance(note, str):
            raise InputError(f"seats[{idx}].note must be a string when present")

        seen_names.add(name)
        seat_record: dict[str, Any] = {
            "name": name,
            "required": required,
            "status": status,
            "provider": provider,
            "model": model,
        }
        if note is not None:
            seat_record["note"] = note
        seats.append(seat_record)
    return seats


def load_entries_files(
    paths: Iterable[Path],
    valid_seats: frozenset[str] | None,
) -> list[tuple[str, str, Any]]:
    """Load all seat entries files and check top-level schema and unique IDs.

    Returns:
        List of (seat_name, namespaced_id, raw_entry) tuples in input order.
        ``valid_seats`` of None skips the seats-file membership check.
    """
    loaded: list[tuple[str, str, Any]] = []
    seen_ids: set[str] = set()
    synthetic_counter = 0

    for path in paths:
        data = load_json_file(path, "entries file")
        if not isinstance(data, dict):
            raise InputError(f"entries file top-level JSON must be an object: {path}")
        seat = data.get("seat")
        in_scope = data.get("in_scope")
        entries = data.get("entries")

        if not isinstance(seat, str) or not seat:
            raise InputError(f"entries file missing valid 'seat': {path}")
        if valid_seats is not None and seat not in valid_seats:
            raise InputError(f"entries file seat {seat!r} is not present in seats file: {path}")
        if not isinstance(in_scope, bool):
            raise InputError(f"entries file missing boolean 'in_scope': {path}")
        if not isinstance(entries, list):
            raise InputError(f"entries file missing list 'entries': {path}")

        for raw_entry in entries:
            if isinstance(raw_entry, dict) and isinstance(raw_entry.get("id"), str) and raw_entry["id"]:
                namespaced_id = f"{seat}:{raw_entry['id']}"
                if namespaced_id in seen_ids:
                    raise InputError(f"duplicate entry id: {namespaced_id}")
                seen_ids.add(namespaced_id)
            else:
                synthetic_counter += 1
                namespaced_id = f"{seat}:<invalid-{synthetic_counter}>"
            loaded.append((seat, namespaced_id, raw_entry))

    return loaded


def _validate_citation_obj(obj: Any, label: str) -> tuple[dict[str, str] | None, str | None]:
    """Validate a single citation/intent object."""
    if not isinstance(obj, dict):
        return None, f"{label} must be an object"
    unknown = sorted(set(obj.keys()) - CITATION_KEYS)
    if unknown:
        return None, f"unknown key(s) in {label}: {', '.join(unknown)}"
    file_val = obj.get("file")
    quote_val = obj.get("quote")
    if not isinstance(file_val, str) or not file_val:
        return None, f"{label}.file must be a non-empty string"
    if not isinstance(quote_val, str) or not normalize_whitespace(quote_val):
        return None, f"{label}.quote must be a non-empty string"
    return {"file": file_val, "quote": quote_val}, None


def _validate_witness_obj(obj: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a witness specification dict and normalize defaults."""
    if not isinstance(obj, dict):
        return None, "witness must be an object"
    unknown = sorted(set(obj.keys()) - WITNESS_KEYS)
    if unknown:
        return None, f"unknown key(s) in witness: {', '.join(unknown)}"

    files_out: dict[str, str] = {}
    if "files" in obj and obj["files"] is not None:
        raw_files = obj["files"]
        if not isinstance(raw_files, dict):
            return None, "witness.files must be an object mapping relative paths to strings"
        for rel_raw in sorted(raw_files.keys()):
            content = raw_files[rel_raw]
            norm_rel, err = validate_relpath(rel_raw, allow_dot=False)
            if err is not None:
                return None, f"witness.files invalid path ({err})"
            if not isinstance(content, str):
                return None, f"witness.files[{rel_raw!r}] content must be a string"
            assert norm_rel is not None
            if norm_rel in files_out:
                return None, f"witness.files contains duplicate normalized path: {norm_rel}"
            files_out[norm_rel] = content

    patch_val = obj.get("patch")
    if patch_val is not None:
        if not isinstance(patch_val, str):
            return None, "witness.patch must be a string"
        patch_err = validate_patch_targets(patch_val)
        if patch_err is not None:
            return None, patch_err

    build_val = obj.get("build")
    if build_val is not None and (
        not isinstance(build_val, list)
        or not build_val
        or not all(isinstance(arg, str) for arg in build_val)
        or not build_val[0]
    ):
        return None, "witness.build must be a non-empty list of strings"

    run_val = obj.get("run")
    if (
        not isinstance(run_val, list)
        or not run_val
        or not all(isinstance(arg, str) for arg in run_val)
        or not run_val[0]
    ):
        return None, "witness.run must be a non-empty list of strings"

    cwd_raw = obj.get("cwd", ".")
    cwd_norm, cwd_err = validate_relpath(cwd_raw, allow_dot=True)
    if cwd_err is not None:
        return None, f"witness.cwd invalid ({cwd_err})"
    assert cwd_norm is not None

    expect_raw = obj.get("expect")
    expect_out: dict[str, Any] = {"exit": 0}
    if expect_raw is not None:
        if not isinstance(expect_raw, dict):
            return None, "witness.expect must be an object"
        unknown_expect = sorted(set(expect_raw.keys()) - EXPECT_KEYS)
        if unknown_expect:
            return None, f"unknown key(s) in witness.expect: {', '.join(unknown_expect)}"
        if "exit" in expect_raw:
            exit_spec = expect_raw["exit"]
            if exit_spec != "nonzero" and not is_exact_int(exit_spec):
                return None, "witness.expect.exit must be an integer or 'nonzero'"
            expect_out["exit"] = exit_spec
        for regex_key in ("stdout_regex", "stderr_regex"):
            if regex_key in expect_raw and expect_raw[regex_key] is not None:
                pattern = expect_raw[regex_key]
                if not isinstance(pattern, str):
                    return None, f"witness.expect.{regex_key} must be a string"
                try:
                    re.compile(pattern, re.MULTILINE)
                except re.error as exc:
                    return None, f"invalid regex in witness.expect.{regex_key}: {exc}"
                expect_out[regex_key] = pattern

    timeout_s = obj.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not is_exact_int(timeout_s) or timeout_s < 1 or timeout_s > MAX_TIMEOUT_S:
        return None, f"witness.timeout_s must be an integer between 1 and {MAX_TIMEOUT_S}"

    timeout_is_failure = obj.get("timeout_is_failure", False)
    if not isinstance(timeout_is_failure, bool):
        return None, "witness.timeout_is_failure must be a boolean"

    broken_exit_raw = obj.get("broken_exit", list(DEFAULT_BROKEN_EXIT))
    if not isinstance(broken_exit_raw, list) or not all(is_exact_int(c) for c in broken_exit_raw):
        return None, "witness.broken_exit must be a list of integers"

    normalized: dict[str, Any] = {
        "run": list(run_val),
        "cwd": cwd_norm,
        "expect": expect_out,
        "timeout_s": timeout_s,
        "timeout_is_failure": timeout_is_failure,
        "broken_exit": list(broken_exit_raw),
    }
    if files_out:
        normalized["files"] = files_out
    if patch_val is not None:
        normalized["patch"] = patch_val
    if build_val is not None:
        normalized["build"] = list(build_val)
    return normalized, None


def validate_entry(
    seat: str,
    namespaced_id: str,
    raw: Any,
) -> tuple[dict[str, Any], list[dict[str, str]], str | None]:
    """Validate a raw entry dict.

    Returns:
        (entry_record, citations_to_check, validation_error).
        Never raises on malformed entry content; reports error so the entry
        becomes 'broken witness'.
    """
    base_record: dict[str, Any] = {
        "id": namespaced_id,
        "seat": seat,
        "type": None,
        "claim": "",
        "severity": None,
        "effective_severity": None,
        "waiver": None,
        "cause": None,
        "intent": None,
        "status": "broken witness",
        "reason": None,
        "witness": None,
        "runs": {"candidate": [], "base": []},
    }

    if not isinstance(raw, dict):
        base_record["reason"] = "entry must be a JSON object"
        return base_record, [], base_record["reason"]

    # Populate safe best-effort display fields before returning on any error.
    if isinstance(raw.get("type"), str):
        base_record["type"] = raw["type"]
    if isinstance(raw.get("claim"), str):
        base_record["claim"] = raw["claim"]
    if isinstance(raw.get("cause"), str):
        base_record["cause"] = normalize_cause(raw["cause"])
    if isinstance(raw.get("intent"), dict):
        file_v = raw["intent"].get("file")
        quote_v = raw["intent"].get("quote")
        if isinstance(file_v, str) and isinstance(quote_v, str):
            base_record["intent"] = {"file": file_v, "quote": quote_v}
    if isinstance(raw.get("witness"), dict):
        base_record["witness"] = raw["witness"]

    unknown = sorted(set(raw.keys()) - ENTRY_KEYS)
    if unknown:
        reason = f"unknown key(s) in entry: {', '.join(unknown)}"
        base_record["reason"] = reason
        return base_record, [], reason

    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        reason = "entry.id must be a non-empty string"
        base_record["reason"] = reason
        return base_record, [], reason

    entry_type = raw.get("type")
    if entry_type not in VALID_ENTRY_TYPES:
        reason = f"entry.type must be one of {sorted(VALID_ENTRY_TYPES)}"
        base_record["reason"] = reason
        return base_record, [], reason
    base_record["type"] = entry_type

    claim = raw.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        reason = "entry.claim must be a non-empty string"
        base_record["reason"] = reason
        return base_record, [], reason
    base_record["claim"] = claim

    if entry_type == "finding":
        sev = raw.get("severity")
        if sev not in VALID_SEVERITIES:
            reason = f"finding requires severity in {sorted(VALID_SEVERITIES)}"
            base_record["reason"] = reason
            return base_record, [], reason
        base_record["severity"] = sev
    elif entry_type == "held":
        # Severity is explicitly ignored for held entries; if it fails later as a
        # real break, evaluate_entry assigns 'should'.
        base_record["severity"] = None
    else:
        sev = raw.get("severity")
        if sev is None:
            base_record["severity"] = "nit"
        elif sev in VALID_SEVERITIES:
            base_record["severity"] = sev
        else:
            reason = f"advisory severity must be in {sorted(VALID_SEVERITIES)}"
            base_record["reason"] = reason
            return base_record, [], reason
    base_record["effective_severity"] = base_record["severity"]

    if "cause" in raw and raw["cause"] is not None:
        if not isinstance(raw["cause"], str) or not raw["cause"].strip():
            reason = "entry.cause must be a non-empty string when present"
            base_record["reason"] = reason
            return base_record, [], reason
        base_record["cause"] = normalize_cause(raw["cause"])

    citations_to_check: list[dict[str, str]] = []
    raw_intent = raw.get("intent")
    if entry_type in ("finding", "held"):
        if raw_intent is None:
            reason = f"{entry_type} requires intent"
            base_record["reason"] = reason
            return base_record, [], reason
        intent_obj, intent_err = _validate_citation_obj(raw_intent, "intent")
        if intent_err is not None:
            base_record["reason"] = intent_err
            return base_record, [], intent_err
        assert intent_obj is not None
        base_record["intent"] = intent_obj
        citations_to_check.append(intent_obj)
    elif raw_intent is not None:
        intent_obj, intent_err = _validate_citation_obj(raw_intent, "intent")
        if intent_err is not None:
            base_record["reason"] = intent_err
            return base_record, [], intent_err
        assert intent_obj is not None
        base_record["intent"] = intent_obj
        citations_to_check.append(intent_obj)

    raw_citations = raw.get("citations")
    if raw_citations is not None:
        if not isinstance(raw_citations, list):
            reason = "entry.citations must be a list"
            base_record["reason"] = reason
            return base_record, [], reason
        for idx, item in enumerate(raw_citations):
            cit_obj, cit_err = _validate_citation_obj(item, f"citations[{idx}]")
            if cit_err is not None:
                base_record["reason"] = cit_err
                return base_record, [], cit_err
            assert cit_obj is not None
            citations_to_check.append(cit_obj)

    has_witness = "witness" in raw and raw["witness"] is not None
    if entry_type == "advisory":
        if has_witness:
            reason = "advisory entry must not include a witness"
            base_record["reason"] = reason
            return base_record, [], reason
        base_record["witness"] = None
    else:
        if not has_witness:
            reason = f"{entry_type} requires a witness"
            base_record["reason"] = reason
            return base_record, [], reason
        witness_obj, witness_err = _validate_witness_obj(raw["witness"])
        if witness_err is not None:
            base_record["reason"] = witness_err
            return base_record, [], witness_err
        base_record["witness"] = witness_obj

    base_record["status"] = "advisory" if entry_type == "advisory" else "pending"
    return base_record, citations_to_check, None


def _resolve_citation_path(raw_file: str, candidate_dir: Path) -> tuple[Path | None, str | None]:
    """Resolve a citation path, keeping relative paths strictly inside candidate_dir."""
    if "\x00" in raw_file:
        return None, f"invalid citation path (path contains NUL byte: {raw_file!r})"

    raw_path = Path(raw_file)
    if raw_path.is_absolute():
        return raw_path, None

    if raw_file.startswith("~"):
        try:
            expanded = raw_path.expanduser()
        except (OSError, RuntimeError) as exc:
            return None, f"invalid citation path: {raw_file} ({exc})"
        if expanded.is_absolute():
            return expanded, None

    norm_rel, err = validate_relpath(raw_file, allow_dot=False)
    if err is not None:
        return None, f"invalid citation path ({err})"
    assert norm_rel is not None

    try:
        resolved_root = candidate_dir.resolve()
        contained = _resolve_symlink_contained(candidate_dir, norm_rel)
        if contained is None:
            return None, f"citation path escapes candidate tree: {raw_file}"
        resolved_target = contained.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"invalid citation path ({raw_file}): {exc}"

    if not resolved_target.is_relative_to(resolved_root):
        return None, f"citation path escapes candidate tree: {raw_file}"
    return resolved_target, None


def check_citations(citations: Sequence[dict[str, str]], candidate_dir: Path) -> str | None:
    """Verify every cited quote appears in its target file after whitespace normalization."""
    for citation in citations:
        raw_file = citation["file"]
        target, path_err = _resolve_citation_path(raw_file, candidate_dir)
        if path_err is not None:
            return path_err
        assert target is not None
        if not target.is_file():
            return f"citation not found: {raw_file}"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f"citation not found: {raw_file}"
        norm_quote = normalize_whitespace(citation["quote"])
        if not norm_quote or norm_quote not in normalize_whitespace(content):
            return f"citation not found: {raw_file}"
    return None


def list_ignored_paths(tree_dir: Path) -> frozenset[str]:
    """Return all untracked gitignored paths currently present in tree_dir."""
    raw = run_git(
        tree_dir,
        ["ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
    ).stdout
    return frozenset(os.fsdecode(entry) for entry in raw.split(b"\x00") if entry)


def _add_user_perms(path: Path, bits: int, *, root: Path | None = None) -> None:
    """Add owner permission bits to path without following symlinks or touching '.git'."""
    try:
        st = path.lstat()
    except OSError:
        return
    if stat.S_ISLNK(st.st_mode):
        return
    if root is None:
        if path.name == ".git":
            return
    else:
        resolved_root = root.resolve()
        try:
            rel = path.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            return
        if rel.parts and rel.parts[0] == ".git":
            return
    os.chmod(path, stat.S_IMODE(st.st_mode) | bits)


def _make_entry_writable(target: Path, *, root: Path | None = None) -> None:
    """Make target's parent directory and target itself owner-writable (never inside '.git')."""
    parent = target.parent
    if parent != target:
        _add_user_perms(parent, stat.S_IWUSR | stat.S_IXUSR, root=root)
    try:
        st = target.lstat()
    except OSError:
        return
    if stat.S_ISLNK(st.st_mode):
        return
    extra = (
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        if stat.S_ISDIR(st.st_mode)
        else stat.S_IWUSR
    )
    _add_user_perms(target, extra, root=root)


def _make_tree_writable(target: Path, *, root: Path) -> None:
    """Make target, its parents up to root, and non-symlink descendants writable."""
    resolved_root = root.resolve()
    ancestors: list[Path] = []
    cur = target.parent
    while cur != root and cur.is_relative_to(root):
        ancestors.append(cur)
        cur = cur.parent
    if cur == root:
        ancestors.append(root)
    for anc in reversed(ancestors):
        _add_user_perms(anc, stat.S_IWUSR | stat.S_IXUSR, root=resolved_root)

    try:
        st = target.lstat()
    except OSError:
        return
    if stat.S_ISLNK(st.st_mode):
        return
    if not stat.S_ISDIR(st.st_mode):
        _add_user_perms(target, stat.S_IWUSR, root=resolved_root)
        return

    _add_user_perms(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR, root=resolved_root)
    for dirpath, dirnames, filenames in os.walk(target, topdown=True, followlinks=False):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dname in dirnames:
            child_dir = current / dname
            if current == root and dname == ".git":
                continue
            if child_dir.is_symlink():
                continue
            _add_user_perms(
                child_dir,
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
                root=resolved_root,
            )
            kept_dirs.append(dname)
        dirnames[:] = kept_dirs
        for fname in filenames:
            child_file = current / fname
            if not child_file.is_symlink():
                _add_user_perms(child_file, stat.S_IWUSR, root=resolved_root)


def _retry_permission_op(
    func: Callable[[str], Any],
    path: str,
    exc: BaseException,
    *,
    root: Path | None = None,
) -> None:
    """Make a read-only entry writable and retry func(path) once on PermissionError."""
    if not isinstance(exc, PermissionError):
        raise exc
    _make_entry_writable(Path(path), root=root)
    func(path)


def _handle_rmtree_error(func: Any, path: str, exc: BaseException) -> None:
    """Make read-only entries writable and retry once during rmtree cleanup.

    An unreadable directory fails in ``os.open`` or ``os.scandir``, which need more
    than a path, so it is made accessible and removed by a fresh ``rmtree``.
    """
    if func in (os.open, os.scandir) and isinstance(exc, PermissionError) and not Path(path).is_symlink():
        os.chmod(path, stat.S_IRWXU)
        shutil.rmtree(path)
        return
    _retry_permission_op(func, path, exc)


def _make_untracked_writable(tree_dir: Path) -> None:
    """Make untracked directories and files in tree_dir writable so git clean can remove them."""
    proc = run_git(
        tree_dir,
        ["ls-files", "-z", "--others", "--exclude-standard", "--directory"],
        check=False,
    )
    if proc.returncode != 0:
        return
    for raw_entry in proc.stdout.split(b"\x00"):
        if not raw_entry:
            continue
        rel = os.fsdecode(raw_entry).rstrip("/")
        if not rel:
            continue
        target = _resolve_symlink_contained(tree_dir, rel)
        if target is None:
            continue
        _make_tree_writable(target, root=tree_dir)


def _prune_extra_ignored(tree_dir: Path, baseline_ignored: frozenset[str]) -> None:
    """Delete gitignored paths not in baseline_ignored and remove empty parent dirs."""
    extra = list_ignored_paths(tree_dir) - baseline_ignored
    if not extra:
        return
    resolved_root = tree_dir.resolve()
    for rel in sorted(extra, reverse=True):
        rel_clean = rel.rstrip("/")
        if not rel_clean:
            continue
        target = _resolve_symlink_contained(tree_dir, rel_clean)
        if target is None or not target.parent.resolve().is_relative_to(resolved_root):
            continue
        if target.is_symlink() or not target.is_dir():
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass
            except PermissionError as exc:
                try:
                    _retry_permission_op(os.unlink, str(target), exc, root=tree_dir)
                except FileNotFoundError:
                    pass
                except OSError as retry_exc:
                    raise InputError(
                        f"failed to remove ignored path {target}: {retry_exc}"
                    ) from retry_exc
        else:
            try:
                shutil.rmtree(target)
            except PermissionError:
                _make_tree_writable(target, root=tree_dir)
                try:
                    shutil.rmtree(target)
                except OSError as retry_exc:
                    raise InputError(
                        f"failed to remove ignored directory {target}: {retry_exc}"
                    ) from retry_exc
        parent = target.parent
        while parent != tree_dir and parent.is_relative_to(tree_dir):
            try:
                os.rmdir(parent)
            except FileNotFoundError:
                pass
            except PermissionError as exc:
                try:
                    _retry_permission_op(os.rmdir, str(parent), exc, root=tree_dir)
                except FileNotFoundError:
                    pass
                except OSError as retry_exc:
                    if retry_exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                        break
                    raise InputError(
                        f"failed to remove empty ignored directory {parent}: {retry_exc}"
                    ) from retry_exc
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    break
                raise InputError(
                    f"failed to remove empty ignored directory {parent}: {exc}"
                ) from exc
            parent = parent.parent


def reset_tree(
    tree_dir: Path,
    snapshot: str,
    *,
    baseline_ignored: frozenset[str] | None = None,
) -> None:
    """Reset a worktree to its snapshot commit and clean post-setup untracked state.

    Uses ``git clean -ffdq`` (without ``-x``) so nested git repositories and
    untracked non-ignored files are removed while gitignored setup outputs
    (such as ``.venv``, ``node_modules``, ``target``) survive. When
    ``baseline_ignored`` is provided, every gitignored path not in that
    post-setup baseline is deleted and directories left empty are pruned.

    Note:
        Modifications to gitignored files that already existed after ``--setup``
        are not reverted.
        Empty directories created by ``--setup`` may be removed when a witness
        adds a file inside them.
    """
    run_git(tree_dir, ["reset", "--hard", "-q", snapshot])
    # -ff removes nested git repos; omit -x so gitignored setup outputs survive.
    clean_proc = run_git(tree_dir, ["clean", "-ffdq"], check=False)
    if clean_proc.returncode != 0:
        _make_untracked_writable(tree_dir)
        run_git(tree_dir, ["clean", "-ffdq"])
    if baseline_ignored is not None:
        _prune_extra_ignored(tree_dir, baseline_ignored)


# Allowlist of ambient environment variables forwarded to witnesses (paths,
# locale, XDG base dirs, and common toolchain settings). Keeping an explicit
# allowlist prevents desktop/IPC sockets, credentials, and ambient state from
# leaking into witness runs; SECRET_ENV_RE further strips secret-looking names
# under allowed prefixes (such as UV_INDEX_X_PASSWORD or CARGO_REGISTRIES_X_TOKEN).
ALLOWED_WITNESS_ENV_VARS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "TERM",
    "TZ",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "JAVA_HOME",
    "GOPATH",
    "GOROOT",
    "GOCACHE",
    "GOMODCACHE",
    "CC",
    "CXX",
    "PKG_CONFIG_PATH",
    "RUSTFLAGS",
    "RUSTC_WRAPPER",
    "RUSTDOCFLAGS",
})
ALLOWED_WITNESS_ENV_PREFIXES: tuple[str, ...] = (
    "LC_",
    "COUNCIL_",
    "UV_",
    "PYTHON",
    "CARGO_",
    "RUSTUP_",
    "SCCACHE_",
    "NODE_",
    "BUN_",
    "PNPM_",
    "DENO_",
)
SECRET_ENV_RE = re.compile(
    r"TOKEN|SECRET|PASS|PWD|CREDENTIAL|AUTH|KEY|DSN|COOKIE|SESSION",
    re.IGNORECASE,
)


def isolate_witness_env(env: Mapping[str, str], runtime_dir: Path) -> dict[str, str]:
    """Filter env to the witness allowlist minus secrets and set XDG_RUNTIME_DIR.

    Only variables in ``ALLOWED_WITNESS_ENV_VARS`` or starting with a prefix in
    ``ALLOWED_WITNESS_ENV_PREFIXES`` are kept, and any kept name matching
    ``SECRET_ENV_RE`` is dropped. ``XDG_RUNTIME_DIR`` is always pointed at the
    per-run private ``runtime_dir`` so witnesses cannot reach user session,
    audio, or IPC sockets.
    """
    isolated = {
        name: value
        for name, value in env.items()
        if (name in ALLOWED_WITNESS_ENV_VARS or name.startswith(ALLOWED_WITNESS_ENV_PREFIXES))
        and not SECRET_ENV_RE.search(name)
    }
    isolated["XDG_RUNTIME_DIR"] = str(runtime_dir)
    return isolated


def build_witness_env(
    role: str,
    tree_dir: Path,
    base_dir: Path,
    candidate_dir: Path,
) -> dict[str, str]:
    """Build the subprocess environment for a setup command, the base for a witness's."""
    env = os.environ.copy()
    env["COUNCIL_ROLE"] = role
    env["COUNCIL_TREE"] = str(tree_dir)
    env["COUNCIL_BASE_TREE"] = str(base_dir)
    env["COUNCIL_CANDIDATE_TREE"] = str(candidate_dir)
    return env


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Force-kill proc's entire process group, reap proc, and drain survivors."""
    pgid = proc.pid
    group_missing = False
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        group_missing = True
    try:
        proc.wait()
    except OSError:
        pass
    if group_missing:
        return
    # Wait briefly for reparented grandchildren in the group to finish exiting
    # so background writers cannot race with the next tree reset or run.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            break
        time.sleep(0.005)


def _limit_fsize(max_bytes: int) -> Callable[[], None]:
    """Return a preexec_fn that caps child file growth via RLIMIT_FSIZE."""

    def _apply() -> None:
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        new_hard = max_bytes if hard == resource.RLIM_INFINITY else min(hard, max_bytes)
        new_soft = new_hard if soft == resource.RLIM_INFINITY else min(soft, new_hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (new_soft, new_hard))

    return _apply


def _read_tail_bytes(handle: Any, max_bytes: int = CAPTURE_TAIL_BYTES) -> tuple[bytes, int]:
    """Read at most the trailing max_bytes from handle and return (bytes, total_size)."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(max(0, size - max_bytes), os.SEEK_SET)
    return handle.read(max_bytes), size


def exec_process(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: int,
    *,
    max_file_bytes: int | None = WITNESS_MAX_FILE_BYTES,
) -> ProcessResult:
    """Run argv in a new session, killing its process group on exit or timeout."""
    start = time.monotonic()
    preexec_fn = _limit_fsize(max_file_bytes) if max_file_bytes is not None else None
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        proc: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            try:
                proc = subprocess.Popen(
                    list(argv),
                    cwd=str(cwd),
                    env=dict(env),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                    # The runner is single-threaded and the hook only calls setrlimit.
                    preexec_fn=preexec_fn,  # noqa: PLW1509
                )
            except FileNotFoundError as exc:
                return ProcessResult(
                    exit_code=127,
                    stdout="",
                    stderr=str(exc),
                    duration_s=round(time.monotonic() - start, 3),
                    timed_out=False,
                    spawn_error=f"command not found: {argv[0]}",
                )
            except PermissionError as exc:
                return ProcessResult(
                    exit_code=126,
                    stdout="",
                    stderr=str(exc),
                    duration_s=round(time.monotonic() - start, 3),
                    timed_out=False,
                    spawn_error=f"permission denied: {argv[0]}",
                )
            except OSError as exc:
                return ProcessResult(
                    exit_code=126,
                    stdout="",
                    stderr=str(exc),
                    duration_s=round(time.monotonic() - start, 3),
                    timed_out=False,
                    spawn_error=f"failed to execute {argv[0]}: {exc}",
                )

            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
            duration_s = round(time.monotonic() - start, 3)
        finally:
            if proc is not None:
                _kill_process_group(proc)

        stdout_bytes, stdout_size = _read_tail_bytes(stdout_file)
        stderr_bytes, stderr_size = _read_tail_bytes(stderr_file)

    exit_code = None if timed_out else proc.returncode
    fsize_exceeded = not timed_out and (
        exit_code == -signal.SIGXFSZ
        or (
            max_file_bytes is not None
            and exit_code not in (None, 0)
            and (stdout_size >= max_file_bytes or stderr_size >= max_file_bytes)
        )
    )
    return ProcessResult(
        exit_code=exit_code,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        duration_s=duration_s,
        timed_out=timed_out,
        fsize_exceeded=fsize_exceeded,
    )


def run_setup_commands(
    setup_cmds: Sequence[str],
    base_dir: Path,
    candidate_dir: Path,
) -> None:
    """Execute each --setup shell command once in base and candidate roots."""
    if not setup_cmds:
        return
    for role, tree_dir in (("base", base_dir), ("candidate", candidate_dir)):
        env = build_witness_env(role, tree_dir, base_dir, candidate_dir)
        for cmd in setup_cmds:
            proc = subprocess.run(
                ["sh", "-c", cmd],
                cwd=str(tree_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                stdout_tail = truncate_tail(proc.stdout.decode("utf-8", errors="replace"))
                stderr_tail = truncate_tail(proc.stderr.decode("utf-8", errors="replace"))
                parts = [p for p in (stdout_tail, stderr_tail) if p]
                tail = "\n".join(parts)
                raise InputError(
                    f"setup failed in {role} tree (exit {proc.returncode}): {cmd}\n{tail}"
                )


def run_witness_once(
    witness: Mapping[str, Any],
    *,
    role: str,
    tree_dir: Path,
    snapshot: str,
    base_dir: Path,
    candidate_dir: Path,
    baseline_ignored: frozenset[str] | None = None,
) -> RunDetail:
    """Reset tree, materialize witness inputs, build, run, and classify one run."""
    reset_tree(tree_dir, snapshot, baseline_ignored=baseline_ignored)

    def make_outcome(
        outcome: str,
        exit_code: int | None,
        duration_s: float,
        stdout: str,
        stderr: str,
        broken_reason: str | None = None,
    ) -> RunDetail:
        effective_outcome = "n/a" if (role == "base" and outcome == "broken") else outcome
        return RunDetail(
            record={
                "outcome": effective_outcome,
                "exit": exit_code,
                "duration_s": round(duration_s, 3),
                "stdout_tail": truncate_tail(stdout),
                "stderr_tail": truncate_tail(stderr),
            },
            broken_reason=broken_reason,
        )

    patch_text = witness.get("patch")
    if patch_text:
        patch_start = time.monotonic()
        patch_proc = run_git(
            tree_dir,
            ["apply", "--whitespace=nowarn", "-"],
            input_bytes=patch_text.encode("utf-8"),
            check=False,
        )
        if patch_proc.returncode != 0:
            return make_outcome(
                "broken",
                patch_proc.returncode,
                time.monotonic() - patch_start,
                patch_proc.stdout.decode("utf-8", errors="replace"),
                patch_proc.stderr.decode("utf-8", errors="replace"),
                broken_reason="patch failed to apply",
            )

    files_map = witness.get("files")
    if files_map:
        for relpath in sorted(files_map.keys()):
            try:
                target = _resolve_symlink_contained(tree_dir, relpath)
                if target is None:
                    detail = f"file path escapes tree via symlink: {relpath}"
                    return make_outcome("broken", None, 0.0, "", detail, broken_reason=detail)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    target.unlink()
                target.write_text(files_map[relpath], encoding="utf-8")
            except (OSError, RuntimeError) as exc:
                detail = f"failed to write witness file {relpath}: {exc}"
                return make_outcome("broken", None, 0.0, "", detail, broken_reason=detail)

    cwd_rel = witness["cwd"]
    work_dir = tree_dir if cwd_rel == "." else tree_dir / cwd_rel
    try:
        cwd_valid = work_dir.is_dir() and work_dir.resolve().is_relative_to(tree_dir.resolve())
    except (OSError, RuntimeError):
        cwd_valid = False
    if not cwd_valid:
        return make_outcome(
            "broken",
            None,
            0.0,
            "",
            f"cwd does not exist: {cwd_rel}",
            broken_reason=f"cwd does not exist: {cwd_rel}",
        )

    runtime_dir = Path(tempfile.mkdtemp(prefix="council-runtime-"))
    try:
        env = isolate_witness_env(build_witness_env(role, tree_dir, base_dir, candidate_dir), runtime_dir)
        return _execute_witness(witness, work_dir, env, make_outcome)
    finally:
        shutil.rmtree(runtime_dir, onexc=_handle_rmtree_error)


def _execute_witness(
    witness: Mapping[str, Any],
    work_dir: Path,
    env: Mapping[str, str],
    make_outcome: Callable[..., RunDetail],
) -> RunDetail:
    """Run a prepared witness's build and run commands and classify the outcome."""
    timeout_s: int = witness["timeout_s"]
    total_duration = 0.0

    build_argv = witness.get("build")
    if build_argv is not None:
        build_res = exec_process(
            build_argv,
            work_dir,
            env,
            timeout_s,
            max_file_bytes=WITNESS_MAX_FILE_BYTES,
        )
        total_duration += build_res.duration_s
        if build_res.timed_out:
            return make_outcome(
                "broken",
                None,
                total_duration,
                build_res.stdout,
                build_res.stderr,
                broken_reason=f"build timed out after {timeout_s}s",
            )
        if build_res.fsize_exceeded:
            return make_outcome(
                "broken",
                build_res.exit_code,
                total_duration,
                build_res.stdout,
                build_res.stderr,
                broken_reason=FSIZE_EXCEEDED_REASON,
            )
        if build_res.spawn_error is not None or build_res.exit_code != 0:
            detail = build_res.spawn_error or f"build exited with code {build_res.exit_code}"
            return make_outcome(
                "broken",
                build_res.exit_code,
                total_duration,
                build_res.stdout,
                build_res.stderr,
                broken_reason=detail,
            )

    run_res = exec_process(
        witness["run"],
        work_dir,
        env,
        timeout_s,
        max_file_bytes=WITNESS_MAX_FILE_BYTES,
    )
    total_duration += run_res.duration_s

    if run_res.timed_out:
        if witness["timeout_is_failure"]:
            return make_outcome("fail", None, total_duration, run_res.stdout, run_res.stderr)
        return make_outcome(
            "broken",
            None,
            total_duration,
            run_res.stdout,
            run_res.stderr,
            broken_reason=f"run timed out after {timeout_s}s",
        )

    if run_res.fsize_exceeded:
        return make_outcome(
            "broken",
            run_res.exit_code,
            total_duration,
            run_res.stdout,
            run_res.stderr,
            broken_reason=FSIZE_EXCEEDED_REASON,
        )

    broken_exits = set(witness["broken_exit"])
    if run_res.spawn_error is not None or run_res.exit_code in broken_exits:
        detail = run_res.spawn_error or f"run exited with broken_exit code {run_res.exit_code}"
        return make_outcome(
            "broken",
            run_res.exit_code,
            total_duration,
            run_res.stdout,
            run_res.stderr,
            broken_reason=detail,
        )

    expect = witness["expect"]
    expect_exit = expect.get("exit", 0)
    if expect_exit == "nonzero":
        exit_ok = run_res.exit_code is not None and run_res.exit_code != 0
    else:
        exit_ok = run_res.exit_code == expect_exit

    stdout_regex = expect.get("stdout_regex")
    stderr_regex = expect.get("stderr_regex")
    stdout_ok = stdout_regex is None or bool(re.search(stdout_regex, run_res.stdout, re.MULTILINE))
    stderr_ok = stderr_regex is None or bool(re.search(stderr_regex, run_res.stderr, re.MULTILINE))

    outcome = "pass" if (exit_ok and stdout_ok and stderr_ok) else "fail"
    return make_outcome(outcome, run_res.exit_code, total_duration, run_res.stdout, run_res.stderr)


def evaluate_entry(
    entry: dict[str, Any],
    citations: Sequence[dict[str, str]],
    *,
    validation_error: str | None,
    runs: int,
    base_dir: Path,
    candidate_dir: Path,
    base_snapshot: str,
    candidate_snapshot: str,
    base_ignored: frozenset[str] | None = None,
    candidate_ignored: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Validate citations, run witness on candidate (and base if needed), and set status."""
    if validation_error is not None:
        entry["status"] = "broken witness"
        entry["reason"] = validation_error
        return entry

    if citations:
        citation_err = check_citations(citations, candidate_dir)
        if citation_err is not None:
            entry["status"] = "broken witness"
            entry["reason"] = citation_err
            return entry

    if entry["type"] == "advisory":
        entry["status"] = "advisory"
        entry["reason"] = None
        return entry

    witness = entry["witness"]
    candidate_details = [
        run_witness_once(
            witness,
            role="candidate",
            tree_dir=candidate_dir,
            snapshot=candidate_snapshot,
            base_dir=base_dir,
            candidate_dir=candidate_dir,
            baseline_ignored=candidate_ignored,
        )
        for _ in range(runs)
    ]
    entry["runs"]["candidate"] = [d.record for d in candidate_details]
    cand_outcomes = [d.record["outcome"] for d in candidate_details]

    if any(outcome == "broken" for outcome in cand_outcomes):
        first_broken = next(d for d in candidate_details if d.record["outcome"] == "broken")
        entry["status"] = "broken witness"
        entry["reason"] = first_broken.broken_reason or "candidate witness run broken"
        return entry

    if all(outcome == "pass" for outcome in cand_outcomes):
        entry["status"] = "held" if entry["type"] == "held" else "not reproduced"
        entry["reason"] = None
        return entry

    if not all(outcome == "fail" for outcome in cand_outcomes):
        entry["status"] = "flaky"
        entry["reason"] = None
        return entry

    # Every candidate run failed; now check base tree.
    base_details = [
        run_witness_once(
            witness,
            role="base",
            tree_dir=base_dir,
            snapshot=base_snapshot,
            base_dir=base_dir,
            candidate_dir=candidate_dir,
            baseline_ignored=base_ignored,
        )
        for _ in range(runs)
    ]
    entry["runs"]["base"] = [d.record for d in base_details]
    base_outcomes = [d.record["outcome"] for d in base_details]

    # A held entry that fails turned out to be a real break; classify like a finding.
    if entry["type"] == "held" and entry["severity"] is None:
        entry["severity"] = "should"
        entry["effective_severity"] = "should"

    if all(outcome == "fail" for outcome in base_outcomes):
        entry["status"] = "pre-existing"
    else:
        entry["status"] = "introduced"
    entry["reason"] = None
    return entry


def load_waivers(path: Path, valid_ids: frozenset[str]) -> dict[str, dict[str, str]]:
    """Load and validate a waivers JSON file against known entry IDs."""
    data = load_json_file(path, "waivers file")
    if not isinstance(data, dict):
        raise InputError(f"waivers file must be a JSON object: {path}")

    waivers: dict[str, dict[str, str]] = {}
    for entry_id in sorted(data.keys()):
        if entry_id not in valid_ids:
            raise InputError(f"unknown entry id in waivers file: {entry_id}")
        raw = data[entry_id]
        if not isinstance(raw, dict):
            raise InputError(f"waiver for {entry_id} must be an object")
        unknown = sorted(set(raw.keys()) - WAIVER_KEYS)
        if unknown:
            raise InputError(f"unknown key(s) in waiver for {entry_id}: {', '.join(unknown)}")

        action = raw.get("action")
        if action not in VALID_WAIVER_ACTIONS:
            raise InputError(
                f"waiver for {entry_id} has invalid action {action!r}; "
                f"expected one of {sorted(VALID_WAIVER_ACTIONS)}"
            )
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InputError(f"waiver for {entry_id} requires a non-empty reason")

        if action == "severity":
            sev = raw.get("severity")
            if sev not in VALID_SEVERITIES:
                raise InputError(
                    f"severity waiver for {entry_id} requires severity in {sorted(VALID_SEVERITIES)}"
                )
            waivers[entry_id] = {"action": "severity", "severity": sev, "reason": reason}
        else:
            if "severity" in raw and raw["severity"] is not None:
                raise InputError(f"waive action for {entry_id} must not specify 'severity'")
            waivers[entry_id] = {"action": "waive", "reason": reason}
    return waivers


def is_blocking(entry: Mapping[str, Any]) -> bool:
    """Return True if an entry blocks the merge gate."""
    waiver = entry.get("waiver")
    waived = isinstance(waiver, Mapping) and waiver.get("action") == "waive"
    return (
        entry.get("status") == "introduced"
        and entry.get("effective_severity") in BLOCKING_SEVERITIES
        and not waived
    )


def entry_sort_key(entry: Mapping[str, Any]) -> tuple[int, str]:
    """Deterministic sort key: status priority, then namespaced entry ID."""
    status = entry["status"]
    if is_blocking(entry):
        rank = 0
    elif status == "introduced":
        rank = 1
    elif status == "flaky":
        rank = 2
    elif status == "broken witness":
        rank = 3
    elif status == "pre-existing":
        rank = 4
    elif status == "held":
        rank = 5
    elif status == "not reproduced":
        rank = 6
    else:
        rank = 7
    return (rank, str(entry["id"]))


def apply_waivers(
    entries: Sequence[dict[str, Any]],
    waivers: Mapping[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Recompute effective_severity and waiver fields from original severity."""
    updated: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        item["effective_severity"] = item["severity"]
        item["waiver"] = None
        waiver = waivers.get(item["id"])
        if waiver is not None:
            item["waiver"] = dict(waiver)
            if waiver["action"] == "severity":
                item["effective_severity"] = waiver["severity"]
        updated.append(item)
    updated.sort(key=entry_sort_key)
    return updated


def build_groups(ordered_entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group entries sharing a normalized cause; entries without a cause stay alone."""
    by_cause: dict[str, list[dict[str, Any]]] = {}
    raw_groups: list[tuple[str | None, list[dict[str, Any]]]] = []

    for entry in ordered_entries:
        cause = normalize_cause(entry.get("cause"))
        if cause is None:
            raw_groups.append((None, [entry]))
            continue
        if cause not in by_cause:
            bucket: list[dict[str, Any]] = []
            by_cause[cause] = bucket
            raw_groups.append((cause, bucket))
        by_cause[cause].append(entry)

    groups: list[dict[str, Any]] = []
    for cause, members in raw_groups:
        sorted_members = sorted(members, key=entry_sort_key)
        groups.append(
            {
                "cause": cause,
                "ids": [m["id"] for m in sorted_members],
                "seats": sorted({m["seat"] for m in sorted_members}),
                "statuses": [m["status"] for m in sorted_members],
            }
        )
    return groups


def compute_gate(
    ordered_entries: Sequence[dict[str, Any]],
    seats: Sequence[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Compute the overall gate ('pass', 'fail', 'unverified') and human-readable reasons."""
    blocking = [e for e in ordered_entries if is_blocking(e)]
    if blocking:
        reasons = [
            f"blocking introduced finding {e['id']} ({e['effective_severity']})"
            for e in blocking
        ]
        return "fail", reasons

    unverified_reasons: list[str] = []
    for entry in ordered_entries:
        if entry["status"] == "flaky":
            unverified_reasons.append(f"flaky witness: {entry['id']}")
        elif entry["status"] == "broken witness":
            detail = entry.get("reason") or "invalid or broken witness"
            unverified_reasons.append(f"broken witness: {entry['id']} ({detail})")

    for seat in seats:
        if seat["required"] and seat["status"] != "completed":
            unverified_reasons.append(
                f"required seat {seat['name']} has status '{seat['status']}'"
            )

    if unverified_reasons:
        return "unverified", unverified_reasons

    return "pass", ["no blocking introduced findings; all witnesses and required seats verified"]


def gate_exit_code(gate: str) -> int:
    """Map gate verdict string to its CLI exit code."""
    if gate == "pass":
        return EXIT_PASS
    if gate == "fail":
        return EXIT_FAIL
    if gate == "unverified":
        return EXIT_UNVERIFIED
    raise InputError(f"unexpected gate value: {gate!r}")


def _format_role_summary(role: str, run_records: Sequence[Mapping[str, Any]]) -> str:
    """Format a compact outcome summary for one tree role."""
    total = len(run_records)
    if total == 0:
        return ""
    counts: dict[str, int] = {}
    for rec in run_records:
        outcome = str(rec["outcome"])
        counts[outcome] = counts.get(outcome, 0) + 1

    if len(counts) == 1:
        only_outcome = next(iter(counts))
        summary = f"{role} {only_outcome} {total}/{total}"
        if only_outcome in ("fail", "broken"):
            exits: list[str] = []
            for rec in run_records:
                code = rec.get("exit")
                label = "timeout" if code is None else f"exit {code}"
                if label not in exits:
                    exits.append(label)
            if exits:
                summary += f" ({', '.join(exits)})"
        return summary

    ordered_outcomes = ("pass", "fail", "broken", "n/a")
    parts = [f"{name} {counts[name]}/{total}" for name in ordered_outcomes if name in counts]
    return f"{role} {', '.join(parts)}"


def format_outcome_summary(entry: Mapping[str, Any]) -> str:
    """Format candidate and base run outcome counts for an entry."""
    runs_obj = entry.get("runs") or {}
    cand = runs_obj.get("candidate") or []
    base = runs_obj.get("base") or []
    if not cand and not base:
        if entry.get("status") == "broken witness":
            reason = entry.get("reason") or "invalid entry"
            return f"not run ({reason})"
        return "not run"
    parts = [p for p in (_format_role_summary("candidate", cand), _format_role_summary("base", base)) if p]
    return "; ".join(parts)


def _fence_block(text: str, lang: str = "text") -> list[str]:
    """Wrap text in a Markdown code fence that cannot collide with backticks inside text."""
    longest_run = 0
    for match in re.finditer(r"`+", text):
        longest_run = max(longest_run, len(match.group(0)))
    fence = "`" * max(3, longest_run + 1)
    content = text.rstrip("\n")
    return [f"{fence}{lang}", content, fence]


def _escape_table_cell(value: str) -> str:
    """Escape pipe characters and newlines for Markdown table cells."""
    return value.replace("|", "\\|").replace("\n", " ")


def _format_severity_label(entry: Mapping[str, Any]) -> str:
    """Format severity, including any override or waiver reason."""
    sev = entry.get("severity")
    eff = entry.get("effective_severity")
    waiver = entry.get("waiver")
    base_label = f"`{eff}`" if eff else "`none`"

    if isinstance(waiver, Mapping):
        action = waiver.get("action")
        reason = waiver.get("reason", "")
        if action == "severity":
            orig = f"`{sev}`" if sev else "`none`"
            return f"{base_label} (overridden from {orig}: {reason})"
        if action == "waive":
            return f"{base_label} (waived: {reason})"
    return base_label


def _render_entry_details(
    entry: Mapping[str, Any],
    *,
    include_identity: bool = True,
) -> list[str]:
    """Render detailed Markdown lines for a single finding/unverified/pre-existing/held entry."""
    lines: list[str] = []
    if include_identity:
        lines.append(f"- **IDs:** `{entry['id']}`")
        lines.append(f"- **Seats:** `{entry['seat']}`")
    lines.append(f"- **Severity:** {_format_severity_label(entry)}")
    lines.append(f"- **Claim:** {entry['claim']}")
    if entry.get("cause"):
        lines.append(f"- **Cause:** `{entry['cause']}`")
    if entry.get("status") == "broken witness" and entry.get("reason"):
        lines.append(f"- **Reason:** {entry['reason']}")

    intent = entry.get("intent")
    if isinstance(intent, Mapping) and intent.get("file") and intent.get("quote"):
        lines.append(f"- **Intent:** `{intent['file']}` — \"{intent['quote']}\"")

    witness = entry.get("witness")
    if isinstance(witness, Mapping) and isinstance(witness.get("run"), list):
        run_argv = [str(arg) for arg in witness["run"]]
        cwd = witness.get("cwd", ".")
        lines.append(f"- **run:** `{shlex.join(run_argv)}` (cwd: `{cwd}`)")

    lines.append(f"- **Outcome:** {format_outcome_summary(entry)}")

    cand_runs = (entry.get("runs") or {}).get("candidate") or []
    first_interesting = next(
        (r for r in cand_runs if r.get("outcome") == "fail"),
        None,
    )
    if first_interesting is None:
        first_interesting = next(
            (r for r in cand_runs if r.get("outcome") == "broken"),
            None,
        )
    if first_interesting is not None:
        outcome_label = (
            "failing" if first_interesting.get("outcome") == "fail" else "broken"
        )
        stdout_tail = first_interesting.get("stdout_tail") or ""
        stderr_tail = first_interesting.get("stderr_tail") or ""
        if stdout_tail:
            lines.append("")
            lines.append(f"**stdout tail (first {outcome_label} candidate run):**")
            lines.extend(_fence_block(stdout_tail))
        if stderr_tail:
            lines.append("")
            lines.append(f"**stderr tail (first {outcome_label} candidate run):**")
            lines.extend(_fence_block(stderr_tail))
    return lines


def _render_grouped_section(
    entries: Sequence[dict[str, Any]],
    groups_by_cause: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Render a section's entries, collapsing entries that share a normalized cause."""
    if not entries:
        return ["_None._", ""]

    buckets: list[tuple[str | None, list[dict[str, Any]]]] = []
    seen_causes: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        cause = normalize_cause(entry.get("cause"))
        if cause is None:
            buckets.append((None, [entry]))
            continue
        if cause not in seen_causes:
            bucket: list[dict[str, Any]] = []
            seen_causes[cause] = bucket
            buckets.append((cause, bucket))
        seen_causes[cause].append(entry)

    lines: list[str] = []
    for cause, members in buckets:
        ids_display = ", ".join(f"`{m['id']}`" for m in members)
        seats_display = ", ".join(f"`{s}`" for s in sorted({m["seat"] for m in members}))
        heading_ids = ", ".join(m["id"] for m in members)
        lines.append(f"### {heading_ids}")
        lines.append("")
        if len(members) == 1:
            entry = members[0]
            lines.extend(_render_entry_details(entry, include_identity=True))
            if cause and cause in groups_by_cause:
                group = groups_by_cause[cause]
                others = [
                    f"`{gid}` ({gstatus})"
                    for gid, gstatus in zip(group["ids"], group["statuses"], strict=True)
                    if gid != entry["id"]
                ]
                if others:
                    lines.append(f"- **Related (same cause):** {', '.join(others)}")
        else:
            lines.append(f"- **IDs:** {ids_display}")
            lines.append(f"- **Seats:** {seats_display}")
            lines.append(f"- **Cause:** `{cause}`")
            if cause and cause in groups_by_cause:
                group = groups_by_cause[cause]
                member_ids = {m["id"] for m in members}
                others = [
                    f"`{gid}` ({gstatus})"
                    for gid, gstatus in zip(group["ids"], group["statuses"], strict=True)
                    if gid not in member_ids
                ]
                if others:
                    lines.append(f"- **Related (same cause):** {', '.join(others)}")
            for member in members:
                lines.append("")
                lines.append(f"#### {member['id']} ({member['seat']})")
                lines.append("")
                lines.extend(_render_entry_details(member, include_identity=False))
        lines.append("")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render the complete council report as deterministic Markdown."""
    gate_upper = str(report["gate"]).upper()
    lines: list[str] = [f"# Council gate: {gate_upper}", ""]

    gate_reasons = report.get("gate_reasons") or []
    for reason in gate_reasons:
        lines.append(f"- {reason}")
    if gate_reasons:
        lines.append("")

    # Coverage table.
    lines.extend(
        [
            "## Coverage",
            "",
            "| Seat | Required | Status | Provider / Model |",
            "| --- | --- | --- | --- |",
        ]
    )
    for seat in report.get("seats") or []:
        req_str = "yes" if seat["required"] else "no"
        status_str = seat["status"]
        if seat.get("note"):
            status_str = f"{status_str} ({seat['note']})"
        prov_model = f"{seat['provider']} / {seat['model']}"
        lines.append(
            f"| {_escape_table_cell(seat['name'])} "
            f"| {req_str} "
            f"| {_escape_table_cell(status_str)} "
            f"| {_escape_table_cell(prov_model)} |"
        )
    lines.append("")

    entries: list[dict[str, Any]] = list(report.get("entries") or [])
    groups_by_cause: dict[str, Mapping[str, Any]] = {
        g["cause"]: g for g in (report.get("groups") or []) if g.get("cause")
    }

    blocking = [e for e in entries if is_blocking(e)]
    non_blocking_unwaived_introduced = [
        e
        for e in entries
        if e["status"] == "introduced" and not is_blocking(e) and e.get("waiver") is None
    ]
    waived_or_overridden = [e for e in entries if e.get("waiver") is not None]
    unverified = [e for e in entries if e["status"] in ("flaky", "broken witness")]
    pre_existing = [e for e in entries if e["status"] == "pre-existing"]
    held = [e for e in entries if e["status"] == "held"]
    not_reproduced = [e for e in entries if e["status"] == "not reproduced"]
    advisory = [e for e in entries if e["status"] == "advisory"]

    lines.extend(["## Blocking", ""])
    lines.extend(_render_grouped_section(blocking, groups_by_cause))

    if non_blocking_unwaived_introduced:
        lines.extend(["## Non-blocking introduced", ""])
        lines.extend(_render_grouped_section(non_blocking_unwaived_introduced, groups_by_cause))

    lines.extend(["## Waived or overridden", ""])
    lines.extend(_render_grouped_section(waived_or_overridden, groups_by_cause))

    lines.extend(["## Unverified", ""])
    lines.extend(_render_grouped_section(unverified, groups_by_cause))

    lines.extend(["## Pre-existing", ""])
    lines.extend(_render_grouped_section(pre_existing, groups_by_cause))

    lines.extend(["## Held", ""])
    lines.extend(_render_grouped_section(held, groups_by_cause))

    lines.extend(["## Not reproduced", ""])
    if not_reproduced:
        for entry in not_reproduced:
            lines.append(f"- `{entry['id']}`")
        lines.append("")
    else:
        lines.extend(["_None._", ""])

    lines.extend(["## Advisory", ""])
    if advisory:
        by_seat: dict[str, list[dict[str, Any]]] = {}
        for entry in advisory:
            by_seat.setdefault(entry["seat"], []).append(entry)
        for seat_name in sorted(by_seat.keys()):
            lines.append(f"### {seat_name}")
            lines.append("")
            for entry in by_seat[seat_name]:
                cause_suffix = f" (`{entry['cause']}`)" if entry.get("cause") else ""
                lines.append(f"- `{entry['id']}`: {entry['claim']}{cause_suffix}")
            lines.append("")
    else:
        lines.extend(["_None._", ""])

    return "\n".join(lines).rstrip() + "\n"


def finalize_report(
    *,
    candidate: dict[str, Any],
    runs: int,
    seats: list[dict[str, Any]],
    entries: Sequence[dict[str, Any]],
    waivers: Mapping[str, dict[str, str]],
) -> dict[str, Any]:
    """Apply waivers, sort entries, group causes, and compute the gate."""
    ordered_entries = apply_waivers(entries, waivers)
    groups = build_groups(ordered_entries)
    gate, gate_reasons = compute_gate(ordered_entries, seats)
    return {
        "version": SCHEMA_VERSION,
        "candidate": candidate,
        "runs": runs,
        "gate": gate,
        "gate_reasons": gate_reasons,
        "seats": seats,
        "entries": ordered_entries,
        "groups": groups,
    }


def _write_text_output(path: Path, content: str) -> None:
    """Write content to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def cmd_materialize(args: argparse.Namespace) -> int:
    """Handle the 'materialize' subcommand."""
    trees_data = materialize(Path(args.repo), args.base, Path(args.out))
    print(json.dumps(trees_data, indent=2))
    return EXIT_PASS


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Handle the 'cleanup' subcommand."""
    cleanup(Path(args.out))
    return EXIT_PASS


def cmd_run(args: argparse.Namespace) -> int:
    """Handle the 'run' subcommand, holding the trees lock throughout."""
    trees_dir = Path(args.trees)
    load_trees(trees_dir)
    with trees_lock(trees_dir):
        return _cmd_run_locked(args)


def _cmd_run_locked(args: argparse.Namespace) -> int:
    """Run every witness and write the reports; the caller holds the trees lock."""
    if args.runs < 1:
        raise InputError(f"--runs must be >= 1, got {args.runs}")

    trees_data, base_dir, candidate_dir = load_trees(Path(args.trees))
    seats = load_seats(Path(args.seats))
    valid_seat_names = frozenset(seat["name"] for seat in seats)
    raw_entries = load_entries_files([Path(p) for p in args.entries], valid_seat_names)

    valid_entry_ids = frozenset(ns_id for _, ns_id, _ in raw_entries)
    waivers: dict[str, dict[str, str]] = {}
    if args.waivers is not None:
        waivers = load_waivers(Path(args.waivers), valid_entry_ids)

    base_snapshot: str = trees_data["base_snapshot"]
    candidate_snapshot: str = trees_data["candidate_snapshot"]

    # Ensure clean snapshot state before running setup commands or checking citations.
    reset_tree(base_dir, base_snapshot)
    reset_tree(candidate_dir, candidate_snapshot)
    run_setup_commands(args.setup, base_dir, candidate_dir)
    base_ignored = list_ignored_paths(base_dir)
    candidate_ignored = list_ignored_paths(candidate_dir)

    # Validate entries and check citations against the pristine candidate snapshot
    # before any witness executes and modifies files in the worktree.
    prepared: list[tuple[dict[str, Any], str | None]] = []
    for seat, namespaced_id, raw_entry in raw_entries:
        entry, citations, validation_err = validate_entry(seat, namespaced_id, raw_entry)
        pre_err = validation_err
        if pre_err is None and citations:
            pre_err = check_citations(citations, candidate_dir)
        prepared.append((entry, pre_err))

    evaluated: list[dict[str, Any]] = []
    try:
        for entry, pre_err in prepared:
            result = evaluate_entry(
                entry,
                (),
                validation_error=pre_err,
                runs=args.runs,
                base_dir=base_dir,
                candidate_dir=candidate_dir,
                base_snapshot=base_snapshot,
                candidate_snapshot=candidate_snapshot,
                base_ignored=base_ignored,
                candidate_ignored=candidate_ignored,
            )
            evaluated.append(result)
    finally:
        reset_tree(base_dir, base_snapshot, baseline_ignored=base_ignored)
        reset_tree(candidate_dir, candidate_snapshot, baseline_ignored=candidate_ignored)

    report = finalize_report(
        candidate=trees_data,
        runs=args.runs,
        seats=seats,
        entries=evaluated,
        waivers=waivers,
    )
    markdown = render_markdown(report)

    if args.report_json is not None:
        _write_text_output(Path(args.report_json), json.dumps(report, indent=2) + "\n")
    if args.report_md is not None:
        _write_text_output(Path(args.report_md), markdown)
    else:
        sys.stdout.write(markdown)

    return gate_exit_code(report["gate"])


def validate_entries_file(path: Path, candidate_dir: Path | None) -> list[str]:
    """Return every problem ``run`` would hit in one seat's entries file, without running.

    The seat name must match the file name (``<seat>.json``), so a misfiled object
    is caught before the run. Citations are checked only when ``candidate_dir`` is given.
    """
    try:
        loaded = load_entries_files([path], None)
    except InputError as exc:
        return [str(exc)]
    seat = load_json_file(path, "entries file")["seat"]
    problems = [] if seat == path.stem else [f"seat {seat!r} does not match the file name {path.name!r}"]
    for seat, namespaced_id, raw_entry in loaded:
        _, citations, error = validate_entry(seat, namespaced_id, raw_entry)
        if error is None and citations and candidate_dir is not None:
            error = check_citations(citations, candidate_dir)
        if error is not None:
            problems.append(f"{namespaced_id}: {error}")
    return problems


def _validate_all(raw_paths: Sequence[str], candidate_dir: Path | None) -> list[str]:
    """Return ``<file>: <problem>`` lines for every entries file."""
    return [
        f"{raw_path}: {problem}"
        for raw_path in raw_paths
        for problem in validate_entries_file(Path(raw_path), candidate_dir)
    ]


def cmd_validate(args: argparse.Namespace) -> int:
    """Check seat entries files and print each problem; exit 3 when there is any."""
    if not args.trees:
        problems = _validate_all(args.entries, None)
    else:
        trees_dir = Path(args.trees)
        trees_data, _, candidate_dir = load_trees(trees_dir)
        with trees_lock(trees_dir):
            reset_tree(candidate_dir, trees_data["candidate_snapshot"])
            problems = _validate_all(args.entries, candidate_dir)
    for problem in problems:
        print(problem)
    if problems:
        return EXIT_ERROR
    print("entries ok")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """Handle the 'gate' subcommand."""
    report_path = Path(args.report_json)
    existing = load_json_file(report_path, "report JSON")
    if not isinstance(existing, dict) or existing.get("version") != SCHEMA_VERSION:
        raise InputError(f"invalid or unsupported report JSON: {report_path}")

    candidate = existing.get("candidate")
    runs = existing.get("runs")
    seats = existing.get("seats")
    entries = existing.get("entries")

    if not isinstance(candidate, dict):
        raise InputError("report JSON missing 'candidate' object")
    if not is_exact_int(runs) or runs < 1:
        raise InputError("report JSON missing valid 'runs' integer")
    if not isinstance(seats, list) or not all(isinstance(s, dict) for s in seats):
        raise InputError("report JSON missing valid 'seats' list")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise InputError("report JSON missing valid 'entries' list")

    for idx, seat in enumerate(seats):
        if (
            not isinstance(seat.get("name"), str)
            or not isinstance(seat.get("required"), bool)
            or seat.get("status") not in VALID_SEAT_STATUSES
            or not isinstance(seat.get("provider"), str)
            or not isinstance(seat.get("model"), str)
        ):
            raise InputError(f"report JSON seats[{idx}] has invalid schema")

    valid_ids: set[str] = set()
    for idx, entry in enumerate(entries):
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            raise InputError(f"report JSON entries[{idx}] missing valid 'id'")
        if not isinstance(entry.get("seat"), str) or not isinstance(entry.get("status"), str):
            raise InputError(f"report JSON entries[{idx}] missing 'seat' or 'status'")
        if "severity" not in entry:
            raise InputError(f"report JSON entries[{idx}] missing 'severity'")
        if eid in valid_ids:
            raise InputError(f"duplicate entry id in report JSON: {eid}")
        valid_ids.add(eid)

    waivers = load_waivers(Path(args.waivers), frozenset(valid_ids))
    updated = finalize_report(
        candidate=candidate,
        runs=runs,
        seats=seats,
        entries=entries,
        waivers=waivers,
    )
    markdown = render_markdown(updated)

    if args.report_json_out is not None:
        _write_text_output(Path(args.report_json_out), json.dumps(updated, indent=2) + "\n")
    if args.report_md is not None:
        _write_text_output(Path(args.report_md), markdown)
    else:
        sys.stdout.write(markdown)

    return gate_exit_code(updated["gate"])


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for witness.py."""
    parser = CouncilArgumentParser(
        prog="witness.py",
        description="Deterministic witness runner for the adversarial council skill.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_mat = sub.add_parser("materialize", help="Create detached base and candidate worktrees.")
    p_mat.add_argument("--repo", required=True, help="Path inside the source git repository.")
    p_mat.add_argument("--base", required=True, help="Base git ref for merge-base.")
    p_mat.add_argument("--out", required=True, help="Output directory outside the repo.")
    p_mat.set_defaults(func=cmd_materialize)

    p_clean = sub.add_parser("cleanup", help="Remove materialized worktrees and trees.json.")
    p_clean.add_argument("--out", required=True, help="Directory previously passed to materialize.")
    p_clean.set_defaults(func=cmd_cleanup)

    p_run = sub.add_parser("run", help="Run seat witnesses against materialized trees.")
    p_run.add_argument("--trees", required=True, help="Directory created by materialize.")
    p_run.add_argument("--seats", required=True, help="Path to seats JSON file.")
    p_run.add_argument(
        "--entries",
        action="extend",
        nargs="+",
        required=True,
        help="Path(s) to per-seat entries JSON file(s). Repeatable.",
    )
    p_run.add_argument("--waivers", help="Optional path to waivers JSON file.")
    p_run.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Runs per tree (default 3).")
    p_run.add_argument(
        "--setup",
        action="append",
        default=[],
        help="Shell command to run once in each tree before witnesses. Repeatable.",
    )
    p_run.add_argument("--report-json", help="Path to write the JSON report.")
    p_run.add_argument("--report-md", help="Path to write the Markdown report.")
    p_run.set_defaults(func=cmd_run)

    p_gate = sub.add_parser("gate", help="Recompute gate from an existing report JSON + waivers.")
    p_gate.add_argument("--report-json", required=True, help="Path to existing report JSON.")
    p_gate.add_argument("--waivers", required=True, help="Path to waivers JSON file.")
    p_gate.add_argument("--report-json-out", help="Path to write the updated JSON report.")
    p_gate.add_argument("--report-md", help="Path to write the updated Markdown report.")
    p_gate.set_defaults(func=cmd_gate)

    p_val = sub.add_parser("validate", help="Check seat entries files without running anything.")
    p_val.add_argument("--entries", required=True, action="extend", nargs="+", help="Entries JSON file(s).")
    p_val.add_argument("--trees", help="Directory created by materialize; enables citation checks.")
    p_val.set_defaults(func=cmd_validate)

    return parser


_TERMINATION_SIGNALS: tuple[int, ...] = (signal.SIGTERM, signal.SIGHUP)


def _handle_termination_signal(signum: int, _frame: Any) -> None:
    """Ignore further termination signals and raise so finally blocks can clean up."""
    for sig in _TERMINATION_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    raise TerminatedBySignal(signum)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint.

    A termination signal can land while ``_main`` is already inside an ``except`` or
    ``finally`` body, where its own handlers no longer apply. The first signal makes
    the handler ignore further ones, so this outer layer cannot be interrupted again.
    """
    try:
        return _main(argv)
    except TerminatedBySignal as exc:
        _cleanup_empty_hooks_dir()
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _main(argv: Sequence[str] | None) -> int:
    """Install termination handlers, dispatch the subcommand, and map errors to exit 3."""
    prev_handlers: dict[int, Any] = {}
    for sig in _TERMINATION_SIGNALS:
        current = signal.getsignal(sig)
        # Respect an inherited ignore, such as nohup's for SIGHUP.
        if current is signal.SIG_IGN:
            continue
        try:
            prev_handlers[sig] = signal.signal(sig, _handle_termination_signal)
        except ValueError:
            break
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        return int(args.func(args))
    except TerminatedBySignal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR
    finally:
        _cleanup_empty_hooks_dir()
        for sig, handler in prev_handlers.items():
            signal.signal(sig, handler)

if __name__ == "__main__":
    sys.exit(main())
