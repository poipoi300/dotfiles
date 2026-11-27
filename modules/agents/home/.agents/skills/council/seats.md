# Council seats

The contract every code-council seat follows, the witness format, and one brief
per seat. The orchestrator sends each seat this file's path with its seat name.
The blind seat's first phase is the exception: it receives only the text of its
phase-1 brief.

## Every seat

You are one seat on a council judging a code change you did not write. Judge the
candidate against the intent in `intent.md`, through your seat's lens only;
other seats cover the rest.

- The user's checkout and the council's trees are read-only. To run code, make
  your own worktree at the absolute path outside the repository that the
  orchestrator gives you (never a relative path, which `git -C <repo>` resolves
  inside the repository), from the candidate snapshot named in `trees.json`
  (`git -C <repo> worktree add --detach <path> <candidate_snapshot>`; the base is
  `base_sha`). Run the setup commands you were given there before anything else,
  and remove the worktree when you finish
  (`git -C <repo> worktree remove --force <path>`). Do not fix anything and do
  not delegate.
- "Nothing in scope" is a valid answer: `"in_scope": false` and no entries. Do
  not manufacture findings, and do not report what is absent or irrelevant.
- Severity follows `~/.agents/roles/reviewer.md`: `blocker` when the intended
  case is wrong, `should` for a real bug callers will hit or a violation of the
  AGENTS.md Correctness rules, `nit` for everything else. A seat's own rule below
  takes precedence.
- A claim blocks only through a witness that a script runs. A claim you cannot
  witness is advisory.

## Output

Return exactly one JSON object in a fenced `json` block:

```json
{
  "seat": "correctness",
  "in_scope": true,
  "entries": [
    {
      "id": "C1",
      "type": "finding",
      "claim": "parse_range drops the last element although end is inclusive",
      "severity": "blocker",
      "cause": "src/ranges.py:42",
      "intent": {"file": "<C>/intent.md", "quote": "the end of a range is inclusive"},
      "witness": {
        "files": {"tests/test_council_c1.py": "from src.ranges import parse_range\n\n\ndef test_inclusive():\n    assert parse_range(\"1-3\") == [1, 2, 3]\n"},
        "build": ["uv", "run", "--no-sync", "pytest", "--collect-only", "-q", "tests/test_council_c1.py"],
        "run": ["uv", "run", "--no-sync", "pytest", "-q", "tests/test_council_c1.py"],
        "expect": {"exit": 0}
      }
    }
  ]
}
```

Entry fields, spelled exactly (an unknown key makes the entry a broken witness):

- `id`: unique within your seat (`C1`, `C2`, ...).
- `type`: `finding` (the candidate breaks), `held` (an attack you ran that did
  not break it; it still needs a witness and an intent), or `advisory`
  (judgment without a witness; never blocks). A hypothesis you withdraw or a
  check you could not run is an advisory entry whose claim starts with
  `Withdrawn:` or `Could not run:`, followed by the reason or the command. A
  `held` entry whose witness fails counts as a break with severity `should`, so
  a `held` witness has to test something real.
- `claim`; `severity` (required for findings); `cause` as `path:line` in the
  real tree.
- `intent`: a verbatim quote and the file that holds it: `intent.md`, a
  docstring, a contract, a doc, or a rule file. Give `intent.md` and rule files
  by the absolute path you were given; relative paths resolve inside the
  candidate tree. The script checks that the quote exists, ignoring
  whitespace; it cannot check that your expectation follows from it, so quote
  the passage that does.
- `citations`: further `{"file", "quote"}` pairs the script also checks, such as
  a rule you rely on.
- `witness`:
  - `run`: argv list, required. `build`: argv list run first, which separates
    "the witness does not compile or collect" from "it ran and failed". `cwd`:
    relative to the tree root, default `.`.
  - `files` (path to content) and `patch` (unified diff) are applied to a freshly
    reset tree before each run, with paths relative to the tree root.
  - `expect`: `exit` (an integer, default 0, or `"nonzero"`), plus optional
    `stdout_regex` and `stderr_regex` that must match.
  - `timeout_s` (default 120, at most 900); `timeout_is_failure` when a hang is
    the bug; `broken_exit` (default `[126, 127]`) for exit codes that mean the
    witness itself could not run.

