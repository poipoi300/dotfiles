---
name: skill-creator
description: When the user asks to write or test a skill.
---

<!-- Synthesized and modified from Obra writing-skills (MIT), Anthropic skill-creator (Apache-2.0),
     and OpenAI Codex skill-creator (Apache-2.0). See NOTICE, LICENSE-MIT.txt, and LICENSE-Apache-2.0.txt. -->

# Skill Creator

Author, refine, validate, and test portable agent skills across Claude Code, Codex, Antigravity, and Grok.

## 1. Core Principles

- **Assume the agent is capable.** Only add instructions, edge cases, local invariants, or scripts an agent would not reliably get right on its own. Challenge every paragraph: if removing it does not change behavior, cut it.
- **Keep `SKILL.md` lean (<500 lines) with 1-level-deep references.** Split heavy reference material (>100 lines), reusable scripts, and output templates into sibling files under `references/`, `scripts/`, or `assets/`, linked directly from `SKILL.md` with clear guidance on *when* to read each file. Do not nest reference chains (`SKILL.md` -> `a.md` -> `b.md`).
- **Match degrees of freedom to fragility.** Use open guidance when multiple approaches work, constrained templates when output shape matters, and deterministic stdlib scripts when operations are brittle or repeated identically across runs.
- **Automate mechanical rules first.** If a rule can be enforced with a hook, linter, schema check, or unit test, enforce it mechanically rather than hoping prose catches it.
- **Preserve user intent and scope.** Do not invent auxiliary `README.md` or changelog files inside a skill folder, do not add surprising side effects, use forward-slash relative paths, make script errors explicit (never silent fallbacks), and avoid time-sensitive assumptions ("before 2026...").
- **Explain the why, then match the form to the failure.** Before writing behavioral guidance, classify the baseline failure—the wrong wording form measurably backfires:

| Baseline failure | Right form | Wrong form |
|---|---|---|
| Skips or rationalizes around a rule under pressure | Bright-line prohibition + rationalization table + red flags (Mode C) | Soft advice ("prefer...", "consider...") |
| Complies, but output has the wrong shape (bloat, buried verdict, restated spec) | Positive recipe or output contract: state what the output *is*, in order | Negative prohibition list ("don't restate", "never narrate") |
| Omits a required element from an artifact it already produces | Structural required field or slot in the template it fills in | Prose reminders next to the template |
| Behavior depends on a condition | Explicit conditional keyed to an observable predicate ("if X exists, do Y") | Unconditional rule plus vague exemption clauses |

*Rules inside behavior-shaping and discipline skills:* avoid **nuance clauses** ("don't X unless it matters") and **exemption clauses** ("this limit doesn't apply to code blocks")—both reopen negotiation or bleed across boundaries. Express real exceptions as separate conditionals on observable predicates, or restructure so the rule cannot reach the exempt part. (These wording rules apply to rules *inside* a skill, not to selecting the testing mode in Section 4.)

## 2. Anatomy, Naming & Description Discipline

```text
<skill-name>/
├── SKILL.md              # Required entrypoint (YAML frontmatter + concise instructions)
├── scripts/              # Optional deterministic helpers (prefer stdlib Python + unit tests)
├── references/           # Optional 1-level-deep specs, schemas, rubrics loaded on demand
├── assets/               # Optional static templates/files used in produced outputs
├── agents/               # Optional subagent briefs or opt-in Codex agents/openai.yaml
└── tests/                # Optional unit/CLI tests for bundled scripts (use temp dirs only)
```

### Frontmatter & Portability
- **Allowed portable keys (enforced by `scripts/quick_validate.py`):** `name`, `description`, `license`, `allowed-tools`, `metadata`.
- **Never use `disable-model-invocation`.** Codex's skill loader rejects it. Limit implicit triggering through `description` (and, if Codex-specific gating is explicitly requested, `policy.allow_implicit_invocation: false` in `agents/openai.yaml`).
- **`name`:** lowercase kebab-case (`a-z`, `0-9`, `-`), <=64 chars, matching the folder name. Prefer active, searchable names (`creating-foo`, `debug-bar`) unless an established name is requested.
- **No `@` file-mention links** in `SKILL.md` (they force-load files into context). Reference sibling skills by bare skill name (e.g., `Load the vcs skill`).

### Description = Triggers Only (Critical)
Agents read `description` to decide whether to load a skill. **Describe ONLY when to trigger the skill—never summarize its workflow, steps, or "what it covers."** When a description summarizes steps, agents shortcut the full skill body and follow the summary instead.

- State concrete user intents, situations, symptoms, error messages, or file types in third person (<=1024 chars; ideally crisp and scannable).
- Cure under-triggering by **broadening user phrasings and trigger contexts**, never by listing workflow steps.

