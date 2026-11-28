---
name: coding
description: Coding standards, style, opinionated tech picks, and worked examples. Load whenever writing, editing, reviewing, refactoring, or testing code, or choosing a library for code. New projects, stack, layout and architecture are the project-setup skill; gates and CI are the forge skill; commits and commit messages are the vcs skill; training, evaluation and experiment design are the ml-design skill. Not needed for pure retrieval, summarization, or reporting tasks.
---

# Coding

## Stay proactive while coding

While working, flag issues you spot even if not asked — file them as issues
(`project-management` skill, or mention in the reply without a tracker) rather
than fixing unasked, unless the change is simple (confirmed bugs in repos the
user owns follow `AGENTS.md` "Fixing what you find broken"):
- Edge cases, off-by-one errors, or logical inconsistencies in adjacent code.
- Silent failures (returning `None` instead of raising, overly eager defaults).
- Mismatched shapes/dtypes/index alignments, or data leakage risks (e.g. future
  data leaking into training targets).
- Hardcoded paths or magic numbers that should be config parameters.

## Goal-driven execution

Turn tasks into verifiable goals:
- "Add validation" → write tests for invalid inputs, then make them pass.
- "Fix the bug" → write a test that reproduces it, then make it pass.
- "Refactor X" → tests pass before and after.

For multi-step tasks, state a brief plan sized to the task with each step
paired to its verification, then proceed without waiting for approval unless a
choice is costly to redo (`AGENTS.md`). Strong success criteria let you loop
independently; weak ones ("make it work") invite unnecessary clarification.

## Style
- Functional and component-based; highly reactive where the platform supports it (Vue reactivity, Convex live queries, React state derived not duplicated).
- Modular, DRY, compact, guard clauses first — handle edge cases early and exit. Prefer broad, general solutions, but never at the cost of design, performance, or flexibility.
- Abstract only when there is churn, or when one logical change would otherwise require edits in multiple files. Premature abstraction is a defect.
- Check what already exists in the codebase for reuse before writing from scratch.
- For refactors or bug fixes, updating related callers or siblings that the change itself would otherwise leave broken or inconsistent is in scope — state that scope in the intent and verify it (unrelated cleanup stays out of scope).
- When a fix would force many call sites to remember something new (threading a parameter everywhere, a new convention), flag that fragility before proposing it and check whether a single boundary-level change removes the need.

## Standards
- **Types:** Python 3.12+ style; exhaustive but non-restrictive hints (`Iterable` over `list`/`Sequence` for parameters).
- **Docs:** Google-style docstrings for "what". Inline comments only for non-obvious "why" or constraints. Match the surrounding codebase's comment conventions. Prefer self-documenting names over comments. Never reference the current query/session in comments.
- **Quoting:** double quotes for multi-character strings, single quotes for single characters, where language idioms allow; language syntax always wins.
- **Delivery:** note alternative/superior approaches and tradeoffs concisely before (or alongside) implementing, then proceed with one best implementation unless the choice is costly to redo (`AGENTS.md`).
- **Libraries:** standard library by default; adopt a specialized library when it has genuinely solved the hard problems (see Opinionated Tech Picks). Cross-platform rules and stack choice are the project-setup skill.

## Opinionated Tech Picks
Pick the tool that already solved the hard problem; keep it reactive and compact:

- **Dataframes:** Polars lazy pipelines (`scan_parquet → collect`), not pandas row loops.
  - **Data at rest and between automated steps:** Parquet, which keeps column types. CSV only at the edge where a person imports or exports a file.
  - **Arrays:** Prefer `torch` over `numpy` for any numerical ops; avoid `.numpy()` roundtrips. Never hardcode device — `torch.device("cuda" if torch.cuda.is_available() else "cpu")`.
- **UI:** small typed components; derive state, don't sync it (Vue `computed`, React derived props); lift state only when two components genuinely share it. shadcn and shadcn-vue components added by their CLI are the project's own code: edit them in place, and never re-add over local edits with `--overwrite`.
- **Convex functions:** argument validators and access control on every public function; indexes (`withIndex`), not `.filter`; scheduled work and `ctx.run*` targets are internal functions; shared logic in plain TypeScript helpers behind thin function wrappers.
- **Paths, batching, iteration:** `pathlib`, `itertools` (`batched`, `chain`, `pairwise`), not `os.path` or hand-rolled loops.

## Worked Examples
Two tiers. Same simple-prompt entry point; effort, agents, and ceremony scale with the ask. A new project is the project-setup skill's worked example.

### Small feature/edit — *"Leaderboard ties break randomly — break ties by earliest submission."*
1. `Read leaderboard/rank.py`; `Grep "sort"` for other consumers of the ordering (fragile-order rule: this change alters observable behavior, so find every reader).
2. Localized, behavior-changing but contained → implement directly. Stable multi-key sort: `sort(["score", "submitted_at"], descending=[True, False])`.
3. Add a regression test pinning the tie case. `Bash: uv run pytest tests/test_rank.py -q && uv run ruff check .`
No sub-agents — one model, one pass.

### Large feature — *"Add CSV and Parquet export to the analytics dashboard."*
1. **Map** — where independent searches justify delegation, use the current runtime's native delegation tools while the lead reads the core: one task traces data flow from the query API to the dashboard table; another lists download/streaming endpoints and their authentication. Choose available models through that runtime's capabilities. The lead merges reports.
2. **Pitch first** (delivery rule) — server-side Polars `sink_csv`/`sink_parquet` streaming vs client-side serialization; recommend server-side (memory-bounded, one format path), state the tradeoff, then proceed.
3. **Implement** — endpoint + typed Vue component with a reactive format selector; touches API, UI, and tests, but no shared contracts change → still direct.
4. **Verify** — unit tests per layer, then one end-to-end export of a real fixture.