How the script reads a witness:

- A witness encodes the intended behavior. It passes on correct code and fails
  on the candidate. Run it in your worktree and watch it fail before you report
  it.
- The script runs it in its own fresh checkout three times, then on the base
  when the candidate fails every time. On the base, a witness that cannot run
  (a failed `build`, a patch that does not apply) means the code is new.
- A witness for code the change adds needs a `build` step that fails when that
  code is missing. Any command works: an import, `pytest --collect-only`,
  `cargo test --no-run`, `tsc --noEmit`, or
  `["sh", "-c", "tool --help | grep -q -- --new-flag"]` for a new flag. Without
  one, an import error on the base looks like an ordinary failure and the claim
  is filed as `pre-existing`.
- A whole-tree check (`rg`, a linter) also matches violations that were already
  on the base, so those come out `pre-existing`. To claim only what the change
  added, compare counts against `COUNCIL_BASE_TREE`.
- Keep it self-contained: no absolute paths into your worktree, no network, no
  production services or real accounts, and no windows or sound on the user's
  devices (AGENTS.md "GUI testing"). Scratch state goes in a temp dir. Run Python
  through `uv run --no-sync`; the runner prepares its checkouts with the same
  setup commands you ran. It passes only an allowlist of environment variables
  (paths, locale, toolchain settings), so a witness that needs anything else is
  broken. `HOME` stays: never read credentials from it. A witness that detaches
  with `setsid` escapes cleanup, so don't.
- Every file a witness writes, its output included, is capped at 256 MiB. A
  process the cap kills is a broken witness, but a shell or program that catches
  the limit may report it as an ordinary failure, so keep witnesses small.
- The environment carries `COUNCIL_ROLE` (`base` or `candidate`),
  `COUNCIL_TREE`, `COUNCIL_BASE_TREE` and `COUNCIL_CANDIDATE_TREE`.

## Seats

### correctness (blocks)

Does the change do what the intent says, and do its callers still work? Read the
diff and every caller of changed code, searching outside the diff. Attack the
cases the author was least likely to run: boundaries, empty and large inputs,
error paths, concurrency, changed defaults, and behavior the diff changed
without saying so. Witness: a failing test or a repro script.

### blind (blocks, two phases)

**Phase 1.** You are a reviewer on a council judging a code change you did not
write, and you receive a garbled copy of it. Project names are tokens (`f` for
functions, `t` for types, `v` for everything else, path components included),
string contents are `s` tokens, comments are gone, and every line number matches
the real file. Builtins and external APIs keep their names, except where the
project defines the same name: renaming goes by name, so a project method `get`
also renames `.get` on a dict. Read `change.diff` and `tree/` in the directory you
were given. Do not open anything outside that directory; that would defeat the
seat. Do not fix anything and do not delegate.
Describe what each changed function actually does, then list hypotheses. Look
for unreachable branches, unused results, and missing cases. Look for paths
that handle the same case differently, where one validates or releases and
another does not. Look for loops that might never end, swallowed errors, and
clones that differ only by renaming.
Return plain text, one hypothesis per line, as
`<garbled path>:<line> <hypothesis>`.

**Phase 2.** You receive your hypotheses with the real names restored, and the
real code. For each hypothesis, write a witness, or withdraw it as an advisory
entry with the reason. A common witness: the name or docstring promises what
the body does not do; cite it as the intent. Do not rewrite a hypothesis to fit
what the names suggest.

### security (blocks when in scope)

In scope when the change touches authentication or authorization, untrusted
input, secrets, subprocesses or shells, file paths, deserialization, SQL or
templates, network listeners, or permissions. Witness: an exploit repro against
a local instance or fixture that expects the safe outcome, or the command of a
static checker that proves the pattern. Never touch real services or accounts.

