#!/usr/bin/env python3
"""Zero-token host resource and delegated-builder watchdog for orchestration batches.

Two modes:
- `snapshot`: print live host RAM/disk//dev/shm headroom, active delegated builder
  and non-builder (review/research) counts, stale active runs, and threshold
  breaches as compact JSON.
- `watch`: poll on an interval with zero LLM tokens. Either exit with code 2 on
  first breach (`--once-on-breach`, waking a coordinator reactively via background
  task completion) or POST breach alerts to a T3 webhook read from an env var or
  file (never CLI arguments).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MIN_MEM_GIB = 8.0
DEFAULT_MIN_DISK_GIB = 6.0
DEFAULT_MIN_SHM_GIB = 4.0
MIN_SHM_TOTAL_GIB = 8.0
DEFAULT_STALE_MINUTES = 20.0
DEFAULT_INTERVAL_S = 180.0
DEFAULT_MAX_MINUTES = 240.0
DEFAULT_COOLDOWN_S = 600.0

NON_BUILDER_SLUG_RE = re.compile(
    r"(?:^|[:/_-])(?:(?:re-?)?review(?:er)?|research|explore|audit|council|adversar\w*)(?=$|[:/_-]|r?\d)",
    re.IGNORECASE,
)
NON_BUILDER_TITLE_RE = re.compile(
    r"(?:^\s*(?:(?:re-?)?review(?:er)?\b|(?:code|pr)\s+review(?:er)?\b|independent\s+(?:delta\s+)?review\b|"
    r"adversar\w*\b|audit\b|council\b|research\b|explore\b)|"
    r"\b(?:re-?)?review(?:er)?\s*$)",
    re.IGNORECASE,
)


def _sqlite_ro_uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def host_resources(workspace_path: Path | None = None) -> dict[str, float | None]:
    mem_avail_gib: float | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isascii() and parts[1].isdigit():
                    mem_avail_gib = round(int(parts[1]) / (1024.0 * 1024.0), 2)
                break
    except OSError:
        pass

    def free_and_total(path: str) -> tuple[float | None, float | None]:
        try:
            st = os.statvfs(path)
            free = round((st.f_bavail * st.f_frsize) / (1024.0 ** 3), 2)
            total = round((st.f_blocks * st.f_frsize) / (1024.0 ** 3), 2)
            return free, total
        except OSError:
            return None, None

    root_free, _ = free_and_total("/")
    ws_free: float | None = None
    if workspace_path is not None:
        ws_free, _ = free_and_total(str(workspace_path))
    shm_free, shm_total = free_and_total("/dev/shm")
    return {
        "mem_avail_gib": mem_avail_gib,
        "root_free_gib": root_free,
        "workspace_free_gib": ws_free,
        "shm_free_gib": shm_free,
        "shm_total_gib": shm_total,
    }


def _parse_iso_ts(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def active_delegates(
    home: Path,
    *,
    parent_thread: str | None = None,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
    now_ts: float | None = None,
) -> dict[str, object]:
    db_path = home / ".t3/userdata/statev2.sqlite"
    empty = {
        "active_builders": 0,
        "active_reviews": 0,
        "active_total": 0,
        "stale_runs": [],
    }
    if not db_path.is_file():
        return empty
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=0.5)
        try:
            rows = conn.execute(
                "SELECT t.thread_id, t.title, t.updated_at, "
                "json_extract(t.payload_json, '$.worktreePath'), "
                "json_extract(t.payload_json, '$.lineage.parentThreadId'), "
                "r.ordinal, r.status, r.requested_at "
                "FROM orchestration_v2_projection_runs r "
                "JOIN orchestration_v2_projection_threads t ON t.thread_id = r.thread_id "
                "WHERE r.status IN ('running', 'starting', 'preparing', 'queued') "
                "AND r.completed_at IS NULL "
                "AND t.archived_at IS NULL AND t.deleted_at IS NULL "
                "AND NOT EXISTS ("
                "    SELECT 1 FROM orchestration_v2_projection_runs r2 "
                "    WHERE r2.thread_id = r.thread_id AND r2.ordinal > r.ordinal "
                "    AND (r2.completed_at IS NOT NULL "
                "         OR r2.status NOT IN ('running', 'starting', 'preparing', 'queued'))"
                ") "
                "AND (t.thread_id LIKE 'thread:delegated-task:%' "
                "     OR json_extract(t.payload_json, '$.lineage.relationshipToParent') = 'subagent') "
                "ORDER BY r.ordinal DESC, r.run_id DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return empty

    now = time.time() if now_ts is None else now_ts
    by_thread: dict[str, dict[str, object]] = {}
    for tid, title, updated_at, wt, parent_id, _ordinal, status, requested_at in rows:
        if not isinstance(tid, str):
            continue
        if parent_thread and parent_id != parent_thread:
            continue
        ts_candidates = [ts for ts in (_parse_iso_ts(updated_at), _parse_iso_ts(requested_at)) if ts is not None]
        latest_ts = max(ts_candidates) if ts_candidates else None
        prev = by_thread.get(tid)
        if prev is not None:
            # First row has the highest ordinal (deterministic status); keep max timestamp across open rows.
            prev_ts = prev["latest_ts"]
            if latest_ts is not None and (not isinstance(prev_ts, float) or latest_ts > prev_ts):
                prev["latest_ts"] = latest_ts
            continue
        by_thread[tid] = {
            "thread_id": tid,
            "title": title if isinstance(title, str) else "",
            "worktree_path": wt.strip() if isinstance(wt, str) and wt.strip() else None,
            "parent_thread_id": parent_id if isinstance(parent_id, str) else None,
            "status": status if isinstance(status, str) else "",
            "latest_ts": latest_ts,
        }

    builders = 0
    non_builders = 0
    stale_runs: list[dict[str, object]] = []
    for info in by_thread.values():
        wt = info["worktree_path"]
        tid = str(info["thread_id"])
        title = str(info["title"])
        is_non_builder = wt is None and bool(NON_BUILDER_SLUG_RE.search(tid) or NON_BUILDER_TITLE_RE.search(title))
        if is_non_builder:
            non_builders += 1
        else:
            builders += 1
        latest_ts = info["latest_ts"]
        if isinstance(latest_ts, float) and stale_minutes > 0:
            age_min = (now - latest_ts) / 60.0
            if age_min >= stale_minutes:
                stale_runs.append({
                    "thread_id": tid,
                    "title": title,
                    "kind": "review_or_research" if is_non_builder else "builder",
                    "status": info["status"],
                    "age_minutes": round(age_min, 1),
                    "worktree_path": wt,
                    "parent_thread_id": info["parent_thread_id"],
                })
    stale_runs.sort(key=lambda item: float(item["age_minutes"]), reverse=True)
    return {
        "active_builders": builders,
        "active_reviews": non_builders,
        "active_total": builders + non_builders,
        "stale_runs": stale_runs,
    }


def evaluate_breaches(
    resources: dict[str, float | None],
    *,
    min_mem_gib: float = DEFAULT_MIN_MEM_GIB,
    min_disk_gib: float = DEFAULT_MIN_DISK_GIB,
    min_shm_gib: float = DEFAULT_MIN_SHM_GIB,
) -> list[str]:
    breaches: list[str] = []
    mem = resources.get("mem_avail_gib")
    if mem is not None and mem < min_mem_gib:
        breaches.append(f"low_ram:{mem:.2f}GiB<{min_mem_gib:.1f}GiB")
    root_free = resources.get("root_free_gib")
    if root_free is not None and root_free < min_disk_gib:
        breaches.append(f"low_root_disk:{root_free:.2f}GiB<{min_disk_gib:.1f}GiB")
    ws_free = resources.get("workspace_free_gib")
    if ws_free is not None and ws_free < min_disk_gib:
        breaches.append(f"low_workspace_disk:{ws_free:.2f}GiB<{min_disk_gib:.1f}GiB")
    shm_free = resources.get("shm_free_gib")
    shm_total = resources.get("shm_total_gib")
    if shm_free is not None and shm_total is not None and shm_total >= MIN_SHM_TOTAL_GIB and shm_free < min_shm_gib:
        breaches.append(f"low_shm:{shm_free:.2f}GiB<{min_shm_gib:.1f}GiB")
    return breaches


def take_snapshot(
    home: Path,
    *,
    workspace_path: Path | None = None,
    parent_thread: str | None = None,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
    min_mem_gib: float = DEFAULT_MIN_MEM_GIB,
    min_disk_gib: float = DEFAULT_MIN_DISK_GIB,
    min_shm_gib: float = DEFAULT_MIN_SHM_GIB,
) -> dict[str, object]:
    res = host_resources(workspace_path)
    delegates = active_delegates(home, parent_thread=parent_thread, stale_minutes=stale_minutes)
    breaches = evaluate_breaches(res, min_mem_gib=min_mem_gib, min_disk_gib=min_disk_gib, min_shm_gib=min_shm_gib)
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": not breaches,
        "breaches": breaches,
        "host": res,
        "delegates": delegates,
    }


def validate_webhook_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Webhook URL must not include userinfo credentials")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a hostname")
    if parsed.scheme == "https":
        return cleaned
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return cleaned
    raise ValueError("Webhook URL must use https:// or localhost http://")


def _load_webhook_url(webhook_env: str | None, webhook_file: Path | None) -> str | None:
    if webhook_env and webhook_file is not None:
        raise ValueError("Pass at most one of --webhook-env or --webhook-file")
    if webhook_env:
        raw = os.environ.get(webhook_env, "").strip()
        if not raw:
            raise ValueError(f"Webhook env var {webhook_env!r} is unset or empty")
        return validate_webhook_url(raw)
    if webhook_file is not None:
        if not webhook_file.is_file():
            raise ValueError(f"Webhook file does not exist: {webhook_file}")
        raw = webhook_file.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError(f"Webhook file is empty: {webhook_file}")
        return validate_webhook_url(raw)
    return None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(f"Webhook redirects are not allowed (HTTP {code})")


def post_webhook(url: str, payload: dict[str, object], timeout_s: float = 10.0) -> None:
    validated = validate_webhook_url(url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        validated,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "agents-orchestration-watch/1"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(req, timeout=timeout_s) as resp:
        resp.read(1024)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run_watch(
    home: Path,
    *,
    workspace_path: Path | None = None,
    parent_thread: str | None = None,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
    min_mem_gib: float = DEFAULT_MIN_MEM_GIB,
    min_disk_gib: float = DEFAULT_MIN_DISK_GIB,
    min_shm_gib: float = DEFAULT_MIN_SHM_GIB,
    interval_s: float = DEFAULT_INTERVAL_S,
    max_minutes: float = DEFAULT_MAX_MINUTES,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    once_on_breach: bool = False,
    webhook_url: str | None = None,
    stop_file: Path | None = None,
    pid: int | None = None,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> int:
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")
    deadline = monotonic_fn() + max(0.0, max_minutes * 60.0)
    last_alert_at: float | None = None

    while True:
        if stop_file is not None and stop_file.exists():
            return 0
        if pid is not None and not _pid_alive(pid):
            return 0

        snap = take_snapshot(
            home,
            workspace_path=workspace_path,
            parent_thread=parent_thread,
            stale_minutes=stale_minutes,
            min_mem_gib=min_mem_gib,
            min_disk_gib=min_disk_gib,
            min_shm_gib=min_shm_gib,
        )
        now_m = monotonic_fn()
        if not snap["ok"]:
            should_emit = last_alert_at is None or (now_m - last_alert_at) >= cooldown_s
            if should_emit:
                delivered = True
                if webhook_url:
                    try:
                        post_webhook(webhook_url, {"event": "resource_breach", **snap})
                    except (OSError, ValueError, urllib.error.URLError) as exc:
                        delivered = False
                        print(f"resource_watch webhook failed: {exc}", file=sys.stderr, flush=True)
                if delivered:
                    last_alert_at = now_m
                print(json.dumps(snap), flush=True)
            if once_on_breach:
                return 2

        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            return 0
        sleep_fn(min(interval_s, remaining))


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", type=Path, help="Target workspace path for mount disk check")
    parser.add_argument("--home", type=Path, default=Path.home(), help="Home directory containing .t3/userdata")
    parser.add_argument("--parent-thread", help="Filter active/stale delegates to one parentThreadId")
    parser.add_argument("--stale-minutes", type=float, default=DEFAULT_STALE_MINUTES)
    parser.add_argument("--min-mem", type=float, default=DEFAULT_MIN_MEM_GIB)
    parser.add_argument("--min-disk", type=float, default=DEFAULT_MIN_DISK_GIB)
    parser.add_argument("--min-shm", type=float, default=DEFAULT_MIN_SHM_GIB)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="Print one JSON snapshot of host headroom and active delegates")
    _add_common_args(p_snap)

    p_watch = sub.add_parser("watch", help="Zero-token polling watchdog for RAM, disk, and /dev/shm")
    _add_common_args(p_watch)
    p_watch.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="Poll interval in seconds (> 0)")
    p_watch.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES, help="Auto-stop after N minutes")
    p_watch.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_S, help="Min seconds between alerts")
    p_watch.add_argument("--once-on-breach", action="store_true", help="Exit 2 on first breach (reactive wakeup)")
    p_watch.add_argument("--webhook-env", help="Env var holding a T3 webhook URL (never pass URLs in argv)")
    p_watch.add_argument("--webhook-file", type=Path, help="0600 file holding a T3 webhook URL")
    p_watch.add_argument("--stop-file", type=Path, help="Stop cleanly with exit 0 if this sentinel file exists")
    p_watch.add_argument("--pid", type=int, help="Stop cleanly with exit 0 once this PID exits")

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        snap = take_snapshot(
            args.home,
            workspace_path=args.path,
            parent_thread=args.parent_thread,
            stale_minutes=args.stale_minutes,
            min_mem_gib=args.min_mem,
            min_disk_gib=args.min_disk,
            min_shm_gib=args.min_shm,
        )
        print(json.dumps(snap, indent=2))
        return 0 if snap["ok"] else 2

    if args.interval <= 0:
        print("Invalid --interval: must be > 0", file=sys.stderr)
        return 2
    if args.pid is not None and args.pid <= 0:
        print("Invalid --pid: must be > 0", file=sys.stderr)
        return 2
    try:
        webhook_url = _load_webhook_url(args.webhook_env, args.webhook_file)
    except (OSError, ValueError) as exc:
        print(f"Invalid webhook configuration: {exc}", file=sys.stderr)
        return 2

    return run_watch(
        args.home,
        workspace_path=args.path,
        parent_thread=args.parent_thread,
        stale_minutes=args.stale_minutes,
        min_mem_gib=args.min_mem,
        min_disk_gib=args.min_disk,
        min_shm_gib=args.min_shm,
        interval_s=args.interval,
        max_minutes=args.max_minutes,
        cooldown_s=max(0.0, args.cooldown),
        once_on_breach=args.once_on_breach,
        webhook_url=webhook_url,
        stop_file=args.stop_file,
        pid=args.pid,
    )


if __name__ == "__main__":
    raise SystemExit(main())