```yaml
# Bad: summarizes workflow (agents skip reading SKILL.md)
description: Use when executing plans - dispatches a subagent per task and reviews between tasks. Covers setup, checks, and cleanup.

# Good: triggering conditions only
description: Use when executing an implementation plan with independent tasks in the current session.

# Good: concise user-request trigger
description: When the user asks to write or test a skill.
```

### Local Placement
- Shared personal skills live in `~/dotfiles/modules/agents/home/.agents/skills/<skill-name>/` (deployed via `arcus` to `~/.agents/skills/` and linked into Claude/Antigravity by `sync.py`; Codex and Grok discover `~/.agents/skills/` natively). Load the `system-changes` skill before adding or changing dotfiles-managed skills.
- Project-local skills live in `<repo>/.agents/skills/<skill-name>/` (with a `.claude/skills/<skill-name>` symlink when needed).

## 3. Authoring Workflow

### Step 1: Survey Existing Skills for Inspiration
Before designing or drafting a new skill (or overhauling an existing one), inspect existing skills in `~/.agents/skills/` (and `<repo>/.agents/skills/` if in a project) for inspiration on patterns to adopt and to check for boundary overlap:
- Scan neighboring skill descriptions and layouts to avoid duplicating an existing skill or leaving fuzzy boundaries between siblings.
- Look for proven local patterns worth adopting: concise imperative tone, progressive disclosure (`SKILL.md` + `references/`), sibling cross-references (`system-changes`, `agent-roles`, `vcs`), stdlib Python scripts with `-I -B` invocations and `unittest` suites, and clean dual-license / `NOTICE` attribution when adapting upstream material.
- Apply `skill-creator`'s portability and description standards rather than blindly copying legacy outliers (for instance, never copy custom frontmatter keys outside Section 2's 5-key allowlist or older workflow-summarizing descriptions).

### Step 2: Capture Intent & Scaffold
Extract what the skill should accomplish, when it should trigger, expected outputs, and failure modes from the current conversation first. Ask only the concrete gaps that cannot be inferred.

Create the directory manually or scaffold it with `scripts/init_skill.py`:
```bash
python3 -I -B ~/.agents/skills/skill-creator/scripts/init_skill.py <skill-name> \
  --path <parent-dir> [--resources scripts,references,assets] [--examples]
```
Pass `--openai-yaml` (or `--interface key=value`) only when Codex UI metadata or `policy.allow_implicit_invocation: false` is explicitly desired; see [references/openai_yaml.md](references/openai_yaml.md) and `scripts/generate_openai_yaml.py`.

### Step 3: Draft Concisely
- Write in direct imperative voice; put the core decision rules and workflow upfront.
- Prefer **one complete, realistic example** over multi-language filler or narratives about past sessions.
- If multiple test runs would write the same helper code, move it into `scripts/` with explicit error handling and unit tests.
- Keep supporting references self-contained, 1 level deep, and linked with relative Markdown paths.

### Step 4: Validate Structure & Metadata
Run the validator on every new or edited skill:
```bash
python3 -I -B ~/.agents/skills/skill-creator/scripts/quick_validate.py <skill-dir>
```
Fix every reported frontmatter, nested `SKILL.md`, or unfinished `[TODO:...]` failure before testing or deploying.

## 4. Risk-Matched Testing & Iteration

Pick the lightest verification mode that matches the skill's failure risk. Do not skip testing on high-risk skills, and do not run heavy TDD pressure campaigns on a 40-line reference lookup.

| Mode | When to use | What it proves |
|---|---|---|
| **Mode A: Quick Validation + Optional Forward-Test** | Narrow edits, thin tool wrappers, pure reference skills | Valid metadata/scripts + clean agent retrieval without leaked hints |
| **Mode B: Output Evals & Benchmark Loop** | Multi-step workflows, artifact/code/document generators, complex refactors | Higher output quality and assertion pass rate vs baseline (`without_skill` / `old_skill`) |
| **Mode C: TDD Pressure Testing (Iron Law)** | Discipline & guardrail skills (rules agents rationalize skipping under time, sunk-cost, or authority pressure) | Rule compliance holds under combined realistic pressures; loopholes closed |

### Mode A — Quick Validation & Independent Forward-Test
1. Run `quick_validate.py` and any bundled script unit tests (`python3 -I -B -m unittest ...`).
2. When non-trivial behavior changed, run a clean blind subagent trial: give it only the skill and raw inputs (never your conclusions or expected answer) and inspect whether it produces the right outcome.