### performance (blocks when in scope)

In scope when the change touches hot paths, loops over growing data, I/O,
queries, allocation, or concurrency. Each claim names the input size that
matters and why it is realistic. Witness: a benchmark whose threshold is fixed
in the witness before it runs. The threshold is absolute (from the intent or a
stated budget) or relative to the base through `COUNCIL_BASE_TREE`, such as
"the candidate at 100k rows takes more than twice the base". Vet it with the
[benchmark-checklist](../benchmark-checklist/SKILL.md) skill. A speedup without
a realistic size is a nit.

### data-structures (blocks)

Can the new types represent states the intent forbids, such as a finished job
without a finish time, two exclusive options both set, or an identifier that may
be empty? Witness: a snippet in `files` that builds the invalid state, run
through the project's type checker or compiler with `expect: {"exit":
"nonzero"}`. Correct types reject it, so a candidate that accepts it fails the
witness. Add the checker's own fatal exit codes to `broken_exit` (mypy 2;
pyright 2, 3 and 4), so a misconfigured run counts as broken rather than as a
rejection. Do not require a diagnostic regex: a misconfigured checker would then
read as a reproduction instead of a dropped claim. Without a type checker, use a
runtime witness: constructing the invalid value must raise. Severity: `should`
when the intent forbids the state (cite it), `nit` otherwise.

### testing (blocks)

Do the tests pin the changed behavior? For each behavior the intent states, find
the test that fails when it breaks. Witness: a `patch` that mutates the changed
code (flip a condition, drop a guard, return early, shift a bound), with the
test command and `expect: {"exit": "nonzero"}`. Good tests fail on the mutant.
The patch does not apply on the base, so a gap in new code counts as introduced.
Use the project's mutation tool (mutmut, cargo-mutants, Stryker) when it has
one. Severity: `should` when the mutant breaks behavior the intent states (cite
it), because the change can then regress silently; `nit` otherwise. An
equivalent mutant is not a finding. Tests the intent does not need are advisory.

### rules (blocks when a command can check it)

Check the change against the written rules. Those are the repository's
AGENTS.md and CLAUDE.md files, `~/.agents/AGENTS.md`, and the
[coding](../coding/SKILL.md) skill. They also include every skill whose trigger
matches what the change touches: forge for gates and CI, vcs for commits and
PRs, ml-design for training code, project-setup for dependencies and layout.
Quote each rule verbatim as the `intent`. When a
command can check the violation (`rg`, `ast-grep`, a linter rule), make it the
witness, passing when the rule holds; otherwise the entry is advisory. Severity:
`should` for the AGENTS.md Correctness rules and for rules the repository marks
required, `nit` otherwise. When a violation is likely to recur, say in the
claim that it deserves a lint; the [correct](../correct/SKILL.md) skill turns
recurring mistakes into checks.

### duplication (advisory)

Every entry has `"type": "advisory"`. Search the repository outside the diff for
helpers, utilities and patterns the change reimplements, and look for clones
inside the diff. Each entry names both locations. Recommend extracting only when
the coding skill's test holds: churn, or one logical change that would otherwise
need edits in several places. Premature abstraction is a defect too.

### style (advisory)

Every entry has `"type": "advisory"`. Covers code and prose: comments,
docstrings, docs, the PR description, and commit messages. Flag AI tells. The
commonest are comments that narrate the change or mention what is not there:
"now uses", "no longer", "removed the old", "no extra dependency needed". Others
are references to the session or the request, docstrings that restate the
signature, inconsistent naming, and padded or hedged prose. Check changed docs
and the PR text with
`uv run ~/.agents/skills/humanizer/scripts/humanize_check.py --profile technical <file>`.
Match the surrounding codebase's conventions rather than a house style.

### code-health (advisory)

Every entry has `"type": "advisory"`. Judge structure against the
[coding](../coding/SKILL.md) skill: modularity and boundaries, guard clauses,
derived rather than synced state, functions that do one thing, dead code, and
premature abstraction. Name the specific change that would improve it and what
that change costs.
