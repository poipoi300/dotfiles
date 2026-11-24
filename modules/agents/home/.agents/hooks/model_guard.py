"""Factor-derived efficiency guards for subagent, delegate, and thread launches.

Enforces model-agnostic checks without hardcoding model version slugs:
1. Block council/review or explicit no-delegation child sessions from spawning subagents.
2. Block review/council delegation prompts that leak a numeric target score / pass threshold.
3. Block Claude native transcript forks when the parent context exceeds 150k tokens.
4. Block Antigravity subagents from inheriting a non-Gemini third-party (`3p`) pool model.
5. Require an explicit `[ultra: <reason>]` opt-in when delegating to an ultra-tier model
   (blended token rate >= 3x the provider's standard tier in live local rate metadata).
"""

from __future__ import annotations

import json
import os
import re
import sys
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
TRANSCRIPT_TAIL_BYTES = 131_072
TRANSCRIPT_HEAD_MAX_BYTES = 1024 * 1024
TRANSCRIPT_MAX_TAIL_BYTES = 4 * 1024 * 1024
ULTRA_OPT_IN = re.compile(r"\[ultra:\s*\S[^\]]*\]", re.IGNORECASE)
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
CHILD_REVIEW_ROLE_RE = re.compile(
    r"\b(?:panel\s+council\s+reviewer|code\s+council|independent\s+(?:panel\s+)?council\s+reviewer|"
    r"council\s+reviewer|review\s+sub-?agent|break-?it\s+seat)\b",
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

# Claude Agent/Task, Codex spawn_agent, Antigravity invoke_subagent, and T3 delegation/launch tools.
MATCHER = (
    r"^(Agent|Task|spawn_agent|invoke_subagent|"
    r"mcp__t3[-_]code__(?:delegate_task|t3_thread_launch|create_threads))$"
)
TOOLS = {
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
DELEGATION_TOOLS = TOOLS


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


def _check_child_no_delegation(payload: dict, home: Path | None = None) -> dict | None:
    is_child, body = _child_task_body(payload, home=home)
    if not is_child:
        return None
    # Explicit prohibition always wins, even if the prompt mentions "subagents" in a negative clause.
    if body and CHILD_EXPLICIT_NO_DELEGATE_RE.search(body):
        return _deny(
            "This delegated child/council reviewer session must not spawn subagents or child tasks. "
            "Complete the audit directly yourself."
        )
    if body and CHILD_ALLOW_DELEGATE_RE.search(body):
        return None
    if (body and CHILD_REVIEW_ROLE_RE.search(body)) or (
        isinstance(payload.get("subagentType"), str)
        and payload["subagentType"].strip().lower() in ("reviewer", "adversary", "ml-reviewer", "verifier")
    ):
        return _deny(
            "This delegated child/council reviewer session must not spawn subagents or child tasks. "
            "Complete the audit directly yourself."
        )
    return None


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
        or _check_review_target_leak(payload)
        or _check_claude_fork(payload)
        or _check_antigravity_3p_inherit(payload)
        or _check_ultra_tier(payload, home=home)
    )