### Mode B — Output Evals & Benchmark Loop
Use Mode B when judging artifact quality or multi-step execution:
1. **Define test cases** under `evals/<case-name>/` (`prompt.md`, `case.yaml`, `graders/NN-*.md`, optional `files/`) following [references/schemas.md](references/schemas.md). Keep 2–3 realistic prompts first; add edge cases as you iterate.
2. **Run parallel `with_skill` vs baseline** (`without_skill` for new skills, or a snapshot `old_skill` for edits) in an external workspace `<skill-name>-workspace/iteration-N/<case-name>/{with_skill,without_skill}/outputs/` (never commit scratch eval workspaces into dotfiles). Record timing/tokens in `timing.json` immediately on subagent completion.
3. **Grade objectively** using [agents/grader.md](agents/grader.md) to write `grading.json` with cited evidence and eval-quality critiques.
4. **Aggregate benchmarks** across runs:
   ```bash
   python3 -I -B ~/.agents/skills/skill-creator/scripts/aggregate_benchmark.py \
     <workspace>/iteration-N --skill-name <skill-name>
   ```
5. **Generate a static HTML review page** for human inspection (never open live browser windows on the user's desktop):
   ```bash
   python3 -I -B ~/.agents/skills/skill-creator/eval-viewer/generate_review.py \
     <workspace>/iteration-N --skill-name <skill-name> \
     --benchmark <workspace>/iteration-N/benchmark.json
   ```
   This writes `<workspace>/iteration-N/review.html` (or `--static <path>`) and exits cleanly. Share the path with the user if they want to inspect outputs and export `feedback.json`. (Opt-in `--serve` binds `127.0.0.1` without opening a browser.)
6. **Optional blind A/B comparison:** when comparing two skill revisions, invoke [agents/comparator.md](agents/comparator.md) blindly on outputs A and B, then [agents/analyzer.md](agents/analyzer.md) post-hoc on transcripts and skills.
7. **Iterate without overfitting:** read full execution transcripts, not just pass rates. Generalize fixes from root causes rather than patching for one test prompt; delete instructions that caused wasted tool calls without helping.

### Mode C — TDD Pressure Testing (Discipline & Guardrail Skills)
**Iron Law (Mode C):** *No discipline skill—or edit to one—without a failing baseline test first.* If you have not watched an agent violate the rule without the skill under pressure, you do not know which rationalizations the skill must counter.

1. **Micro-test critical wording first** before slow full-scenario loops:
   - Use 1 fresh-context sample per call with the realistic surrounding prompt/skill.
   - Always include a **no-guidance control** (if the control does not fail, do not add guidance).
   - Run **5+ reps per variant**; single samples lie.
   - **Manually read every flagged hit** (automated regexes miss quoted counter-examples).
   - Treat **variance as a metric**: 5 different interpretations across 5 reps means the wording is not binding—fix the form (table in Section 1) before adding words.
2. **RED (Baseline):** run scenarios combining 3+ realistic pressures (time, sunk cost, authority, exhaustion) with a subagent *without* the skill. Record choices and rationalizations verbatim.
3. **GREEN (Minimal Skill):** add bright-line rules, an explicit rationalization table, and a Red Flags list targeting the exact observed excuses. Re-run with the skill and confirm compliance.
4. **REFACTOR (Close Loopholes):** capture any new rationalizations, plug each explicitly, meta-test unclear sections, and re-verify until compliance holds.
5. For full scenario design, meta-testing, and psychology foundations, read [references/pressure-testing.md](references/pressure-testing.md) and [references/persuasion-principles.md](references/persuasion-principles.md).

### Provider-Agnostic Trigger Check
Before shipping any skill with a new or edited `description`, test it against **5–10 realistic queries** (half should-trigger with varied casual phrasing, half near-miss should-not-trigger sharing keywords with sibling skills). Verify the agent selects the skill on should-trigger queries and skips it on near-misses—adjusting only trigger conditions/contexts in `description`, never adding workflow summaries.

## 5. Deployment Checklist

- [ ] `python3 -I -B ~/.agents/skills/skill-creator/scripts/quick_validate.py <skill-dir>` passes.
- [ ] All bundled script unit tests pass (using temporary directories only; zero dirty repo artifacts).
- [ ] Every relative Markdown link in `SKILL.md` and `references/` resolves to an existing file, and no dead/placeholder references remain.
- [ ] Appropriate testing mode (A, B, or C) + trigger check completed for the skill's risk level.
- [ ] For dotfiles-managed skills: follow `system-changes` (`arcus plan` -> `arcus apply -y < /dev/null` -> `python3 -I -B ~/.agents/launch.py sync` -> `python3 -I -B ~/.agents/sync.py check`), update `modules/agents/home/.agents/tests/test_portable_content.py` and READMEs when adding/renaming a shared skill, and verify discovery in a fresh provider session.
