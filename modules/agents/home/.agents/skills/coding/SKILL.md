---
name: coding
description: Coding standards, style, opinionated tech picks, and worked examples. Load whenever writing, editing, reviewing, refactoring, or testing code, or choosing libraries/architecture. Commits and commit messages are the vcs skill; training, evaluation and experiment design are the ml-design skill. Not needed for pure retrieval, summarization, or reporting tasks.
---

# Coding

## Style
- Functional and component-based; highly reactive where the platform supports it (Vue reactivity, Convex live queries, React state derived not duplicated).
- Modular, DRY, compact, guard clauses first — handle edge cases early and exit. Prefer broad, general solutions, but never at the cost of design, performance, or flexibility.
- Abstract only when there is churn, or when one logical change would otherwise require edits in multiple files. Premature abstraction is a defect.

## Standards
- **Types:** Python 3.12+ style; exhaustive but non-restrictive hints (`Iterable` over `list`/`Sequence` for parameters).
- **Docs:** Google-style docstrings for "what". Inline comments only for non-obvious "why" or constraints. Match the surrounding codebase's comment conventions. Prefer self-documenting names over comments. Never reference the current query/session in comments.
- **Quoting:** double quotes for multi-character strings, single quotes for single characters, where language idioms allow; language syntax always wins.
- **Delivery:** pitch alternative/superior approaches *before* the final code, then present one best implementation.
- **Environment:** cross-platform (Win/Mac/Ubuntu) local-first for data/ML/API work. Standard library by default; adopt a specialized library when it has genuinely solved the hard problems (see Opinionated Tech Picks).

## Opinionated Tech Picks
Pick the tool that already solved the hard problem; keep it reactive and compact:

- **Dataframes:** Polars lazy pipelines (`scan_parquet → collect`), not pandas row loops.
  - **Data at rest and between automated steps:** Parquet, which keeps column types. CSV only at the edge where a person imports or exports a file.
  - **Arrays:** Prefer `torch` over `numpy` for any numerical ops; avoid `.numpy()` roundtrips. Never hardcode device — `torch.device("cuda" if torch.cuda.is_available() else "cpu")`.
- **Python packaging/execution:** `uv` (`uv run`, `uv add`), not pip + venv rituals.
- **Backend + realtime state:** Convex functions and live queries, not REST + manual websockets + client caches.
- **Web scripts/tooling:** Bun (`bun run`, `bun test`), not node + separate bundler/test runner.
- **UI:** small typed components; derive state, don't sync it (Vue `computed`, React derived props); lift state only when two components genuinely share it.
- **Perf-critical hot paths:** a small Rust crate with Python bindings (PyO3/maturin), not micro-optimized Python.
- **Paths, batching, iteration:** `pathlib`, `itertools` (`batched`, `chain`, `pairwise`), not `os.path` or hand-rolled loops.

## Worked Examples
Three tiers. Same simple-prompt entry point; effort, agents, and ceremony scale with the ask.

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

### Project — *"Build me a habit tracker with realtime sync."*
1. **Plan (orchestrator tier)** — architecture sketch: Convex (schema, functions, live queries) + Vue/TS + Bun. This is architectural → **suggest first**: present the plan and stack tradeoffs, get a yes before scaffolding.
2. **Scaffold** — `Bash: bun create vue@latest`, `bun add convex convex-vue`, `bunx convex dev` (configures the project and creates `convex/`).
3. **Delegate** — the schema is the shared contract, so the orchestrator writes it first; only then parallelize against it with narrow contracts: Convex CRUD functions and tests against that schema; habit list/streak components derived from live queries; seed data and fixtures. Use runtime-native delegation and isolation. The orchestrator owns integration and reviews every diff against these rules.
4. **Integrate & verify** — wire components to live queries, run the full suite, click through realtime sync with two sessions.
