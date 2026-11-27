"""Factor-derived efficiency guards for subagent, delegate, and thread launches.

Enforces model-agnostic checks without hardcoding model version slugs:
1. Default-deny delegated child/subagent sessions from spawning nested subagents unless explicitly authorized.
2. Block review/council delegation prompts that leak a numeric target score / pass threshold.
3. Block `t3_thread_send` for reviews/re-reviews or recycling delegated threads onto new tasks.
4. Block Claude `effort="max"` / `"ultrathink"` unless explicitly opted in with `[max-effort: <reason>]`.
5. Enforce active worktree/builder concurrency caps and host RAM/disk/`/dev/shm` headroom before launching builders.
6. Block Claude native transcript forks when the parent context exceeds 150k tokens.
7. Block Antigravity subagents from inheriting a non-Gemini third-party (`3p`) pool model.
8. Require an explicit `[ultra: <reason>]` opt-in when delegating to an ultra-tier model
   (blended token rate >= 3x the provider's standard tier in live local rate metadata).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.abspath(__file__)).parent.parent
if str(ROOT / "usage") not in sys.path:
    sys.path.insert(0, str(ROOT / "usage"))

try:
    import routing  # ships in the private usage module; standalone fallback reads T3 rates directly
except ImportError:
    routing = None

FORK_CONTEXT_LIMIT = 150_000
ULTRA_COST_RATIO = 3.0
DEFAULT_COMPILED_MAX_BUILDERS = 4
DEFAULT_MAX_BUILDERS = 6
MIN_BUILDER_MEM_GIB = 8.0
MIN_ANY_MEM_GIB = 3.0
MIN_ROOT_DISK_GIB = 6.0
MIN_SHM_GIB = 4.0
MIN_SHM_TOTAL_GIB = 8.0
ACTIVE_RUN_MAX_IDLE_SECONDS = 12 * 3600
COMPILED_MARKERS = ("Cargo.toml", "CMakeLists.txt", "meson.build", "go.mod", "build.zig")
TRANSCRIPT_TAIL_BYTES = 131_072
TRANSCRIPT_HEAD_MAX_BYTES = 1024 * 1024
TRANSCRIPT_MAX_TAIL_BYTES = 4 * 1024 * 1024
ULTRA_OPT_IN = re.compile(r"\[ultra:\s*\S[^\]]*\]", re.IGNORECASE)
MAX_EFFORT_OPT_IN = re.compile(r"\[max-effort:\s*\S[^\]]*\]", re.IGNORECASE)
CLAUDE_MODEL_RE = re.compile(r"\b(?:claude|opus|sonnet|haiku|fable)\b", re.IGNORECASE)
CLAUDE_PROVIDER_RE = re.compile(r"claude|anthropic", re.IGNORECASE)
CLAUDE_FORBIDDEN_EFFORTS = {"max", "ultrathink"}
CLAUDE_MODEL_EFFORT_SUFFIX_RE = re.compile(r"-(max|ultrathink)$", re.IGNORECASE)
SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
T3_CHILD_PREFIX = re.compile(
    r'\[T3 Code:\s*the agent in T3 thread "[^"]+" sent this message,\s*not the user\.\]',
    re.IGNORECASE,
)
CHILD_EXPLICIT_NO_DELEGATE_RE = re.compile(
    r"\b(?:(?:do\s+not|don't|must\s+not|mustn't|never|cannot|can't|not\s+allowed\s+to)\s+"
    r"(?:delegate|spawn\s+subagents|start\s+subagents|launch\s+subagents|use\s+subagents)|"
    r"start\s+no\s+subagents|no\s+subagents)\b",
    re.IGNORECASE,
)
CHILD_ALLOW_DELEGATE_RE = re.compile(
    r"(?<!not\s)(?<!never\s)\b(?:you\s+may|authorized\s+to|explicitly\s+allowed\s+to|permitted\s+to)\s+"
    r"(?:delegate|spawn\s+subagents|start\s+subagents|launch\s+subagents)\b",
    re.IGNORECASE,
)
REVIEW_DELEGATION_RE = re.compile(
    r"\b(?:council|reviewer|adversary|rubric|independent\s+review|panel\s+council|code\s+council|"
    r"review\s+sub-?agent|adversarial\s+audit|read-only\s+audit)\b",
    re.IGNORECASE,
)
REVIEW_TITLE_RE = re.compile(
    r"^\s*(?:(?:re-?)?review\b|independent\s+(?:delta\s+)?review\b|adversar\w*\b|audit\b|council\b)",
    re.IGNORECASE,
)
REVIEW_SLUG_RE = re.compile(
    r"(?:^|[:/_-])(?:re-?)?review(?=$|[:/_-]|r?\d)",
    re.IGNORECASE,
)
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
REVIEW_SEND_MSG_RE = re.compile(
    r"(?:^\s*(?:please\s+)?(?:"
    r"re-?review\b|"
    r"review\s+(?:only\b|round\s*[1-9]\b|delta\b|pr\s*#?\d+\b|#\d+\b|this\b|commit\b|branch\b|diff\b)|"
    r"(?:quick\s+)?independent\s+(?:delta\s+)?review\b|"
    r"round\s*[1-9]\s+(?:delta\s+|final\s+)?(?:re-?)?review\s+(?:of|for|:|\()|"
    r"round\s*[2-9]\s+delta\s+verification\b|"
    r"act\s+as\s+(?:the\s+)?review\s+sub-?agent\b)|"
    r"\breview\s+only\.\s*do\s+not\s+edit\b)",
    re.IGNORECASE,
)
ISSUE_NUM_RE = re.compile(r"#(\d+)\b")
NEW_ISSUE_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:please|now|next|ok(?:ay)?|great)[,\s]+)*"
    r"(?:implement|build|work\s+on|fix|tackle|take\s+on)\s+(?:github\s+)?(?:issue\s+)?#(\d+)\b",
    re.IGNORECASE,
)
_SCORE_VAL = r"(?:[0-9](?:\.[0-9]+)?|10(?:\.0+)?)"
TARGET_SCORE_LEAK_RE = re.compile(
    rf"(?:"
    # Explicit score/rating/bar/threshold/target paired with a score value (with or without /10).
    rf"\b(?:(?:target|pass(?:ing)?|acceptance|consensus|minimum)\s+(?:score|rating|grade|bar|threshold)|"
    rf"acceptance\s+target|consensus\s+target|score\s+to\s+(?:beat|reach|hit))\b"
    rf"[^\n]{{0,30}}?(?:(?:>=|≥|at\s+least|of|is|=|:)\s*)?{_SCORE_VAL}(?:\s*/\s*10)?\b"
    rf"|"
    # Action phrasing ("until it reaches 9.0", "must score 9.5", "aim for 9.5/10").
    rf"\b(?:must\s+(?:score|reach|hit|exceed)|aim(?:ing)?\s+for|until\s+(?:it\s+)?reaches?)\b"
    rf"[^\n]{{0,30}}?(?:(?:>=|≥|at\s+least)\s*)?(?:{_SCORE_VAL}\s*/\s*10\b|[0-9]\.[0-9]+\b|10(?:\.0+)?\s*(?:/\s*10|points?|out\s+of\s+10)\b)"
    rf"|"
    # Explicit X/10 paired with target/consensus/threshold/pass bar on either side.
    rf"\b(?:target|consensus|threshold|pass(?:ing)?\s+(?:bar|score|target))\b[^\n]{{0,30}}?{_SCORE_VAL}\s*/\s*10\b"
    rf"|"
    rf"\b{_SCORE_VAL}\s*/\s*10\s+(?:target|consensus|threshold|pass(?:ing)?\s+(?:bar|score))\b"
    rf")",
    re.IGNORECASE,
)
EFFORT_SUFFIX = re.compile(r"-(?:low|medium|high|xhigh|max|ultra)$", re.IGNORECASE)
DATE_SUFFIX = re.compile(r"(?:-20\d{2}(?:-?\d{2}){0,2}|-(?:0\d|1[0-2])\d{2})(?=$|[-@])")
NON_CANONICAL_PREFIX = re.compile(
    r"^(?:us|eu|apac|au|jp|us-gov|global|anthropic|openai|google|azure|bedrock|vertex_ai|"
    r"databricks|system\.ai|ft:|aihubmix|openrouter|vercel_ai_gateway|zerogpu)[./:-]",
    re.IGNORECASE,
)
FIRST_PARTY_PREFIX = re.compile(r"^(?:anthropic|openai|gemini|vertex_ai|xai)/", re.IGNORECASE)
SPECIALTY_SUFFIX = re.compile(
    r"(?:^|-)(?:tts|audio|image|embedding|live|transcribe|translate|robotics|decision|"
    r"cyber|experimental|exp|nano-banana|omni|search)(?=$|-)",
    re.IGNORECASE,
)

# Claude Agent/Task, Codex spawn_agent, Antigravity invoke_subagent, and T3 delegation/thread tools.
MATCHER = (
    r"^(Agent|Task|spawn_agent|invoke_subagent|"
    r"mcp__t3[-_]code__(?:delegate_task|t3_thread_launch|create_threads|t3_thread_send|t3_thread_configure))$"
)
DELEGATION_TOOLS = {
    "Agent",
    "Task",
    "spawn_agent",
    "invoke_subagent",
    "mcp__t3-code__delegate_task",
    "mcp__t3_code__delegate_task",
    "mcp__t3-code__t3_thread_launch",
    "mcp__t3_code__t3_thread_launch",
    "mcp__t3-code__create_threads",
    "mcp__t3_code__create_threads",
}
THREAD_SEND_TOOLS = {
    "mcp__t3-code__t3_thread_send",
    "mcp__t3_code__t3_thread_send",
}
THREAD_CONFIGURE_TOOLS = {
    "mcp__t3-code__t3_thread_configure",
    "mcp__t3_code__t3_thread_configure",
}
T3_LAUNCH_TOOLS = {
    "mcp__t3-code__delegate_task",
    "mcp__t3_code__delegate_task",
    "mcp__t3-code__t3_thread_launch",
    "mcp__t3_code__t3_thread_launch",
    "mcp__t3-code__create_threads",
    "mcp__t3_code__create_threads",
}
TOOLS = DELEGATION_TOOLS | THREAD_SEND_TOOLS | THREAD_CONFIGURE_TOOLS


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _extract_models(value: object) -> list[str]:
    models: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == "model" and isinstance(item, str) and item.strip():
                models.append(item.strip())
            else:
                models.extend(_extract_models(item))
    elif isinstance(value, list):
        for item in value:
            models.extend(_extract_models(item))
    return models


def _has_ultra_opt_in(value: object) -> bool:
    if isinstance(value, str):
        return bool(ULTRA_OPT_IN.search(value))
    if isinstance(value, dict):
        return any(_has_ultra_opt_in(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_ultra_opt_in(item) for item in value)
    return False


def _latest_claude_context_tokens(transcript_path: object) -> int | None:
    """Scan backwards from the tail of a Claude JSONL transcript for the latest assistant input context."""
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return None
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        if size <= 0:
            return None
        floor = max(0, size - TRANSCRIPT_MAX_TAIL_BYTES)
        pos = size
        step = TRANSCRIPT_TAIL_BYTES
        carry = b""
        with path.open("rb") as stream:
            while pos > floor:
                start = max(floor, pos - step)
                stream.seek(start)
                buf = stream.read(pos - start) + carry
                pos = start
                step = min(step * 2, TRANSCRIPT_MAX_TAIL_BYTES)
                lines = buf.splitlines()
                if pos > 0 and lines:
                    carry = lines.pop(0)  # incomplete head line; prepend to previous chunk
                else:
                    carry = b""
                for raw_line in reversed(lines):
                    if b'"usage"' not in raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except ValueError:
                        continue
                    if not isinstance(entry, dict) or entry.get("type") != "assistant":
                        continue
                    message = entry.get("message")
                    usage = message.get("usage") if isinstance(message, dict) else None
                    if not isinstance(usage, dict):
                        continue
                    total = 0
                    for field in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                        val = usage.get(field)
                        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                            total += val
                    if total > 0:
                        return total
    except OSError:
        return None
    return None


def _standalone_tier_info(model_name: str, home: Path) -> dict | None:
    """Fallback rate-ratio check when the private routing module is absent."""
    rates_path = home / ".t3/userdata/usage-model-rates.json"
    try:
        raw = json.loads(rates_path.read_text(encoding="utf-8")).get("document")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(raw, dict):
        return None

    def blended(entry: dict) -> float | None:
        inp = entry.get("input_cost_per_token")
        out = entry.get("output_cost_per_token")
        if isinstance(inp, bool) or isinstance(out, bool) or not isinstance(inp, (int, float)) or not isinstance(out, (int, float)):
            return None
        cr = entry.get("cache_read_input_token_cost")
        cw = entry.get("cache_creation_input_token_cost")
        cr_f = float(cr) if isinstance(cr, (int, float)) and not isinstance(cr, bool) else float(inp) * 0.1
        cw_f = float(cw) if isinstance(cw, (int, float)) and not isinstance(cw, bool) else float(inp) * 1.25
        return (0.65 * cr_f + 0.10 * cw_f + 0.05 * float(inp) + 0.20 * float(out)) * 1e6

    rates: dict[str, tuple[float, int]] = {}
    priority: dict[str, int] = {}
    for key, entry in raw.items():
        if key == "sample_spec" or not isinstance(entry, dict):
            continue
        val = blended(entry)
        if val is None or val <= 0:
            continue
        short = key.split("/")[-1]
        short_low = short.lower()
        ctx_raw = entry.get("max_input_tokens")
        ctx = int(ctx_raw) if isinstance(ctx_raw, (int, float)) and not isinstance(ctx_raw, bool) and ctx_raw > 0 else 200_000
        if key == short and not NON_CANONICAL_PREFIX.search(key):
            rank = 3
        elif FIRST_PARTY_PREFIX.search(key) and not NON_CANONICAL_PREFIX.search(short):
            rank = 2
        elif not NON_CANONICAL_PREFIX.search(short):
            rank = 1
        else:
            rank = 0
        if rank > priority.get(short_low, -1):
            rates[short_low] = (val, ctx)
            priority[short_low] = rank

    cleaned = model_name.strip().split("/")[-1].lower()
    base = EFFORT_SUFFIX.sub("", cleaned)
    matched = rates.get(cleaned) or rates.get(base)
    if matched is None:
        cands = []
        for k, v in rates.items():
            if k.startswith(base + "-") or k.startswith(base + ".") or f"-{base}-" in k or k.endswith(f"-{base}"):
                is_canon = 0 if NON_CANONICAL_PREFIX.search(k) or "@" in k else 1
                is_general = 0 if SPECIALTY_SUFFIX.search(k) else 1
                ver = tuple(int(n) for n in re.findall(r"\d+", DATE_SUFFIX.sub("", k))) or (0,)
                cands.append((is_canon, is_general, ver, -len(k), k, v))
        if cands:
            cands.sort(reverse=True)
            matched = cands[0][5]
    if matched is None:
        return None
    rate_val, ctx_window = matched
    ratio = rate_val / 2.42  # standard fresh_rev blended anchor (~$2/$10 tier)
    return {
        "model": model_name.strip(),
        "cost_ratio": round(ratio, 2),
        "ctx_window": ctx_window,
        "is_ultra": ratio >= ULTRA_COST_RATIO,
        "recommended": None,
    }


def _model_tier_info(model_name: str, home: Path | None = None) -> dict | None:
    root_home = Path.home() if home is None else home
    if routing is not None:
        try:
            return routing.model_tier_info(model_name, home=root_home)
        except Exception:
            pass
    return _standalone_tier_info(model_name, root_home)


def _check_claude_fork(payload: dict) -> dict | None:
    if payload.get("tool_name") not in ("Agent", "Task"):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    subagent_type = tool_input.get("subagent_type")
    is_fork = not isinstance(subagent_type, str) or not subagent_type.strip() or subagent_type.strip().lower() == "fork"
    if not is_fork:
        return None
    ctx = _latest_claude_context_tokens(payload.get("transcript_path"))
    if ctx is not None and ctx > FORK_CONTEXT_LIMIT:
        return _deny(
            f"Parent context is {ctx // 1000}k tokens (>{FORK_CONTEXT_LIMIT // 1000}k); "
            "forking clones the full transcript into the subagent. Pass an explicit non-fork "
            "subagent_type (e.g. general-purpose, Explore, reviewer, verifier) with a self-contained brief instead."
        )
    return None


def _check_antigravity_3p_inherit(payload: dict) -> dict | None:
    if payload.get("tool_name") != "invoke_subagent":
        return None
    parent_model = payload.get("model_name")
    if not isinstance(parent_model, str) or not parent_model.strip():
        return None
    if parent_model.strip().lower().startswith("gemini"):
        return None
    tool_input = payload.get("tool_input")
    requested = None
    if isinstance(tool_input, dict):
        for key, val in tool_input.items():
            if key.lower() == "model" and isinstance(val, str):
                requested = val.strip().lower()
                break
    if not requested or requested == "inherit":
        return _deny(
            f"Parent runs on Antigravity's scarce 3p pool ({parent_model.strip()}); do not inherit it "
            "into a subagent. Pass an explicit Model (e.g. flash or a Gemini model) or delegate to the native provider."
        )
    return None


def _check_ultra_tier(payload: dict, home: Path | None = None) -> dict | None:
    tool_input = payload.get("tool_input")
    models = _extract_models(tool_input)
    if not models or _has_ultra_opt_in(tool_input):
        return None
    for model in models:
        if model.lower() in ("inherit", "default"):
            continue
        info = _model_tier_info(model, home=home)
        if not info or not info.get("is_ultra"):
            continue
        ratio = info.get("cost_ratio", ULTRA_COST_RATIO)
        ctx = info.get("ctx_window")
        ctx_note = f" with a {ctx // 1000}k context window" if isinstance(ctx, int) and 0 < ctx < 350_000 else ""
        rec = info.get("recommended") if isinstance(info.get("recommended"), dict) else None
        rec_bits = []
        if rec:
            if rec.get("impl"):
                rec_bits.append(f"impl={','.join(rec['impl'])}")
            if rec.get("review"):
                rec_bits.append(f"review={','.join(rec['review'])}")
        rec_note = f" (live picks: {'; '.join(rec_bits)})" if rec_bits else ""
        return _deny(
            f"{model} is ~{ratio:.1f}x standard token cost{ctx_note}{rec_note}. "
            "Use a standard/flagship pick for routine work, or re-invoke with "
            "[ultra: <short reason>] in the task/prompt if this genuinely needs ultra-tier judgment."
        )
    return None


def _extract_prompt_texts(value: object) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            kl = key.lower()
            if kl in ("task", "prompt", "brief", "message", "title", "role", "subagent_type", "typename") and isinstance(item, str) and item.strip():
                texts.append(item)
            elif isinstance(item, (dict, list)):
                texts.extend(_extract_prompt_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_extract_prompt_texts(item))
    return texts


def _strip_t3_boilerplate(text: str) -> str:
    return re.sub(r"<t3_code_instructions>.*?</t3_code_instructions>", "", text, flags=re.S | re.I)


def _child_task_body(payload: dict, home: Path | None = None) -> tuple[bool, str]:
    """Return `(is_child_session, task_text)` for delegated T3 children and native subagents."""
    is_native_sub = bool(payload.get("subagentType"))
    tpath_raw = payload.get("transcript_path")
    tpath = Path(tpath_raw) if isinstance(tpath_raw, str) and tpath_raw.strip() else None
    if tpath is not None and "/subagents/" in tpath.as_posix():
        is_native_sub = True

    # Antigravity passes lastUserInput directly in PreToolUse payloads.
    last_in = payload.get("last_user_input")
    if isinstance(last_in, str) and last_in.strip():
        cleaned = _strip_t3_boilerplate(last_in)
        match = T3_CHILD_PREFIX.search(cleaned)
        if match:
            return True, cleaned[match.end():]
        if is_native_sub:
            return True, cleaned

    # Inspect head of transcript file (preferring sibling transcript_full.jsonl for Antigravity).
    candidates: list[Path] = []
    if tpath is not None:
        if tpath.name == "transcript.jsonl":
            full = tpath.with_name("transcript_full.jsonl")
            if full.is_file():
                candidates.append(full)
        if tpath.is_file():
            candidates.append(tpath)
    else:
        # Grok delegated sessions store their first user prompt in ~/.grok/sessions/*/<session_id>/chat_history.jsonl.
        sid = payload.get("session_id") or payload.get("sessionId")
        if isinstance(sid, str) and SAFE_SESSION_ID.fullmatch(sid):
            grok_sessions = (Path.home() if home is None else home) / ".grok" / "sessions"
            if grok_sessions.is_dir():
                for chat_path in grok_sessions.glob(f"*/{sid}/chat_history.jsonl"):
                    if chat_path.is_file():
                        candidates.append(chat_path)
                        break

    for path in candidates:
        user_chunks: list[str] = []
        bytes_read = 0
        try:
            with path.open("rb") as stream:
                for raw_line in stream:
                    bytes_read += len(raw_line)
                    try:
                        entry = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except ValueError:
                        if bytes_read >= TRANSCRIPT_HEAD_MAX_BYTES:
                            break
                        continue
                    if not isinstance(entry, dict):
                        continue
                    etype = entry.get("type")
                    found_chunks: list[str] = []
                    if etype == "user" or entry.get("role") == "user":
                        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
                        content = msg.get("content")
                        if isinstance(content, str):
                            found_chunks.append(content)
                        elif isinstance(content, list):
                            found_chunks.extend(
                                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("text")
                            )
                    elif etype == "USER_INPUT":
                        content = entry.get("content")
                        if isinstance(content, str):
                            found_chunks.append(content)
                    elif etype == "response_item":
                        pl = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                        if pl.get("role") == "user":
                            content = pl.get("content")
                            if isinstance(content, str):
                                found_chunks.append(content)
                            elif isinstance(content, list):
                                found_chunks.extend(
                                    str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("text")
                                )
                    elif etype == "session_meta":
                        pl = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                        if isinstance(pl.get("source"), dict) and "subagent" in pl["source"]:
                            is_native_sub = True
                    for chunk in found_chunks:
                        cleaned = _strip_t3_boilerplate(chunk)
                        match = T3_CHILD_PREFIX.search(cleaned)
                        if match:
                            return True, cleaned[match.end():]
                        user_chunks.append(cleaned)
                    if (is_native_sub and user_chunks) or bytes_read >= TRANSCRIPT_HEAD_MAX_BYTES:
                        break
        except OSError:
            continue
        if is_native_sub and user_chunks:
            return True, "\n".join(user_chunks)

    return is_native_sub, ""


def _unwrap_input(payload: dict) -> dict:
    """Return the innermost tool_input dict (unwrapping Grok's deferred-MCP wrapper)."""
    raw = payload.get("tool_input")
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("tool_input")
    if isinstance(nested, dict) and isinstance(raw.get("tool_name"), str):
        return nested
    return raw


def _has_max_effort_opt_in(value: object) -> bool:
    if isinstance(value, str):
        return bool(MAX_EFFORT_OPT_IN.search(value))
    if isinstance(value, dict):
        return any(_has_max_effort_opt_in(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_max_effort_opt_in(item) for item in value)
    return False


def _extract_effort_values(options: object) -> list[str]:
    efforts: list[str] = []
    if isinstance(options, dict):
        for key, val in options.items():
            if key.lower() in ("effort", "reasoningeffort", "reasoning_effort") and isinstance(val, str) and val.strip():
                efforts.append(val.strip().lower())
    elif isinstance(options, list):
        for entry in options:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("id")
            val = entry.get("value")
            if isinstance(eid, str) and eid.lower() in ("effort", "reasoningeffort", "reasoning_effort") and isinstance(val, str) and val.strip():
                efforts.append(val.strip().lower())
    return efforts


def _target_specs(
    value: object,
    inherited_providers: tuple[str, ...] = (),
    inherited_model: str = "",
) -> list[tuple[tuple[str, ...], str, list[str]]]:
    """Extract `(providers, model, efforts)` tuples while propagating outer/sibling target context."""
    specs: list[tuple[tuple[str, ...], str, list[str]]] = []
    if isinstance(value, dict):
        own_model = value.get("model").strip() if isinstance(value.get("model"), str) and value.get("model").strip() else ""
        own_providers = tuple(
            pval.strip()
            for pkey in ("providerInstanceId", "provider", "instanceId", "driverKind")
            if isinstance((pval := value.get(pkey)), str) and pval.strip()
        )
        # Sibling `target` or `modelSelection` supplies provider/model for sibling `options`/`effort`.
        sib_model = own_model
        sib_providers = list(own_providers)
        for container_key in ("target", "modelSelection"):
            sub = value.get(container_key)
            if isinstance(sub, dict):
                if not sib_model and isinstance(sub.get("model"), str) and sub["model"].strip():
                    sib_model = sub["model"].strip()
                for pkey in ("providerInstanceId", "provider", "instanceId", "driverKind"):
                    pval = sub.get(pkey)
                    if isinstance(pval, str) and pval.strip() and pval.strip() not in sib_providers:
                        sib_providers.append(pval.strip())

        providers = tuple(sib_providers) if sib_providers else inherited_providers
        if sib_model:
            model = sib_model
        elif sib_providers and tuple(sib_providers) != inherited_providers:
            model = ""
        else:
            model = inherited_model
        efforts = _extract_effort_values(value.get("options"))
        efforts.extend(_extract_effort_values(value))
        if model or providers or efforts:
            specs.append((providers, model, efforts))
        for key, item in value.items():
            if key in ("options",):
                continue
            if isinstance(item, (dict, list)):
                specs.extend(_target_specs(item, providers, model))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                specs.extend(_target_specs(item, inherited_providers, inherited_model))
    return specs


def _is_claude_target(providers: tuple[str, ...], model: str, *, default_claude: bool = False) -> bool:
    if any(CLAUDE_PROVIDER_RE.search(p) for p in providers):
        return True
    if model:
        return bool(CLAUDE_MODEL_RE.search(model))
    if providers:
        return False
    return default_claude


def _sqlite_ro_uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def _statev2_thread_info(thread_id: str, home: Path) -> tuple[str | None, bool, tuple[str, ...], str]:
    """Return `(title, is_delegated_child, providers, model)` from T3's read-only statev2 projection."""
    is_child = thread_id.startswith("thread:delegated-task:")
    db_path = home / ".t3/userdata/statev2.sqlite"
    if not db_path.is_file():
        return None, is_child, (), ""
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=0.5)
        try:
            row = conn.execute(
                "SELECT title, "
                "json_extract(payload_json, '$.lineage.relationshipToParent'), "
                "json_extract(payload_json, '$.lineage.parentThreadId'), "
                "json_extract(payload_json, '$.providerInstanceId'), "
                "json_extract(payload_json, '$.modelSelection.provider'), "
                "json_extract(payload_json, '$.modelSelection.model') "
                "FROM orchestration_v2_projection_threads WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None, is_child, (), ""
    if not row:
        return None, is_child, (), ""
    title = row[0] if isinstance(row[0], str) else None
    rel = row[1] if isinstance(row[1], str) else ""
    parent_id = row[2] if isinstance(row[2], str) and row[2].strip() else ""
    providers = tuple(v.strip() for v in (row[3], row[4]) if isinstance(v, str) and v.strip())
    model = row[5].strip() if isinstance(row[5], str) and row[5].strip() else ""
    return title, is_child or rel == "subagent" or bool(parent_id), providers, model


def _check_claude_max_effort_unit(
    unit: object,
    *,
    tool_name: object,
    inherited_providers: tuple[str, ...] = (),
    inherited_model: str = "",
    allow_opt_in: bool = True,
) -> dict | None:
    if allow_opt_in and _has_max_effort_opt_in(unit):
        return None
    default_claude = tool_name in ("Agent", "Task")
    for providers, model, efforts in _target_specs(unit, inherited_providers, inherited_model):
        if not _is_claude_target(providers, model, default_claude=default_claude):
            continue
        bad = [eff for eff in efforts if eff in CLAUDE_FORBIDDEN_EFFORTS]
        suffix_match = CLAUDE_MODEL_EFFORT_SUFFIX_RE.search(model)
        if suffix_match:
            bad.append(suffix_match.group(1).lower())
        if bad:
            chosen = bad[0]
            label = model or (providers[0] if providers else "Claude")
            opt_note = (
                " Only re-invoke with [max-effort: <short reason>] in the prompt if the user explicitly asked for max."
                if allow_opt_in
                else ""
            )
            return _deny(
                f"{label} effort='{chosen}' is blocked because 'max'/'ultrathink' often degrades quality/latency "
                f"vs 'xhigh' while burning excessive tokens. Use effort='xhigh' for hard tasks or effort='high' "
                f"for routine work.{opt_note}"
            )
    return None


def _check_claude_max_effort(payload: dict, home: Path | None = None) -> dict | None:
    tool_name = payload.get("tool_name")
    tool_input = _unwrap_input(payload)
    if not tool_input:
        return None

    inherited_providers: tuple[str, ...] = ()
    inherited_model = ""
    if tool_name in (THREAD_CONFIGURE_TOOLS | THREAD_SEND_TOOLS):
        tid = tool_input.get("threadId")
        if isinstance(tid, str) and tid.strip():
            root_home = Path.home() if home is None else home
            _, _, inherited_providers, inherited_model = _statev2_thread_info(tid.strip(), root_home)

    # In create_threads, scope [max-effort: ...] opt-in per thread item while propagating shared outer defaults.
    if tool_name in ("mcp__t3-code__create_threads", "mcp__t3_code__create_threads") and isinstance(tool_input.get("threads"), list):
        outer = {k: v for k, v in tool_input.items() if k != "threads"}
        outer_specs = _target_specs(outer, inherited_providers, inherited_model)
        outer_providers = outer_specs[0][0] if outer_specs else inherited_providers
        outer_model = outer_specs[0][1] if outer_specs else inherited_model
        for item in tool_input["threads"]:
            if not isinstance(item, dict):
                continue
            denied = _check_claude_max_effort_unit(
                item,
                tool_name=tool_name,
                inherited_providers=outer_providers,
                inherited_model=outer_model,
                allow_opt_in=True,
            )
            if denied:
                return denied
        return _check_claude_max_effort_unit(
            outer,
            tool_name=tool_name,
            inherited_providers=inherited_providers,
            inherited_model=inherited_model,
            allow_opt_in=False,
        )

    return _check_claude_max_effort_unit(
        tool_input,
        tool_name=tool_name,
        inherited_providers=inherited_providers,
        inherited_model=inherited_model,
        allow_opt_in=tool_name not in THREAD_CONFIGURE_TOOLS,
    )


def _check_thread_send(payload: dict, home: Path | None = None) -> dict | None:
    if payload.get("tool_name") not in THREAD_SEND_TOOLS:
        return None
    tool_input = _unwrap_input(payload)
    thread_id = tool_input.get("threadId")
    message = tool_input.get("message")
    if not isinstance(thread_id, str) or not thread_id.strip():
        return None
    msg = message if isinstance(message, str) else ""
    root_home = Path.home() if home is None else home
    title, is_child, _, _ = _statev2_thread_info(thread_id.strip(), root_home)

    is_review_thread = is_child and (
        bool(REVIEW_SLUG_RE.search(thread_id))
        or (title is not None and bool(REVIEW_TITLE_RE.search(title)))
    )
    if is_review_thread or (is_child and msg and REVIEW_SEND_MSG_RE.search(msg)):
        return _deny(
            "Every new delegate review or re-check must be a fresh delegate_task (with brief, ledger, and diff), "
            "never t3_thread_send. Launch a new delegate_task instead."
        )

    if is_child:
        if tool_input.get("freshContext") is True:
            return _deny(
                "Do not recycle a delegated child thread with freshContext=true via t3_thread_send. "
                "Launch a fresh delegate_task so thread IDs, titles, and worktrees stay cleanly scoped."
            )
        if title and msg:
            old_issues = set(ISSUE_NUM_RE.findall(title))
            new_match = NEW_ISSUE_ASSIGN_RE.search(msg)
            if old_issues and new_match:
                new_issue = new_match.group(1)
                if new_issue not in old_issues:
                    return _deny(
                        f"Do not repurpose delegated child thread ({title[:60]!r}) for a different issue "
                        f"(#{new_issue}) via t3_thread_send. Launch a fresh delegate_task instead."
                    )
    return None


def _is_compiled_workspace(cwd: object) -> bool:
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    start = Path(cwd)
    try:
        cur = start.resolve()
    except OSError:
        cur = start
    for directory in (cur, *cur.parents):
        if any((directory / marker).is_file() for marker in COMPILED_MARKERS):
            return True
        if (directory / ".git").exists():
            break
    return False


def _target_workspace_paths(tool_input: dict, fallback_cwd: object) -> list[str]:
    paths: list[str] = []

    def add(val: object) -> None:
        if isinstance(val, str) and val.strip() and val.strip() not in paths:
            paths.append(val.strip())

    def collect(obj: dict) -> None:
        for key in ("cwd", "projectPath", "worktreePath", "basePath"):
            add(obj.get(key))
        ws = obj.get("workspaceStrategy")
        if isinstance(ws, dict):
            add(ws.get("worktreePath"))
            add(ws.get("basePath"))

    collect(tool_input)
    threads = tool_input.get("threads")
    if isinstance(threads, list):
        for item in threads:
            if isinstance(item, dict):
                collect(item)
    if not paths:
        add(fallback_cwd)
    return paths


def _host_resources(target_paths: tuple[str, ...] = ()) -> dict[str, float | None]:
    """Read live MemAvailable and free GiB on `/`, target mounts, and `/dev/shm` without subprocesses."""
    mem_avail_gib: float | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isascii() and parts[1].isdigit():
                    mem_avail_gib = int(parts[1]) / (1024.0 * 1024.0)
                break
    except OSError:
        pass

    def free_and_total_gib(path: str) -> tuple[float | None, float | None]:
        try:
            st = os.statvfs(path)
            free = (st.f_bavail * st.f_frsize) / (1024.0 ** 3)
            total = (st.f_blocks * st.f_frsize) / (1024.0 ** 3)
            return free, total
        except OSError:
            return None, None

    root_free, _ = free_and_total_gib("/")
    target_free: float | None = None
    for tpath in target_paths:
        free, _ = free_and_total_gib(tpath)
        if free is not None and (target_free is None or free < target_free):
            target_free = free
    shm_free, shm_total = free_and_total_gib("/dev/shm")
    return {
        "mem_avail_gib": mem_avail_gib,
        "root_free_gib": root_free,
        "target_free_gib": target_free,
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


def _active_builder_count(home: Path, now_ts: float | None = None) -> int:
    """Count currently running/queued delegated builder threads in T3's statev2 projection."""
    db_path = home / ".t3/userdata/statev2.sqlite"
    if not db_path.is_file():
        return 0
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=0.5)
        try:
            rows = conn.execute(
                "SELECT t.thread_id, t.title, json_extract(t.payload_json, '$.worktreePath'), "
                "t.updated_at, r.requested_at "
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
        return 0
    now = datetime.now(timezone.utc).timestamp() if now_ts is None else now_ts
    by_thread: dict[str, tuple[str, str | None, float | None]] = {}
    for tid, title, wt, updated_at, requested_at in rows:
        tid_s = tid if isinstance(tid, str) else ""
        if not tid_s:
            continue
        ts_vals = [ts for ts in (_parse_iso_ts(updated_at), _parse_iso_ts(requested_at)) if ts is not None]
        latest_ts = max(ts_vals) if ts_vals else None
        prev = by_thread.get(tid_s)
        if prev is not None:
            prev_title, prev_wt, prev_ts = prev
            merged_ts = latest_ts if (latest_ts is not None and (prev_ts is None or latest_ts > prev_ts)) else prev_ts
            by_thread[tid_s] = (prev_title, prev_wt, merged_ts)
        else:
            wt_s = wt.strip() if isinstance(wt, str) and wt.strip() else None
            by_thread[tid_s] = (title if isinstance(title, str) else "", wt_s, latest_ts)
    count = 0
    for tid_s, (title_s, wt_s, latest_ts) in by_thread.items():
        if latest_ts is not None and (now - latest_ts) > ACTIVE_RUN_MAX_IDLE_SECONDS:
            continue
        if wt_s is not None:
            count += 1
            continue
        if NON_BUILDER_SLUG_RE.search(tid_s) or NON_BUILDER_TITLE_RE.search(title_s):
            continue
        count += 1
    return count


def _is_non_worktree_review_or_research(item: dict, text_keys: tuple[str, ...]) -> bool:
    role = str(item.get("role") or "").strip().lower()
    if role in ("review", "research"):
        return True
    title = str(item.get("title") or "")
    body = next((str(item.get(k)) for k in text_keys if isinstance(item.get(k), str) and item.get(k).strip()), "")
    return bool(REVIEW_TITLE_RE.search(title) or REVIEW_DELEGATION_RE.search(body[:400]))


def _requested_builder_count(tool_name: str, tool_input: dict) -> int:
    if tool_name in ("Agent", "Task"):
        return 1 if tool_input.get("isolation") == "worktree" else 0
    if tool_name in ("mcp__t3-code__delegate_task", "mcp__t3_code__delegate_task"):
        if tool_input.get("isolation") == "worktree":
            return 1
        return 0 if _is_non_worktree_review_or_research(tool_input, ("task",)) else 1
    if tool_name in ("mcp__t3-code__t3_thread_launch", "mcp__t3_code__t3_thread_launch"):
        ws = tool_input.get("workspaceStrategy")
        if isinstance(ws, dict) and ws.get("type") == "worktree":
            return 1
        msg = str(tool_input.get("message") or "")
        if not msg.strip() or _is_non_worktree_review_or_research(tool_input, ("message",)):
            return 0
        return 1
    if tool_name in ("mcp__t3-code__create_threads", "mcp__t3_code__create_threads"):
        threads = tool_input.get("threads")
        if not isinstance(threads, list):
            return 0
        count = 0
        for item in threads:
            if not isinstance(item, dict):
                continue
            ws = item.get("workspaceStrategy")
            if isinstance(ws, dict) and ws.get("type") == "worktree":
                count += 1
                continue
            if item.get("isolation") == "worktree":
                count += 1
                continue
            prompt = str(item.get("prompt") or item.get("message") or "")
            if not prompt.strip() or _is_non_worktree_review_or_research(item, ("prompt", "message")):
                continue
            count += 1
        return count
    return 0


def _check_concurrency_and_resources(payload: dict, home: Path | None = None) -> dict | None:
    tool_name = payload.get("tool_name")
    tool_input = _unwrap_input(payload)
    is_native_worktree = tool_name in ("Agent", "Task") and tool_input.get("isolation") == "worktree"
    if tool_name not in T3_LAUNCH_TOOLS and not is_native_worktree:
        return None
    requested = _requested_builder_count(str(tool_name), tool_input)
    root_home = Path.home() if home is None else home
    target_paths = tuple(_target_workspace_paths(tool_input, payload.get("cwd")))

    res = _host_resources(target_paths)
    mem_avail = res.get("mem_avail_gib")
    if mem_avail is not None and mem_avail < MIN_ANY_MEM_GIB:
        return _deny(
            f"Critically low host RAM ({mem_avail:.1f} GiB available < {MIN_ANY_MEM_GIB:.0f} GiB). "
            "Wait for active builds to finish or reclaim /dev/shm build directories before launching new agents."
        )

    if requested <= 0:
        return None

    if mem_avail is not None and mem_avail < MIN_BUILDER_MEM_GIB:
        return _deny(
            f"Low host RAM ({mem_avail:.1f} GiB available < {MIN_BUILDER_MEM_GIB:.0f} GiB) for a new builder. "
            "Wait for active builds to finish or clean /dev/shm and target/ build directories first."
        )
    root_free = res.get("root_free_gib")
    if root_free is not None and root_free < MIN_ROOT_DISK_GIB:
        return _deny(
            f"Low root disk space ({root_free:.1f} GiB free < {MIN_ROOT_DISK_GIB:.0f} GiB on /). "
            "Reclaim idle target/ or llvm-cov-target build directories before launching another builder."
        )
    target_free = res.get("target_free_gib")
    if target_free is not None and target_free < MIN_ROOT_DISK_GIB:
        return _deny(
            f"Low workspace disk space ({target_free:.1f} GiB free < {MIN_ROOT_DISK_GIB:.0f} GiB). "
            "Reclaim idle target/ or build artifacts before launching another builder."
        )
    shm_free = res.get("shm_free_gib")
    shm_total = res.get("shm_total_gib")
    if shm_free is not None and shm_total is not None and shm_total >= MIN_SHM_TOTAL_GIB and shm_free < MIN_SHM_GIB:
        return _deny(
            f"Low /dev/shm space ({shm_free:.1f} GiB free < {MIN_SHM_GIB:.0f} GiB). "
            "Delete stale /dev/shm build targets before launching another builder."
        )

    if is_native_worktree:
        return None

    cap_env = os.environ.get("AGENTS_MAX_BUILDERS", "").strip()
    if cap_env.isascii() and cap_env.isdigit() and int(cap_env) > 0:
        cap = int(cap_env)
    elif any(_is_compiled_workspace(p) for p in target_paths):
        cap = DEFAULT_COMPILED_MAX_BUILDERS
    else:
        cap = DEFAULT_MAX_BUILDERS

    active = _active_builder_count(root_home)
    if active + requested > cap:
        return _deny(
            f"Active builder concurrency cap reached ({active} active + {requested} requested > cap {cap}). "
            "Wait for an active builder to finish and clean its build directory before launching another."
        )
    return None


def _check_child_no_delegation(payload: dict, home: Path | None = None) -> dict | None:
    if payload.get("tool_name") not in DELEGATION_TOOLS:
        return None
    is_child, body = _child_task_body(payload, home=home)
    if not is_child:
        return None
    # Explicit prohibition always wins, even if the prompt mentions "subagents" in a negative clause.
    if body and CHILD_EXPLICIT_NO_DELEGATE_RE.search(body):
        return _deny(
            "This delegated child/council reviewer session must not spawn subagents or child tasks. "
            "Complete the work directly yourself."
        )
    if body and CHILD_ALLOW_DELEGATE_RE.search(body):
        return None
    return _deny(
        "Delegated child and subagent sessions must do the work themselves and must not spawn subagents "
        "unless the parent task explicitly authorizes delegation (e.g. 'you may delegate ...'). "
        "Complete this task directly yourself."
    )


def _check_review_target_leak(payload: dict) -> dict | None:
    texts = _extract_prompt_texts(payload.get("tool_input"))
    if not texts:
        return None
    combined = "\n".join(texts)
    if not REVIEW_DELEGATION_RE.search(combined):
        return None
    # Strip explicit anti-leak reminders ("do not share a target score of 9.5/10")? No—review briefs shouldn't mention target numbers at all.
    match = TARGET_SCORE_LEAK_RE.search(combined)
    if match:
        return _deny(
            f"Review/council brief leaks a numeric pass target ({match.group(0).strip()!r}). "
            "Keep reviewers blind to target scores and pass thresholds so scoring stays unanchored; "
            "send only the candidate, rubric anchors, and raw evidence, and compare against the target yourself."
        )
    return None


def guard(payload: dict, home: Path | None = None) -> dict | None:
    if payload.get("tool_name") not in TOOLS:
        return None
    return (
        _check_child_no_delegation(payload, home=home)
        or _check_thread_send(payload, home=home)
        or _check_review_target_leak(payload)
        or _check_claude_max_effort(payload, home=home)
        or _check_claude_fork(payload)
        or _check_antigravity_3p_inherit(payload)
        or _check_ultra_tier(payload, home=home)
        or _check_concurrency_and_resources(payload, home=home)
    )
