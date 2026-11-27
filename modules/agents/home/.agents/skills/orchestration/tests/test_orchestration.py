import http.server
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HOOKS = Path(__file__).resolve().parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HOOKS))

import gate_runner  # noqa: E402
import model_guard  # noqa: E402
import resource_watch  # noqa: E402


class GateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="orch-gate-test-")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _wait_pid_dead(self, pid: int, timeout_s: float = 2.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"Process {pid} was still alive after {timeout_s}s")

    def test_passing_gate_omits_tail_and_records_git_head(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "a.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "add", "a.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init"],
            check=True,
        )
        handoff = self.root / "handoff.md"
        handoff.write_text("# Handoff\n", encoding="utf-8")
        log = self.root / "gate.log"
        code, summary = gate_runner.run_gate(
            self.root,
            "echo line1 && echo line2",
            log_path=log,
            handoff_path=handoff,
        )
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(len(summary["head"]), 40)
        self.assertTrue(summary["handoff_exists"])
        self.assertNotIn("tail", summary)
        self.assertIn("line2", log.read_text(encoding="utf-8"))

    def test_failing_gate_captures_bounded_tail_including_small_max_chars(self):
        log = self.root / "fail.log"
        code, summary = gate_runner.run_gate(
            self.root,
            "for i in $(seq 1 20); do echo line-$i; done; echo ERR_MARKER >&2; exit 3",
            log_path=log,
            tail_lines=5,
            max_tail_chars=80,
        )
        self.assertEqual(code, 3)
        self.assertEqual(summary["status"], "fail")
        self.assertIn("ERR_MARKER", summary["tail"])
        self.assertLessEqual(len(summary["tail"]), 80)

        # Edge cases: max_chars <= len(TRUNCATION_PREFIX) must never slice negatively or exceed max_chars.
        for limit in (16, 10, 4, 1):
            clipped = gate_runner._read_tail(log, tail_lines=10, max_chars=limit)
            self.assertLessEqual(len(clipped), limit, limit)
            self.assertTrue(clipped.endswith("ERR_MARKER"[-limit:]), (limit, clipped))

    def test_timeout_kills_sigterm_ignoring_grandchild_even_when_shell_exits_fast(self):
        pid_file = self.root / "grandchild.pid"
        log = self.root / "timeout.log"
        # Grandchild ignores SIGTERM; parent bash exits immediately on SIGTERM.
        cmd = (
            f"python3 -I -B -c \"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)\" & "
            f"echo $! > {pid_file}; trap 'exit 0' TERM; while :; do sleep 0.05; done"
        )
        code, summary = gate_runner.run_gate(
            self.root,
            cmd,
            timeout_s=0.4,
            kill_grace_s=0.3,
            log_path=log,
        )
        self.assertEqual(code, 124)
        self.assertEqual(summary["status"], "timeout")
        self.assertTrue(pid_file.is_file())
        grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
        self._wait_pid_dead(grandchild_pid)

    def test_cli_entrypoint_and_sigterm_propagation_to_child_group(self):
        # Subprocess CLI happy path.
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPTS / "gate_runner.py"),
             "--workdir", str(self.root), "--cmd", "echo cli-ok"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "pass")

        # Subprocess CLI cancellation via SIGTERM must also kill detached child process group.
        pid_file = self.root / "runner-child.pid"
        with subprocess.Popen(
            [sys.executable, "-I", "-B", str(SCRIPTS / "gate_runner.py"),
             "--workdir", str(self.root),
             "--cmd", f"sleep 30 & echo $! > {pid_file}; wait"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as runner:
            deadline = time.monotonic() + 5.0
            while not pid_file.is_file() or not pid_file.read_text(encoding="utf-8").strip():
                if time.monotonic() >= deadline:
                    runner.kill()
                    self.fail("Child PID file was never written")
                time.sleep(0.05)
            child_pid = int(pid_file.read_text(encoding="utf-8").strip())
            runner.send_signal(signal.SIGTERM)
            runner.wait(timeout=10)
            self.assertNotEqual(runner.returncode, 0)
        self._wait_pid_dead(child_pid)

    def test_missing_workdir_returns_error(self):
        code, summary = gate_runner.run_gate(self.root / "missing", "echo hi")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "error")


class ResourceWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="orch-watch-test-")
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _seed_statev2(
        self,
        rows: list[tuple[str, str, str | None, str | None, str, str, str]],
        *,
        extra_runs: list[tuple[str, int, str, str, str | None]] = (),
        archived_ids: set[str] = frozenset(),
    ) -> None:
        db = self.home / ".t3/userdata/statev2.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE orchestration_v2_projection_threads "
            "(thread_id TEXT PRIMARY KEY, title TEXT, updated_at TEXT, archived_at TEXT, deleted_at TEXT, payload_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE orchestration_v2_projection_runs "
            "(run_id TEXT PRIMARY KEY, thread_id TEXT, ordinal INTEGER, status TEXT, requested_at TEXT, completed_at TEXT)"
        )
        for idx, (tid, title, wt, parent_id, status, updated_at, requested_at) in enumerate(rows):
            payload: dict[str, object] = {"lineage": {"relationshipToParent": "subagent", "parentThreadId": parent_id}}
            if wt is not None:
                payload["worktreePath"] = wt
            archived_at = "2026-11-26T00:00:00Z" if tid in archived_ids else None
            completed_at = updated_at if status not in ("running", "starting", "preparing", "queued") else None
            conn.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES (?, ?, ?, ?, ?, ?)",
                (tid, title, updated_at, archived_at, None, json.dumps(payload)),
            )
            conn.execute(
                "INSERT INTO orchestration_v2_projection_runs VALUES (?, ?, ?, ?, ?, ?)",
                (f"run-{idx}", tid, 1, status, requested_at, completed_at),
            )
        for ridx, (tid, ordinal, status, req_at, comp_at) in enumerate(extra_runs):
            conn.execute(
                "INSERT INTO orchestration_v2_projection_runs VALUES (?, ?, ?, ?, ?, ?)",
                (f"extra-{ridx}", tid, ordinal, status, req_at, comp_at),
            )
        conn.commit()
        conn.close()

    def test_active_delegates_classifies_builders_reviews_research_and_ordinal_order(self):
        self._seed_statev2(
            [
                ("thread:delegated-task:w1", "Implement #10", "/tmp/w1", "parent-a", "running", "2026-11-27T00:00:00Z", "2026-11-27T00:00:00Z"),
                ("thread:delegated-task:fix-preview", "Implement #11 preview", None, "parent-a", "running", "2026-11-27T00:25:00Z", "2026-11-27T00:20:00Z"),
                ("thread:delegated-task:review-10-r1", "Review #10 R1", None, "parent-a", "running", "2026-11-27T00:28:00Z", "2026-11-27T00:28:00Z"),
                ("thread:delegated-task:research-api", "Research codec crate", None, "parent-a", "running", "2026-11-27T00:29:00Z", "2026-11-27T00:29:00Z"),
                ("thread:delegated-task:r-end", "PR #42 review", None, "parent-a", "running", "2026-11-27T00:29:00Z", "2026-11-27T00:29:00Z"),
                ("thread:delegated-task:other", "Implement #99", "/tmp/w99", "parent-b", "running", "2026-11-27T00:00:00Z", "2026-11-27T00:00:00Z"),
                ("thread:delegated-task:done", "Implement #1", "/tmp/w0", "parent-a", "completed", "2026-11-27T00:00:00Z", "2026-11-27T00:00:00Z"),
                ("thread:delegated-task:superseded", "Old superseded", "/tmp/old", "parent-a", "queued", "2026-11-21T19:11:30Z", "2026-11-21T19:11:30Z"),
                ("thread:delegated-task:archived", "Archived", "/tmp/arch", "parent-a", "running", "2026-11-27T00:00:00Z", "2026-11-27T00:00:00Z"),
            ],
            extra_runs=[
                ("thread:delegated-task:superseded", 2, "failed", "2026-11-21T19:25:09Z", "2026-11-21T19:25:11Z"),
                # Second open run at higher ordinal must deterministically supply status='queued' (not scan order).
                ("thread:delegated-task:w1", 2, "queued", "2026-11-27T00:01:00Z", None),
            ],
            archived_ids={"thread:delegated-task:archived"},
        )
        now_ts = resource_watch._parse_iso_ts("2026-11-27T00:30:00Z")
        summary = resource_watch.active_delegates(self.home, parent_thread="parent-a", stale_minutes=20.0, now_ts=now_ts)
        self.assertEqual(summary["active_builders"], 2)
        self.assertEqual(summary["active_reviews"], 3)
        self.assertEqual(summary["active_total"], 5)
        self.assertEqual(len(summary["stale_runs"]), 1)
        self.assertEqual(summary["stale_runs"][0]["thread_id"], "thread:delegated-task:w1")
        self.assertEqual(summary["stale_runs"][0]["status"], "queued")
        self.assertEqual(summary["stale_runs"][0]["age_minutes"], 29.0)

        # Regex parity check between model_guard.py and resource_watch.py so classifications stay aligned.
        self.assertEqual(model_guard.NON_BUILDER_SLUG_RE.pattern, resource_watch.NON_BUILDER_SLUG_RE.pattern)
        self.assertEqual(model_guard.NON_BUILDER_TITLE_RE.pattern, resource_watch.NON_BUILDER_TITLE_RE.pattern)

    def test_evaluate_breaches_and_webhook_validation_and_redirect_rejection(self):
        breaches = resource_watch.evaluate_breaches({
            "mem_avail_gib": 5.5,
            "root_free_gib": 4.0,
            "workspace_free_gib": 3.0,
            "shm_free_gib": 2.0,
            "shm_total_gib": 32.0,
        })
        self.assertEqual(len(breaches), 4)
        self.assertEqual(resource_watch.evaluate_breaches({
            "mem_avail_gib": 20.0,
            "root_free_gib": 20.0,
            "workspace_free_gib": 20.0,
            "shm_free_gib": 2.0,
            "shm_total_gib": 4.0,
        }), [])

        self.assertEqual(resource_watch.validate_webhook_url("https://example.invalid/hook"), "https://example.invalid/hook")
        self.assertEqual(resource_watch.validate_webhook_url("http://127.0.0.1:9000/hook"), "http://127.0.0.1:9000/hook")
        for bad in (
            "file:///etc/passwd",
            "http://example.invalid/hook",
            "ftp://example.invalid",
            "https://user:pass@example.invalid/hook",
            "https://user@",
            "https://:8443/hook",
        ):
            with self.assertRaises(ValueError, msg=bad):
                resource_watch.validate_webhook_url(bad)

        # Real local HTTP server: 200 succeeds, 302 redirect is rejected to prevent redirect SSRF.
        received: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                    return
                received.append(json.loads(body.decode("utf-8")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)

        resource_watch.post_webhook(f"http://127.0.0.1:{port}/ok", {"event": "test"})
        self.assertEqual(received, [{"event": "test"}])
        with self.assertRaisesRegex(ValueError, "redirects"):
            resource_watch.post_webhook(f"http://127.0.0.1:{port}/redirect", {"event": "test"})

    def test_watch_once_on_breach_stop_file_cooldown_and_retry_on_failure(self):
        bad_res = {"mem_avail_gib": 4.0, "root_free_gib": 40.0, "workspace_free_gib": 40.0, "shm_free_gib": 20.0, "shm_total_gib": 32.0}
        with patch.object(resource_watch, "host_resources", return_value=bad_res), \
             patch("sys.stdout", new_callable=io.StringIO) as buf:
            code = resource_watch.run_watch(self.home, once_on_breach=True, max_minutes=5.0)
        self.assertEqual(code, 2)
        emitted = json.loads(buf.getvalue())
        self.assertFalse(emitted["ok"])
        self.assertIn("low_ram", emitted["breaches"][0])

        stop = self.home / "stop"
        stop.write_text("", encoding="utf-8")
        with patch.object(resource_watch, "host_resources") as hr:
            self.assertEqual(resource_watch.run_watch(self.home, stop_file=stop, once_on_breach=True), 0)
            hr.assert_not_called()

        # Webhook failure does not arm cooldown; subsequent success arms cooldown.
        clock = [0.0]

        def fake_sleep(dt: float) -> None:
            clock[0] += dt

        outcomes = [OSError("transient"), None, None]
        with patch.object(resource_watch, "host_resources", return_value=bad_res), \
             patch.object(resource_watch, "post_webhook", side_effect=outcomes) as post, \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr", new_callable=io.StringIO):
            code = resource_watch.run_watch(
                self.home,
                interval_s=60.0,
                max_minutes=4.5,
                cooldown_s=150.0,
                webhook_url="https://example.invalid/hook",
                sleep_fn=fake_sleep,
                monotonic_fn=lambda: clock[0],
            )
        self.assertEqual(code, 0)
        # t=0 fails -> t=60 retries & succeeds (arms cooldown) -> t=120,180 suppressed -> t=240 fires.
        self.assertEqual(post.call_count, 3)

        with self.assertRaises(ValueError):
            resource_watch.run_watch(self.home, interval_s=0.0)

    def test_cli_snapshot_and_watch_flag_validation(self):
        snap_proc = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPTS / "resource_watch.py"),
             "snapshot", "--home", str(self.home), "--min-mem", "0", "--min-disk", "0", "--min-shm", "0"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(snap_proc.returncode, 0, snap_proc.stderr)
        self.assertTrue(json.loads(snap_proc.stdout)["ok"])

        # Missing webhook env var, missing webhook file, conflicting flags, and --interval <= 0 must fail cleanly.
        for bad_args in (
            ["watch", "--home", str(self.home), "--webhook-env", "ORCH_UNSET_TEST_VAR"],
            ["watch", "--home", str(self.home), "--webhook-file", str(self.home / "nope")],
            ["watch", "--home", str(self.home), "--webhook-env", "A", "--webhook-file", str(self.home / "b")],
            ["watch", "--home", str(self.home), "--interval", "0"],
        ):
            proc = subprocess.run(
                [sys.executable, "-I", "-B", str(SCRIPTS / "resource_watch.py"), *bad_args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(proc.returncode, 2, bad_args)


if __name__ == "__main__":
    unittest.main()
