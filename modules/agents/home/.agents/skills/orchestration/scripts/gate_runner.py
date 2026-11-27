#!/usr/bin/env python3
"""Zero-token deterministic runner for slow build, test, and coverage gates.

Runs a shell gate command in a worktree without keeping an LLM context alive
across the 5-minute prompt-cache TTL. Captures the full log to disk, kills the
entire process group on timeout or cancellation, and prints a compact JSON
summary with only the failure tail so a fresh fix agent can be spawned cleanly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 1800
DEFAULT_TAIL_LINES = 120
DEFAULT_MAX_TAIL_CHARS = 16_000
DEFAULT_KILL_GRACE_S = 5.0
TRUNCATION_PREFIX = "... [truncated]\n"


def _git_head(workdir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            head = proc.stdout.strip()
            return head or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _default_log_path(workdir: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", workdir.name or "gate").strip("-") or "gate"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    fd, path = tempfile.mkstemp(prefix=f"orch-gate-{slug}-{stamp}-", suffix=".log")
    os.close(fd)
    return Path(path)


def _read_tail(log_path: Path, tail_lines: int, max_chars: int) -> str:
    if tail_lines <= 0 or max_chars <= 0:
        return ""
    try:
        size = log_path.stat().st_size
        if size <= 0:
            return ""
        window = min(size, max(max_chars * 4, 262_144))
        with log_path.open("rb") as handle:
            if size > window:
                handle.seek(size - window)
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    excerpt = "\n".join(lines[-tail_lines:])
    if len(excerpt) > max_chars:
        if max_chars <= len(TRUNCATION_PREFIX):
            return excerpt[-max_chars:]
        excerpt = TRUNCATION_PREFIX + excerpt[-(max_chars - len(TRUNCATION_PREFIX)):]
    return excerpt


def _leader_exited_unreaped(pid: int) -> bool:
    """Return True if `pid` has exited, leaving it un-reaped so its PGID cannot be reused."""
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        return info is not None
    except ChildProcessError:
        return True


def _terminate_group(proc: subprocess.Popen, grace_s: float = DEFAULT_KILL_GRACE_S) -> None:
    """Terminate `proc` and every descendant in its process group, then reap `proc`."""
    pgid = proc.pid
    try:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            proc.terminate()

        # Keep `proc` un-reaped during the grace window so its PGID cannot be recycled.
        deadline = time.monotonic() + max(0.0, grace_s)
        leader_done_at: float | None = None
        while time.monotonic() < deadline:
            if _leader_exited_unreaped(pgid):
                if leader_done_at is None:
                    leader_done_at = time.monotonic()
                # Brief post-leader grace so well-behaved grandchildren finish SIGTERM.
                if (time.monotonic() - leader_done_at) >= min(0.25, grace_s):
                    break
            time.sleep(0.02)

        # Escalate to SIGKILL for the entire group so stubborn grandchildren never leak.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            proc.kill()
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_gate(
    workdir: Path,
    command: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    log_path: Path | None = None,
    handoff_path: Path | None = None,
    tail_lines: int = DEFAULT_TAIL_LINES,
    max_tail_chars: int = DEFAULT_MAX_TAIL_CHARS,
    kill_grace_s: float = DEFAULT_KILL_GRACE_S,
) -> tuple[int, dict]:
    if not workdir.is_dir():
        return 2, {"status": "error", "error": f"workdir does not exist: {workdir}"}
    if not command.strip():
        return 2, {"status": "error", "error": "empty gate command"}
    if timeout_s <= 0:
        return 2, {"status": "error", "error": "timeout must be > 0"}

    resolved_dir = workdir.resolve()
    target_log = log_path if log_path is not None else _default_log_path(resolved_dir)
    target_log.parent.mkdir(parents=True, exist_ok=True)
    head = _git_head(resolved_dir)
    started = time.monotonic()

    timed_out = False
    exit_code = 1
    prev_sigterm = None
    signal_installed = False

    def _on_sigterm(_signum: int, _frame: object) -> None:
        # Ignore follow-up SIGTERM while tearing down the child process group.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise SystemExit(143)

    with target_log.open("wb") as log_handle:
        proc = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=str(resolved_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            prev_sigterm = signal.signal(signal.SIGTERM, _on_sigterm)
            signal_installed = True
        except ValueError:
            # Not in main thread; cancellation cleanup still runs on KeyboardInterrupt/exceptions.
            pass
        try:
            exit_code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(proc, grace_s=kill_grace_s)
            exit_code = 124
        except BaseException:
            _terminate_group(proc, grace_s=kill_grace_s)
            raise
        finally:
            if signal_installed:
                signal.signal(signal.SIGTERM, prev_sigterm)

    duration_s = round(time.monotonic() - started, 2)
    status = "timeout" if timed_out else ("pass" if exit_code == 0 else "fail")
    result: dict[str, object] = {
        "status": status,
        "exit_code": exit_code,
        "duration_s": duration_s,
        "workdir": str(resolved_dir),
        "head": head,
        "log_path": str(target_log),
    }
    if handoff_path is not None:
        result["handoff_path"] = str(handoff_path)
        result["handoff_exists"] = handoff_path.is_file()
    if status != "pass":
        result["tail"] = _read_tail(target_log, tail_lines, max_tail_chars)
    return exit_code, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="Worktree or repository directory")
    parser.add_argument("--cmd", required=True, help="Shell command to execute under bash -lc")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Timeout in seconds (default 1800)")
    parser.add_argument("--log-path", type=Path, help="Optional path for combined stdout/stderr log")
    parser.add_argument("--handoff", type=Path, help="Optional handoff brief path to echo in output")
    parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES, help="Max failure tail lines")
    parser.add_argument("--max-tail-chars", type=int, default=DEFAULT_MAX_TAIL_CHARS, help="Max failure tail chars")
    args = parser.parse_args(argv)

    code, summary = run_gate(
        args.workdir,
        args.cmd,
        timeout_s=args.timeout,
        log_path=args.log_path,
        handoff_path=args.handoff,
        tail_lines=args.tail_lines,
        max_tail_chars=args.max_tail_chars,
    )
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
