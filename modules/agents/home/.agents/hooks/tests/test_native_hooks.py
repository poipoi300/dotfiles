"""Native hook protocol, shell rewrite, design hook, and registration fixtures."""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.abspath(__file__)).parents[2]
sys.path.insert(0, str(ROOT / "hooks"))
import behavior
import journal
import model_guard
import native
import register

needs_rtk = unittest.skipUnless(shutil.which("rtk"), "rtk is not installed")
needs_engine = unittest.skipUnless(behavior.impeccable_engine(ROOT / "skills" / "impeccable"),
                                   "the impeccable engine is not installed")


def usage(**kwargs):
    """Stand-in for the private usage/budget.py module the SessionStart hook optionally uses."""
    return patch.object(native, "budget", types.SimpleNamespace(**kwargs))


class ModelGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="model-guard-")
        self.home = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        rates_path = self.home / ".t3/userdata/usage-model-rates.json"
        rates_path.parent.mkdir(parents=True)
        rates_path.write_text(json.dumps({"document": {
            "claude-fable-5-2": {"input_cost_per_token": 8e-6, "output_cost_per_token": 40e-6,
                                 "cache_read_input_token_cost": 2e-7, "cache_creation_input_token_cost": 10e-6,
                                 "max_input_tokens": 1_000_000},
            "claude-opus-5-5": {"input_cost_per_token": 4e-6, "output_cost_per_token": 20e-6,
                                "cache_read_input_token_cost": 2e-7, "cache_creation_input_token_cost": 5e-6,
                                "max_input_tokens": 1_000_000},
            "gpt-6.2-sol": {"input_cost_per_token": 2e-6, "output_cost_per_token": 10e-6,
                            "cache_read_input_token_cost": 1e-7, "cache_creation_input_token_cost": 2.5e-6,
                            "max_input_tokens": 475_000},
            "gpt-6.2-astra": {"input_cost_per_token": 8e-6, "output_cost_per_token": 40e-6,
                              "cache_read_input_token_cost": 4e-7, "cache_creation_input_token_cost": 10e-6,
                              "max_input_tokens": 258_400},
        }}), encoding="utf-8")

    def decide(self, tool, tool_input, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, **extra}
        healthy = {"mem_avail_gib": 32.0, "root_free_gib": 50.0, "shm_free_gib": 24.0, "shm_total_gib": 46.0}
        with patch.object(Path, "home", return_value=self.home), \
             patch.object(model_guard, "routing", None), \
             patch.object(model_guard, "_host_resources", return_value=healthy):
            return native.dispatch("claude", payload).get("hookSpecificOutput", {})

    def _seed_statev2(self, threads: list[tuple], *, extra_runs: list[tuple] = ()) -> None:
        """Create a T3 statev2.sqlite projection matching live thread/run columns."""
        db = self.home / ".t3/userdata/statev2.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "CREATE TABLE orchestration_v2_projection_threads "
                "(thread_id TEXT PRIMARY KEY, title TEXT, updated_at TEXT, archived_at TEXT, deleted_at TEXT, payload_json TEXT)"
            )
            conn.execute(
                "CREATE TABLE orchestration_v2_projection_runs "
                "(run_id TEXT PRIMARY KEY, thread_id TEXT, ordinal INTEGER, status TEXT, requested_at TEXT, completed_at TEXT)"
            )
            for idx, row in enumerate(threads):
                tid, title, wt, rel, status = row[:5]
                provider = row[5] if len(row) > 5 else None
                model = row[6] if len(row) > 6 else None
                updated_at = row[7] if len(row) > 7 else None
                archived_at = row[8] if len(row) > 8 else None
                payload = {"worktreePath": wt, "lineage": {"relationshipToParent": rel}}
                if provider:
                    payload["providerInstanceId"] = provider
                if model or provider:
                    payload["modelSelection"] = {"provider": provider, "model": model}
                conn.execute(
                    "INSERT INTO orchestration_v2_projection_threads VALUES (?, ?, ?, ?, ?, ?)",
                    (tid, title, updated_at, archived_at, None, json.dumps(payload)),
                )
                completed_at = "2026-11-26T00:00:00Z" if status not in ("running", "starting", "preparing", "queued") else None
                conn.execute(
                    "INSERT INTO orchestration_v2_projection_runs VALUES (?, ?, ?, ?, ?, ?)",
                    (f"run:{idx}", tid, 1, status, updated_at, completed_at),
                )
            for ridx, (tid, ordinal, status, req_at, comp_at) in enumerate(extra_runs):
                conn.execute(
                    "INSERT INTO orchestration_v2_projection_runs VALUES (?, ?, ?, ?, ?, ?)",
                    (f"extra:{ridx}", tid, ordinal, status, req_at, comp_at),
                )
            conn.commit()
        finally:
            conn.close()

    def test_ultra_tier_delegates_and_launches_require_opt_in_without_blanket_ban(self):
        for tool, tool_input in (
            ("Agent", {"prompt": "x", "subagent_type": "reviewer", "model": "fable"}),
            ("Agent", {"prompt": "x", "subagent_type": "reviewer", "model": "Claude-Fable-5-2"}),
            ("spawn_agent", {"prompt": "x", "model": "GPT-6.2-Astra"}),
            ("mcp__t3-code__delegate_task", {"task": "x", "target": {"provider": "claude", "model": "claude-fable-5-2"}}),
            ("mcp__t3-code__t3_thread_launch", {"prompt": "x", "target": {"model": "gpt-6.2-astra"}}),
            ("mcp__t3-code__create_threads", {"threads": [{"prompt": "x", "target": {"model": "gpt-6.2-astra"}}]}),
        ):
            output = self.decide(tool, tool_input)
            self.assertEqual(output["permissionDecision"], "deny", tool)
            self.assertIn("[ultra:", output["permissionDecisionReason"])

        # Explicit [ultra: <reason>] opt-in is allowed (no permanent blanket block).
        allowed = self.decide("mcp__t3-code__delegate_task", {
            "task": "[ultra: stuck concurrency deadlock] diagnose race",
            "target": {"model": "claude-fable-5-2"},
        })
        self.assertNotIn("permissionDecision", allowed)

    def test_standard_and_flagship_models_are_allowed(self):
        self.assertEqual(self.decide("Agent", {"prompt": "compare fable and opus", "subagent_type": "Explore", "model": "opus"}), {})
        self.assertEqual(self.decide("mcp__t3-code__delegate_task", {"task": "x", "target": {"model": "gpt-6.2-sol"}}), {})

    def test_claude_native_fork_is_denied_above_150k_context_and_allowed_below(self):
        transcript = self.home / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 500, "cache_read_input_tokens": 180_000, "cache_creation_input_tokens": 1000}},
        }) + "\n", encoding="utf-8")
        # Omitted subagent_type or explicit "fork" over 150k -> denied.
        for inp in ({"prompt": "do work"}, {"prompt": "do work", "subagent_type": "fork"}):
            out = self.decide("Agent", inp, transcript_path=str(transcript))
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn("150k", out["permissionDecisionReason"])
        # Explicit non-fork subagent_type over 150k -> allowed.
        self.assertEqual(self.decide("Agent", {"prompt": "do work", "subagent_type": "Explore"},
                                     transcript_path=str(transcript)), {})
        # Large >128 KiB tool_result at transcript tail does not hide an earlier >150k assistant usage entry.
        assistant_line = json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 500, "cache_read_input_tokens": 180_000, "cache_creation_input_tokens": 1000}},
        })
        huge_tail = json.dumps({"type": "user", "content": "x" * 200_000})
        transcript.write_text(assistant_line + "\n" + huge_tail + "\n", encoding="utf-8")
        out = self.decide("Agent", {"prompt": "do work"}, transcript_path=str(transcript))
        self.assertEqual(out["permissionDecision"], "deny")
        # Under 150k -> fork allowed.
        transcript.write_text(json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 500, "cache_read_input_tokens": 80_000, "cache_creation_input_tokens": 0}},
        }) + "\n", encoding="utf-8")
        self.assertEqual(self.decide("Agent", {"prompt": "do work"}, transcript_path=str(transcript)), {})

    def test_mid_session_budget_refreshes_on_stale_delegation_only(self):
        startup = unittest.mock.Mock(return_value="[Budget fresh]")
        healthy = {"mem_avail_gib": 32.0, "root_free_gib": 50.0, "target_free_gib": 50.0, "shm_free_gib": 24.0, "shm_total_gib": 46.0}
        with patch.object(native, "ROOT", self.home), \
             patch.object(Path, "home", return_value=self.home), \
             patch.object(model_guard, "routing", None), \
             patch.object(model_guard, "_host_resources", return_value=healthy), \
             patch.object(behavior, "design_hook", return_value={}), \
             usage(startup=startup):
            # SessionStart records the fresh marker.
            native.dispatch("claude", {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(self.home)})
            self.assertEqual(startup.call_count, 1)
            # Immediate delegation sees fresh marker -> no redundant refresh.
            out = native.dispatch("claude", {
                "hook_event_name": "PreToolUse", "session_id": "s1", "cwd": str(self.home),
                "tool_name": "mcp__t3-code__delegate_task", "tool_input": {"task": "x", "target": {"model": "gpt-6.2-sol"}},
            })
            self.assertEqual(out, {})
            self.assertEqual(startup.call_count, 1)
            # Backdate marker past refresh interval -> next delegation injects refreshed snapshot.
            marker = native._session_budget_marker("claude", "s1")
            marker.write_text(json.dumps({"at": 1.0}), encoding="utf-8")
            out2 = native.dispatch("claude", {
                "hook_event_name": "PreToolUse", "session_id": "s1", "cwd": str(self.home),
                "tool_name": "mcp__t3-code__delegate_task", "tool_input": {"task": "x", "target": {"model": "gpt-6.2-sol"}},
            })
            self.assertEqual(out2["hookSpecificOutput"]["additionalContext"], "[Budget fresh]")
            self.assertEqual(startup.call_count, 2)

    def test_review_target_score_leak_is_denied_while_rubric_scales_pass(self):
        for leaky in (
            "You are a Panel Council reviewer. Evaluate the repo; target score is 9.5/10.",
            "Act as an independent reviewer and score the rubric until it reaches 9.0/10.",
            "Act as an independent reviewer; must score 9.0 and passing bar is 9.0.",
            "Council review: check if the candidate meets the 9.5/10 consensus bar.",
            "Adversarial audit: acceptance threshold >= 9.0 across all criteria.",
            "Council reviewer: aim for 9.5 on every criterion.",
        ):
            out = self.decide("mcp__t3-code__delegate_task", {"task": leaky, "target": {"model": "gpt-6.2-sol"}})
            self.assertEqual(out.get("permissionDecision"), "deny", leaky)
            self.assertIn("blind to target scores", out.get("permissionDecisionReason", ""))

        # Blind rubric scale definitions, code/config technical numbers, and ordinary non-review tasks pass.
        for clean in (
            "You are a Panel Council reviewer. Score each criterion on a 0-10 scale (9-10 = excellent, 6-7 = acceptable).",
            "Act as a reviewer for the Raft module: verify quorum consensus of 3 nodes and target: 3.12 compatibility.",
            "Council audit: check that migration target = 0 rows affected across target of 2 files.",
            "Implement the scoring function so test fixture reaches 9.5/10.",
        ):
            out = self.decide("mcp__t3-code__delegate_task", {"task": clean, "target": {"model": "gpt-6.2-sol"}})
            self.assertNotIn("permissionDecision", out, clean)

    def test_council_child_cannot_spawn_nested_subagents_while_parents_and_authorized_children_can(self):
        # T3 delegated Claude council child transcript -> denied from calling Agent or delegate_task,
        # including negative phrasings like "Do not spawn subagents" or "not allowed to delegate".
        for prompt_body in (
            "Perform an independent Panel Council audit of the repo. Do NOT delegate.",
            "You are a Panel Council reviewer. Do not spawn subagents.",
            "Audit this commit read-only. You are not allowed to delegate to subagents.",
        ):
            child_tr = self.home / "child.jsonl"
            child_tr.write_text(json.dumps({
                "type": "user",
                "message": {"content": (
                    '[T3 Code: the agent in T3 thread "parent-1" sent this message, not the user.]\n\n'
                    + prompt_body
                )},
            }) + "\n", encoding="utf-8")
            out = self.decide("Agent", {"prompt": "probe sqlite", "subagent_type": "adversary"}, transcript_path=str(child_tr))
            self.assertEqual(out.get("permissionDecision"), "deny", prompt_body)
            self.assertIn("must not spawn subagents", out.get("permissionDecisionReason", ""))

        # Large >64 KiB developer preamble before first user message (e.g. Codex) is still detected.
        codex_tr = self.home / "codex-child.jsonl"
        preamble = json.dumps({"type": "response_item", "payload": {"role": "developer", "content": [{"text": "A" * 100_000}]}})
        user_row = json.dumps({"type": "response_item", "payload": {"role": "user", "content": (
            '[T3 Code: the agent in T3 thread "parent-1" sent this message, not the user.]\n'
            "Code council seat: do not spawn subagents."
        )}})
        codex_tr.write_text(preamble + "\n" + user_row + "\n", encoding="utf-8")
        out_codex = self.decide("spawn_agent", {"prompt": "helper"}, transcript_path=str(codex_tr))
        self.assertEqual(out_codex.get("permissionDecision"), "deny")

        # Top-level parent transcript containing T3 boilerplate is NOT blocked.
        parent_tr = self.home / "parent.jsonl"
        parent_tr.write_text(json.dumps({
            "type": "user",
            "message": {"content": (
                "<t3_code_instructions>As a subagent or child, start no subagents.</t3_code_instructions>\n"
                "Build this app and run a council review."
            )},
        }) + "\n", encoding="utf-8")
        self.assertEqual(
            self.decide("Agent", {"prompt": "probe sqlite", "subagent_type": "adversary"}, transcript_path=str(parent_tr)),
            {},
        )

        # Delegated child explicitly authorized to spawn subagents is allowed.
        coord_tr = self.home / "coord.jsonl"
        coord_tr.write_text(json.dumps({
            "type": "user",
            "message": {"content": (
                '[T3 Code: the agent in T3 thread "parent-1" sent this message, not the user.]\n\n'
                "You are a review coordinator; you may delegate individual seats to subagents."
            )},
        }) + "\n", encoding="utf-8")
        self.assertEqual(
            self.decide("Agent", {"prompt": "audit auth", "subagent_type": "adversary"}, transcript_path=str(coord_tr)),
            {},
        )

        # Native Claude subagent under /subagents/ running as a reviewer -> denied.
        sub_tr = self.home / "sess/subagents/agent-1.jsonl"
        sub_tr.parent.mkdir(parents=True)
        sub_tr.write_text(json.dumps({
            "type": "user",
            "message": {"content": "Act as the review sub-agent for this diff. Read-only audit."},
        }) + "\n", encoding="utf-8")
        out_sub = self.decide("Agent", {"prompt": "nested", "subagent_type": "Explore"}, transcript_path=str(sub_tr))
        self.assertEqual(out_sub["permissionDecision"], "deny")

        # Ordinary delegated implementation worker with no delegation wording -> default-DENIED.
        worker_tr = self.home / "worker-child.jsonl"
        worker_tr.write_text(json.dumps({
            "type": "user",
            "message": {"content": (
                '[T3 Code: the agent in T3 thread "coord-1" sent this message, not the user.]\n\n'
                "Implement GitHub issue #163 animated stills in this worktree and commit locally."
            )},
        }) + "\n", encoding="utf-8")
        for tool, inp in (
            ("Agent", {"prompt": "review my diff", "subagent_type": "reviewer"}),
            ("mcp__t3-code__delegate_task", {"task": "review #163", "target": {"model": "gemini-4-neon"}}),
        ):
            out_w = self.decide(tool, inp, transcript_path=str(worker_tr))
            self.assertEqual(out_w.get("permissionDecision"), "deny", tool)
            self.assertIn("must not spawn subagents", out_w.get("permissionDecisionReason", ""))

    def test_claude_max_and_ultrathink_effort_are_blocked_while_xhigh_and_non_claude_pass(self):
        self._seed_statev2([
            ("claude-top-1", "Main Claude Thread", None, None, "idle", "claudeAgent", "claude-opus-5-5"),
        ])
        for tool, inp in (
            ("mcp__t3-code__delegate_task", {
                "task": "implement #163",
                "target": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "max"}},
            }),
            ("mcp__t3-code__delegate_task", {
                "task": "implement #163",
                "target": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5",
                           "options": [{"id": "effort", "value": "ultrathink"}]},
            }),
            # Opaque providerInstanceId with driverKind=claudeAgent (R3a).
            ("mcp__t3-code__delegate_task", {
                "task": "implement #163",
                "target": {"providerInstanceId": "default", "driverKind": "claude-code", "options": {"effort": "max"}},
            }),
            # Outer modelSelection paired with sibling options (R2).
            ("mcp__t3-code__t3_thread_launch", {
                "title": "Build feature", "message": "go",
                "modelSelection": {"provider": "claude", "model": "claude-opus-5-5"},
                "options": {"effort": "max"},
            }),
            # Thread-configure updating only options on an existing Claude thread in statev2 (R2).
            ("mcp__t3-code__t3_thread_configure", {
                "threadId": "claude-top-1",
                "modelSelection": {"options": {"effort": "max"}},
            }),
            # Batch create_threads: opt-in in item 0 must NOT authorize item 1 (R3c).
            ("mcp__t3-code__create_threads", {
                "threads": [
                    {"prompt": "[max-effort: explicit] a", "target": {"model": "claude-opus-5-5", "options": {"effort": "max"}}},
                    {"prompt": "b", "target": {"model": "claude-opus-5-5", "options": {"effort": "max"}}},
                ],
            }),
            # Agent with omitted model inherits Claude (R3b).
            ("Agent", {"prompt": "go", "subagent_type": "general-purpose", "options": {"effort": "max"}}),
            ("Agent", {"prompt": "go", "subagent_type": "general-purpose", "model": "claude-opus-5-5-max"}),
        ):
            out = self.decide(tool, inp)
            self.assertEqual(out.get("permissionDecision"), "deny", (tool, inp))
            self.assertIn("xhigh", out.get("permissionDecisionReason", ""))

        # xhigh/high on Claude, non-Claude max, and explicit [max-effort: ...] opt-in pass.
        for tool, inp in (
            ("mcp__t3-code__delegate_task", {
                "task": "implement #163",
                "target": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            }),
            ("mcp__t3-code__delegate_task", {
                "task": "implement #163",
                "target": {"providerInstanceId": "codex", "model": "gpt-6.2-sol", "options": {"reasoningEffort": "max"}},
            }),
            ("mcp__t3-code__delegate_task", {
                "task": "[max-effort: user explicitly requested] implement #163",
                "target": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "max"}},
            }),
            ("mcp__t3-code__t3_thread_configure", {
                "modelSelection": {"model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            }),
            ("mcp__t3-code__create_threads", {
                "target": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                "threads": [{"prompt": "go", "target": {"providerInstanceId": "codex", "options": {"effort": "max"}}}],
            }),
        ):
            self.assertNotIn("permissionDecision", self.decide(tool, inp), (tool, inp))

    def test_thread_send_blocks_reviews_and_recycled_workers_but_allows_same_issue_followups(self):
        self._seed_statev2([
            ("thread:delegated-task:review-149-r1", "Review #170 R1: Theme system", None, "subagent", "completed"),
            ("thread:delegated-task:worker-148-v1", "Implement #148: Canvas shade dropdowns", "/tmp/wt-148", "subagent", "completed"),
            ("thread:delegated-task:fix-page-preview", "Implement #102: Remote ZIP first preview", "/tmp/wt-102", "subagent", "completed"),
            ("top-level-thread-1", "Main Feature Thread", None, None, "idle"),
            ("top-level-review-1", "Review PR #42 architecture", None, None, "idle"),
        ])
        # Sending a re-review or new review to a reviewer thread -> denied.
        out_rev = self.decide("mcp__t3-code__t3_thread_send", {
            "threadId": "thread:delegated-task:review-149-r1",
            "message": "Round 2 review of #170: verify R1-R8.",
        })
        self.assertEqual(out_rev.get("permissionDecision"), "deny")
        self.assertIn("fresh delegate_task", out_rev.get("permissionDecisionReason", ""))

        # Sending explicit review prompts to a delegated worker thread -> denied (R6).
        for rev_msg in (
            "Review only. Do not edit, commit, or push. Audit commit abc123.",
            "Please re-review this branch after the fix.",
            "Review PR #148 and report blockers.",
        ):
            out_rev_msg = self.decide("mcp__t3-code__t3_thread_send", {
                "threadId": "thread:delegated-task:worker-148-v1",
                "message": rev_msg,
            })
            self.assertEqual(out_rev_msg.get("permissionDecision"), "deny", rev_msg)

        # Recycling a delegated worker with freshContext=true -> denied.
        out_fresh = self.decide("mcp__t3-code__t3_thread_send", {
            "threadId": "thread:delegated-task:worker-148-v1",
            "freshContext": True,
            "message": "Now build #176 plugin manager.",
        })
        self.assertEqual(out_fresh.get("permissionDecision"), "deny")
        self.assertIn("freshContext=true", out_fresh.get("permissionDecisionReason", ""))

        # Repurposing a delegated worker onto a different issue (even with preamble or referencing old issue, R5) -> denied.
        for repurpose_msg in (
            "Implement #176: Plugin manager, trust levels, and SDK.",
            "Please now fix #176 since #148 is done.",
        ):
            out_repurpose = self.decide("mcp__t3-code__t3_thread_send", {
                "threadId": "thread:delegated-task:worker-148-v1",
                "message": repurpose_msg,
            })
            self.assertEqual(out_repurpose.get("permissionDecision"), "deny", repurpose_msg)
            self.assertIn("#176", out_repurpose.get("permissionDecisionReason", ""))

        # Same-issue fix follow-ups (including summary lead-ins & "preview" slug, R4/R6) -> ALLOWED.
        for ok_tid, ok_msg in (
            ("thread:delegated-task:worker-148-v1", "Fix R1-R5 findings from round 1 review on #148 and commit locally."),
            ("thread:delegated-task:worker-148-v1", "Round 1 review found 2 issues — fix both and re-run tests."),
            ("thread:delegated-task:worker-148-v1", "Review the failing test output below and fix it."),
            ("thread:delegated-task:fix-page-preview", "Fix the seek budget test failure on #102."),
        ):
            self.assertEqual(self.decide("mcp__t3-code__t3_thread_send", {
                "threadId": ok_tid, "message": ok_msg,
            }), {}, (ok_tid, ok_msg))

        # Top-level user threads (even with a "Review ..." title, R1) -> ALLOWED.
        for top_tid in ("top-level-thread-1", "top-level-review-1"):
            self.assertEqual(self.decide("mcp__t3-code__t3_thread_send", {
                "threadId": top_tid, "message": "Please merge the approved PRs.",
            }), {}, top_tid)

    def test_builder_concurrency_cap_and_resource_headroom_guards(self):
        cargo_dir = self.home / "rust-repo"
        cargo_dir.mkdir()
        (cargo_dir / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        plain_dir = self.home / "docs-repo"
        plain_dir.mkdir()

        # 4 active delegated builders (including "preview" slug without worktree, R4) + 2 active read-only reviews.
        # Superseded queued runs, archived threads, and >12h-stale zombie runs must NOT steal builder slots.
        self._seed_statev2(
            [
                ("thread:delegated-task:w1", "Implement #1", "/tmp/w1", "subagent", "running"),
                ("thread:delegated-task:w2", "Implement #2", "/tmp/w2", "subagent", "running"),
                ("thread:delegated-task:w3", "Implement #3", "/tmp/w3", "subagent", "starting"),
                ("thread:delegated-task:fix-preview", "Implement #4 preview", None, "subagent", "queued"),
                ("thread:delegated-task:review-1-r1", "Review #1 R1", None, "subagent", "running"),
                ("thread:delegated-task:r2", "Review #2 R1", None, "subagent", "running"),
                ("thread:delegated-task:research-1", "Research crate options", None, "subagent", "running"),
                ("thread:delegated-task:r-end", "PR #99 review", None, "subagent", "running"),
                ("thread:delegated-task:done", "Implement #0", "/tmp/w0", "subagent", "completed"),
                ("thread:delegated-task:superseded", "Old superseded", "/tmp/old", "subagent", "queued"),
                ("thread:delegated-task:archived", "Archived run", "/tmp/arch", "subagent", "running", None, None, None, "2026-11-26T00:00:00Z"),
                ("thread:delegated-task:zombie", "Stale zombie", "/tmp/zmb", "subagent", "running", None, None, "2020-01-01T00:00:00Z"),
            ],
            extra_runs=[
                ("thread:delegated-task:superseded", 2, "failed", "2026-11-21T19:25:00Z", "2026-11-21T19:25:11Z"),
                ("thread:delegated-task:w1", 2, "queued", None, None),
            ],
        )
        self.assertEqual(model_guard._active_builder_count(self.home), 4)

        # 5th builder in a Cargo workspace (including via target cwd when caller cwd is plain_dir, R7) -> denied (cap 4).
        out_cap = self.decide("mcp__t3-code__delegate_task", {
            "task": "Implement #5", "isolation": "worktree", "role": "implementation",
            "cwd": str(cargo_dir), "target": {"model": "claude-sonnet-5-5"},
        }, cwd=str(plain_dir))
        self.assertEqual(out_cap.get("permissionDecision"), "deny")
        self.assertIn("cap 4", out_cap.get("permissionDecisionReason", ""))

        # Read-only reviewers (delegate_task, t3_thread_launch, create_threads, R8) -> ALLOWED even at cap 4.
        for tool, inp in (
            ("mcp__t3-code__delegate_task", {"task": "Review only. Audit #1.", "role": "review", "target": {"model": "gemini-4-neon"}}),
            ("mcp__t3-code__t3_thread_launch", {"title": "Audit #1", "message": "Independent review of #1."}),
            ("mcp__t3-code__create_threads", {"threads": [{"title": "Review #1", "prompt": "Independent review", "role": "review"}]}),
            ("mcp__t3-code__create_threads", {"threads": [{"message": "Independent review of #1."}]}),
        ):
            self.assertEqual(self.decide(tool, inp, cwd=str(cargo_dir)), {}, tool)

        # Non-compiled workspace has cap 6 -> 5th builder allowed, batch of 3 denied (4 + 3 > 6).
        self.assertEqual(self.decide("mcp__t3-code__delegate_task", {
            "task": "Edit docs #5", "isolation": "worktree", "target": {"model": "claude-sonnet-5-5"},
        }, cwd=str(plain_dir)), {})
        out_batch = self.decide("mcp__t3-code__create_threads", {
            "threads": [{"prompt": "a"}, {"prompt": "b"}, {"prompt": "c"}],
        }, cwd=str(plain_dir))
        self.assertEqual(out_batch.get("permissionDecision"), "deny")
        self.assertIn("cap 6", out_batch.get("permissionDecisionReason", ""))

        # Low host RAM / disk / workspace mount / /dev/shm blocks builders & native worktree Agent (R9); critical RAM blocks reviews.
        for tool, bad_res, snippet, is_review, is_worktree in (
            ("mcp__t3-code__delegate_task", {"mem_avail_gib": 5.2, "root_free_gib": 50.0, "target_free_gib": 50.0, "shm_free_gib": 20.0, "shm_total_gib": 46.0}, "Low host RAM", False, False),
            ("mcp__t3-code__delegate_task", {"mem_avail_gib": 30.0, "root_free_gib": 3.1, "target_free_gib": 50.0, "shm_free_gib": 20.0, "shm_total_gib": 46.0}, "Low root disk", False, False),
            ("mcp__t3-code__delegate_task", {"mem_avail_gib": 30.0, "root_free_gib": 50.0, "target_free_gib": 2.5, "shm_free_gib": 20.0, "shm_total_gib": 46.0}, "Low workspace disk", False, False),
            ("mcp__t3-code__delegate_task", {"mem_avail_gib": 30.0, "root_free_gib": 50.0, "target_free_gib": 50.0, "shm_free_gib": 1.8, "shm_total_gib": 46.0}, "Low /dev/shm", False, False),
            ("mcp__t3-code__delegate_task", {"mem_avail_gib": 1.9, "root_free_gib": 50.0, "target_free_gib": 50.0, "shm_free_gib": 20.0, "shm_total_gib": 46.0}, "Critically low host RAM", True, False),
            ("Agent", {"mem_avail_gib": 5.0, "root_free_gib": 50.0, "target_free_gib": 50.0, "shm_free_gib": 20.0, "shm_total_gib": 46.0}, "Low host RAM", False, True),
        ):
            inp = (
                {"prompt": "build in wt", "subagent_type": "general-purpose", "isolation": "worktree"}
                if tool == "Agent"
                else {"task": "Review only." if is_review else "Build #9",
                      "role": "review" if is_review else "implementation",
                      "target": {"model": "gemini-4-neon"}}
            )
            payload = {"hook_event_name": "PreToolUse", "cwd": str(plain_dir), "tool_name": tool, "tool_input": inp}
            with patch.object(Path, "home", return_value=self.home), \
                 patch.object(model_guard, "routing", None), \
                 patch.object(model_guard, "_active_builder_count", return_value=0), \
                 patch.object(model_guard, "_host_resources", return_value=bad_res):
                out = native.dispatch("claude", payload).get("hookSpecificOutput", {})
            self.assertEqual(out.get("permissionDecision"), "deny", (tool, snippet))
            self.assertIn(snippet, out.get("permissionDecisionReason", ""))


class NativeHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shared-hooks-")
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def payload(self, event="SessionStart", **kwargs):
        return {"hook_event_name": event, "session_id": "fixture-session", "source": "startup",
                "cwd": str(self.directory), **kwargs}

    @needs_rtk
    def test_codex_rewrite_preserves_all_tool_arguments(self):
        payload = self.payload("PreToolUse", tool_name="Bash",
                               tool_input={"command": "git status", "timeout_ms": 2000, "description": "fixture"})
        output = behavior.rewrite_shell(payload)["hookSpecificOutput"]
        self.assertEqual(output["updatedInput"], {**payload["tool_input"], "command": "rtk git status"})
        self.assertEqual(output["permissionDecision"], "allow")

    def test_rtk_prefix_is_not_rewritten_again(self):
        with patch.object(behavior.subprocess, "run") as run:
            for command in ("rtk git status", " rtk"):
                self.assertEqual(behavior.rewrite_shell(self.payload(
                    "PreToolUse", tool_name="Bash", tool_input={"command": command})), {})
            run.assert_not_called()

    def test_missing_rtk_leaves_the_command_alone(self):
        with patch.object(behavior.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(behavior.rewrite_shell(self.payload(
                "PreToolUse", tool_name="Bash", tool_input={"command": "git status"})), {})

    @needs_rtk
    def test_non_shell_and_unsupported_commands_are_untouched(self):
        for tool, command in (("apply_patch", "git status"), ("Bash", "this_command_has_no_rtk_equivalent")):
            self.assertEqual(behavior.rewrite_shell(self.payload(
                "PreToolUse", tool_name=tool, tool_input={"command": command})), {})

    @needs_rtk
    def test_claude_existing_rtk_contract(self):
        result = subprocess.run(["rtk", "hook", "claude"], check=False, input=json.dumps(self.payload(
            "PreToolUse", tool_name="Bash", tool_input={"command": "git status"})),
            capture_output=True, text=True, encoding="utf-8", timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"], "rtk git status")

    def test_claude_adapter_does_not_add_second_rtk_pass(self):
        with patch.object(behavior, "rewrite_shell") as rewrite:
            self.assertEqual(native.dispatch("claude", self.payload("PreToolUse")), {})
            rewrite.assert_not_called()

    def test_start_resume_compact_and_fork_context(self):
        with patch.object(native, "ROOT", self.directory), patch.object(behavior, "design_hook", side_effect=lambda *_: {}), \
             usage(startup=lambda cwd: "native fixture usage"):
            for provider in ("claude", "codex"):
                for source in ("startup", "resume", "clear", "compact", "fork"):
                    with self.subTest(provider=provider, source=source):
                        output = native.dispatch(provider, self.payload(source=source))["hookSpecificOutput"]
                        self.assertEqual(output["hookEventName"], "SessionStart")
                        self.assertEqual(output["additionalContext"], "native fixture usage")

    def test_wpo_session_skips_budget_only(self):
        with patch.dict(os.environ, {"WPO_SESSION": "fixture"}), patch.object(native, "ROOT", self.directory), \
             patch.object(behavior, "design_hook", return_value={}) as design, \
             patch.object(native, "budget", types.SimpleNamespace(startup=unittest.mock.Mock())) as stand_in:
            native.dispatch("codex", self.payload())
            design.assert_called_once()
            stand_in.startup.assert_not_called()

    def test_hooks_work_without_the_private_usage_module(self):
        with patch.object(native, "ROOT", self.directory), patch.object(behavior, "design_hook", return_value={}), \
             patch.object(native, "budget", None):
            self.assertEqual(native.dispatch("claude", self.payload()), {})

    def test_design_failure_preserves_usage_context(self):
        with patch.object(native, "ROOT", self.directory), patch.object(behavior, "design_hook", side_effect=OSError), \
             usage(startup=lambda cwd: "usage survives"):
            output = native.dispatch("codex", self.payload())
            self.assertEqual(output["hookSpecificOutput"]["additionalContext"], "usage survives")

    def test_usage_failure_preserves_design_and_gives_agent_a_refresh_command(self):
        for provider in ("claude", "codex"):
            with patch.object(native, "ROOT", self.directory), \
                 patch.object(behavior, "design_hook", return_value={"hookSpecificOutput": {
                     "hookEventName": "SessionStart", "additionalContext": "design context"}}), \
                 usage(startup=unittest.mock.Mock(side_effect=OSError("PRIVATE"))):
                output = native.dispatch(provider, self.payload())["hookSpecificOutput"]
                self.assertTrue(output["additionalContext"].startswith("design context\n"))
                self.assertIn("UNKNOWN", output["additionalContext"])
                self.assertIn("python3 -I -B ~/.agents/usage/budget.py --refresh", output["additionalContext"])
                self.assertNotIn("PRIVATE", json.dumps(output))

    def test_project_design_hook_takes_precedence_for_each_provider(self):
        for provider, relative in (("claude", ".claude/settings.local.json"), ("codex", ".codex/hooks.json")):
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [
                {"type": "command", "command": "impeccable hook"}]}]}}))
            payload = self.payload("PostToolUse", tool_name="apply_patch")
            self.assertTrue(behavior.matching_project_design_hook(provider, payload, self.directory / "other-home"))
            with patch.object(behavior, "matching_project_design_hook", return_value=True), \
                 patch.object(behavior.subprocess, "run") as run:
                self.assertEqual(behavior.design_hook(provider, payload, ROOT), {})
                run.assert_not_called()

    def test_nonmatching_project_hook_does_not_suppress_global(self):
        path = self.directory / ".codex/hooks.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
            {"command": "impeccable hook"}]}]}}))
        self.assertFalse(behavior.matching_project_design_hook("codex", self.payload(
            "PostToolUse", tool_name="apply_patch"), self.directory / "other-home"))

    def test_missing_engine_has_explicit_manual_fallback(self):
        with patch.object(behavior.shutil, "which", return_value=None), \
             patch.dict(os.environ, {"IMPECCABLE_BIN": ""}):
            result = behavior.design_hook("codex", self.payload(), self.directory)
        self.assertIn("manual detector", result["systemMessage"])

    def test_engine_resolution_order(self):
        skill = self.directory / "skills" / "impeccable"
        sibling = skill / "scripts" / "bin" / "linux-x64" / "impeccable"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("#!/bin/sh\n")
        sibling.chmod(0o755)
        override = self.directory / "override"
        override.write_text("#!/bin/sh\n")
        override.chmod(0o755)
        with patch.object(behavior.shutil, "which", return_value="/usr/bin/impeccable"), \
             patch("platform.machine", return_value="x86_64"):
            with patch.dict(os.environ, {"IMPECCABLE_BIN": str(override)}):
                self.assertEqual(behavior.impeccable_engine(skill), override)
            with patch.dict(os.environ, {"IMPECCABLE_BIN": ""}):
                self.assertEqual(behavior.impeccable_engine(skill), sibling)
            sibling.unlink()
            with patch.dict(os.environ, {"IMPECCABLE_BIN": ""}):
                self.assertEqual(behavior.impeccable_engine(skill), Path("/usr/bin/impeccable"))

    @needs_engine
    def test_real_engine_edit_apply_patch_stop_and_dedup(self):
        isolated_home = patch.dict(os.environ, {"HOME": str(self.directory)})
        isolated_home.start()
        self.addCleanup(isolated_home.stop)
        file = self.directory / "view.html"
        file.write_text('<div style="background:linear-gradient(red,blue);background-clip:text;color:transparent">Hello</div>')
        for tool in ("Write", "apply_patch"):
            payload = self.payload("PostToolUse", session_id=f"fixture-{tool}", tool_name=tool,
                                   tool_input={"file_path": str(file)} if tool == "Write" else {
                                       "command": f"*** Begin Patch\n*** Update File: {file}\n@@\n+fixture\n*** End Patch"})
            result = behavior.design_hook("claude" if tool == "Write" else "codex", payload, ROOT)
            self.assertIn("gradient-text", result["hookSpecificOutput"]["additionalContext"])
            stop = behavior.design_hook("codex", self.payload("Stop", session_id=f"fixture-{tool}", stop_hook_active=False), ROOT)
            self.assertNotIn("gradient-text", json.dumps(stop))
            again = behavior.design_hook("codex", self.payload("Stop", session_id=f"fixture-{tool}", stop_hook_active=True), ROOT)
            self.assertEqual(again, {})

    def test_registration_command_runs_under_sh(self):
        command = register.python_command(Path.home(), "hooks/native.py", "claude")
        self.assertEqual(command, 'python3 -I -B "$HOME/.agents/hooks/native.py" claude')
        payload = self.payload("PreToolUse", tool_name="Bash", tool_input={"command": "git status"})
        home = self.directory / "home"
        (home / ".agents/hooks").mkdir(parents=True)
        for name in ("native.py", "behavior.py", "model_guard.py", "vcs_guard.py", "fix_route.py"):
            shutil.copy2(ROOT / "hooks" / name, home / ".agents/hooks" / name)
        result = subprocess.run(["sh", "-c", command], check=False, input=json.dumps(payload), capture_output=True,
                                text=True, timeout=8, env={**os.environ, "HOME": str(home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})  # git status is allowed; Claude does not rewrite it

    def test_malformed_cli_input_does_not_echo_sensitive_data(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "hooks/native.py"), "codex"],
                                check=False, input="secret-fixture-not-json", capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("secret-fixture", result.stdout + result.stderr)
        self.assertIn("systemMessage", json.loads(result.stdout))

    def test_local_markers_store_no_prompts_arguments_or_replies(self):
        payload = self.payload("Stop", last_assistant_message="PRIVATE", tool_input={"command": "PRIVATE"}, prompt="PRIVATE")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: behavior.record_marker("codex", payload, self.directory), range(12)))
        files = list(self.directory.glob("markers/*/*.json"))
        self.assertEqual(len(files), 12)
        for file in files:
            self.assertNotIn("PRIVATE", file.read_text())
            self.assertNotIn("fixture-session", file.read_text())
        summary = journal.summarize(self.directory)
        self.assertEqual(summary["sessions"][0]["events"], {"Stop": 12})
        self.assertNotIn("PRIVATE", json.dumps(summary))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hook-registration-")
        self.home = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_registered_commands_are_portable_and_matcher_is_bash_only(self):
        for provider in ("claude", "codex"):
            handlers = register.handlers(Path.home(), provider)
            commands = {h["command"] for groups in handlers.values() for g in groups for h in g["hooks"]}
            self.assertEqual(commands, {f'python3 -I -B "$HOME/.agents/hooks/native.py" {provider} || true'})
        for provider in ("claude", "codex"):
            pre_tool = register.handlers(Path.home(), provider)["PreToolUse"][0]
            self.assertEqual(pre_tool["matcher"], "^Bash$")
            self.assertEqual(pre_tool["hooks"][0]["timeout"], 5)
            self.assertEqual(register.handlers(Path.home(), provider)["PreToolUse"][1]["matcher"],
                             model_guard.MATCHER)

    def test_missing_hook_script_passes_through_for_claude_and_codex(self):
        for provider in ("claude", "codex"):
            command = register.handlers(Path.home(), provider)["PreToolUse"][0]["hooks"][0]["command"]
            result = subprocess.run(["sh", "-c", command], check=False, input="{}", capture_output=True, text=True,
                                    timeout=10, env={**os.environ, "HOME": str(self.home)})
            self.assertEqual((result.returncode, result.stdout), (0, ""), provider)

    def test_hand_merged_home_relative_registration_is_recognised_as_owned(self):
        home = self.home
        config = {"hooks": {event: groups for event, groups in register.handlers(Path.home(), "claude").items()}}
        self.assertEqual(register.owned_view(config, home, "claude").keys(), config["hooks"].keys())
        self.assertEqual(register.generated(config, home, "claude")["hooks"].keys(), config["hooks"].keys())

    def test_statusline_is_registered_only_when_the_usage_script_exists(self):
        self.assertNotIn("statusLine", register.generated({}, self.home, "claude"))
        (self.home / ".agents/usage").mkdir(parents=True)
        (self.home / ".agents/usage/budget.py").write_text("")
        status = register.generated({}, self.home, "claude")["statusLine"]
        self.assertTrue(status["command"].endswith('/.agents/usage/budget.py" --claude-statusline'))

    def test_legacy_usage_hook_is_recognized_with_any_python(self):
        for python in ("python3", "/usr/bin/python3", "C:/Python311/python.exe"):
            command = f"{python} -I {self.home.as_posix()}/.claude/hooks/usage_budget.py --hook"
            self.assertTrue(register.legacy_handler({"command": command}, self.home))

    def test_legacy_impeccable_guard_is_recognized_narrowly(self):
        path = (self.home / ".claude/skills/impeccable/scripts/impeccable").as_posix()
        handler = {"command": f'[ ! -f "{path}" ] || "{path}" hook'}
        self.assertTrue(register.legacy_handler(handler, self.home))
        self.assertFalse(register.legacy_handler({"command": handler["command"] + "; unrelated"}, self.home))

    def test_preserves_rtk_native_settings_and_other_hooks(self):
        rtk = {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
        original = {"env": {"KEEP": "yes"}, "model": "unchanged", "hooks": {"PreToolUse": [rtk],
                    "Stop": [{"hooks": [{"command": "other checker"}]}]}, "enabledPlugins": {"delta@zed-industries": True}}
        desired = register.generated(original, self.home, "claude")
        self.assertEqual(desired["hooks"]["PreToolUse"][0], rtk)
        guard = desired["hooks"]["PreToolUse"][1]
        self.assertEqual(guard["matcher"], "^Bash$")
        self.assertEqual(guard["hooks"][0]["timeout"], 5)
        self.assertIn('hooks/native.py" claude', guard["hooks"][0]["command"])
        self.assertEqual(desired["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
        for key in ("env", "model", "enabledPlugins"):
            self.assertEqual(desired[key], original[key])
        self.assertEqual(register.generated(desired, self.home, "claude"), desired)

    def test_statusline_collision_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            register.generated({"statusLine": {"command": "user-owned"}}, self.home, "claude")

    def test_backup_idempotence_and_drift_refusal(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir()
        original = b'{"theme":"fixture"}'
        path.write_bytes(original)
        self.assertEqual(register.synchronize(self.home), ["claude", "codex"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(register.synchronize(self.home, apply=True), ["claude", "codex"])
        backups = list((self.home / ".agents/hooks/backups").glob("*/claude.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(register.synchronize(self.home), [])
        config = json.loads(path.read_text())
        config["hooks"]["Stop"][-1]["hooks"][0]["timeout"] = 1
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "drift"):
            register.synchronize(self.home, apply=True)

    def test_codex_mcp_file_is_never_touched(self):
        path = self.home / ".codex/config.toml"
        path.parent.mkdir()
        path.write_bytes(b"# unrelated native config\n")
        register.synchronize(self.home, apply=True)
        self.assertEqual(path.read_bytes(), b"# unrelated native config\n")


if __name__ == "__main__":
    unittest.main()
