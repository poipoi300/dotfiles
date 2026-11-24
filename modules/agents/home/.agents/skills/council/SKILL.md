---
name: council
description: Use only when the user mentions council, asks to interrogate or adversarially review a change or design or stress-test it by trying to break it, or when a hillclimb result cannot be verified with hard tests. Ordinary implementation and feature reviews use the relevant reviewer subagent, not council.
---

# Council

Gather independent agents' judgments about the same artifact and turn them into
a verdict. Council evaluates; the caller decides and implements changes. It runs
when the user asks for it, or to judge a [Hillclimb](../hillclimb/SKILL.md)
candidate that hard tests cannot verify. There are two modes:

- **[Code council](#code-council)**, the default whenever the candidate is code.
  Ten specialist seats file claims. A claim can block only when it carries a
  witness: a command that passes on correct code. `scripts/witness.py` runs every
  witness on the candidate, and on the base when the candidate fails, then
  computes each status and the gate, so no status rests on an agent's word.
- **[Panel council](#panel-council)**, for designs, writing and other artifacts
  no command can check, and for Hillclimb criteria no witness can establish.
  Reviewers judge a rubric and the verdict comes from aggregation.

A request to interrogate a change or design, or to break it, runs the code
council with its [break-it seats](#break-it-requests). Inside Hillclimb, use the
panel council unless the Hillclimb contract names the code council; then `pass`
counts as a passing required check, and `fail` or `unverified` as not met.

## Set the review contract

Identify the exact candidate, user goal, audience, constraints, and evidence to
inspect. Give the candidate a stable identifier: a commit, content hash, or named
snapshot. Reviewers must inspect the actual artifact; visual judgments require
visual access. Missing access means unverified, never an invented assessment.

When the user asks for a council review of code, the caller first fixes known
issues, addresses open PR comments, and runs the project's required checks, so
the seats judge the current implementation rather than defects already known. A
failed required check is a fail on its own; no council judgment overrides it.
Skip fixes to code the user wants replaced. Break-it requests judge the change as
it stands. Inside Hillclimb, evaluate the baseline unchanged; known fixes become
the first round.

## Choose real reviewers

This workflow calls for independent agents. Follow the active harness's
delegation rules, user-selected providers/models, and any restriction on agents.

- In T3, discover providers, models, and options through
  `orchestrator_capabilities` before the first `delegate_task`. Use native
  delegation where T3's rules require it; use `delegate_task` for cross-provider
  selection, explicit settings, worktree isolation, or requested background work.
  Follow a delegated task's returned `next`; do not poll running tasks. Each
  re-review is a fresh task. Never use top-level threads as reviewers.
- Outside T3, use the harness's supported agent mechanism and available models.
  A shared role brief is not proof of a registered agent or connected provider.
- Use different model families or providers when available, permitted, and
  useful; separate runs of one model are acceptable, with reduced diversity
  disclosed. Prefer reviewer families distinct from the builder when healthy
  alternatives exist. Choose by strength and pool-normalized quota efficiency
  (`budget.py --route` `burn=%/M` alongside pace), not hardcoded IDs: GPT-family
  models for harsh technical seats (correctness, security, performance,
  data-structures), Claude for judgment and taste (rules, style, code-health,
  duplication), and for the blind seat a family other than the correctness
  seat's. Reserve providers with high weekly pool burn per token for targeted
  passes where their diversity matters most rather than every round of a long
  loop. Respect supplied reviewer counts, concurrent work, deadline, and quota.
  A smaller code council drops seats; a dropped blocking seat is `missing` and
  leaves the gate `unverified`. Fix the panel before collecting judgments.
- If delegation is unavailable or prohibited, report that an independent Council
  could not be formed. A direct assessment can still help, but does not count as
  multiple reviews. Do not silently replace an unavailable requested provider.

Reviewers are read-only on the user's checkout and must not delegate (hooks
enforce this for child reviewers). Run them concurrently where supported and
affordable, otherwise sequentially. Send each the same candidate and raw
evidence plus its own brief. Never send the builder's self-rating, effort,
desired verdict, acceptance target / score threshold, round number, prior-round
scores, or other reviewers' opinions—keep reviewers blind to the target so
scores stay unanchored. Pass each reviewer its remaining time and allocated
quota, and stop outstanding work when the caller's limit or cancellation
applies. Do not wait idle, and do not change the candidate until every reviewer
has finished. Council sets each reviewer's output contract; the roles' default
report formats do not apply.

## Code council

`S` below is `~/.agents/skills/council/scripts`. `C` is one scratch directory
outside the repository that holds every council file; pass absolute paths under
it. Seat briefs and the witness format are in [seats.md](seats.md).

### 1. Intent, setup and trees

The candidate is the named scope, by default the branch diff from its merge base
with the base branch (the PR's base, else the default branch) through the
working tree, untracked files included. Write `C/intent.md`: the user's goal in
their own words, quoted; the PR description verbatim when there is one; and the
commit subjects. Witnesses cite this file, so quote rather than paraphrase. Ask
only when an unclear intent would change what counts as broken.

Pick the setup commands that prepare a fresh checkout the way the project
documents it, such as `uv sync --frozen`, `bun install --frozen-lockfile` or
`cargo fetch`. The seats and the runner both use them.

```sh
python3 -I -B $S/witness.py materialize --repo <repo root> --base <base ref> --out $C/trees
uv run $S/garble.py garble --base $C/trees/base --candidate $C/trees/candidate \
  --out $C/garbled --map $C/map.json --file-issues > $C/garble-summary.json
```

`materialize` builds detached base and candidate worktrees without touching the
user's checkout. The candidate gets the uncommitted and untracked files in a
snapshot commit, and `C/trees/trees.json` holds the candidate id. `garble`
writes a garbled copy for the blind seat. Project names become `f1`/`t2`/`v3`,
string contents become `s4`, and comments disappear; line numbers stay, and
builtins and external APIs keep their names. The change touches a
supported-language file when `C/garbled/change.diff` is not empty.

For each unsupported language in a file the change touches, `--file-issues`
makes sure an issue exists in the repository hosting this skill. The summary
lists those languages under `unsupported_changed` with the issue links, or an
`issues_error` when filing failed. Exit 1 means some files could not be garbled
(`garble_errors`) and are missing from the blind seat's copy. All of these go in
the coverage line.

### 2. Seats

| Seat | Role | Blocks |
| --- | --- | --- |
| correctness | `adversary` | yes |
| blind | `reviewer`, then `adversary` | yes, when `change.diff` is not empty |
| security | `adversary` | yes |
| performance | `adversary` | yes |
| data-structures | `adversary` | yes |
| testing | `adversary` | yes |
| rules | `reviewer` | yes |
| duplication | `reviewer` | advisory |
| style | `reviewer` | advisory |
| code-health | `reviewer` | advisory |

Launch every seat at once, the blind seat in its first phase. Every other seat
gets the path of [seats.md](seats.md) and its seat name, the absolute path of
`C/intent.md`, the repository path, `C/trees/trees.json`, the setup commands, its
worktree path `C/worktrees/<seat>`, and its remaining time and quota.

Each seat returns one JSON object in a fenced block. Save the object without the
fence as `C/entries/<seat>.json` and check it:

```sh
python3 -I -B $S/witness.py validate --trees $C/trees --entries $C/entries/<seat>.json
```

Ask once more when the object is missing or `validate` reports a problem, and
include its output. After that the seat's status is `failed`.

The blind seat's first phase gets only the text of its phase-1 brief from
[seats.md](seats.md) and the path `C/garbled`. Do not send seats.md itself,
`intent.md`, the repository path or `trees.json`: they all carry real names. This
isolation is a prompt constraint rather than a sandbox; say so in the coverage
line. Save its plain-text hypotheses as `C/blind-hypotheses.md` before the second
phase, then reveal them:

```sh
uv run $S/garble.py reveal --map $C/map.json --in $C/blind-hypotheses.md --out $C/blind-revealed.md
```

The second phase is a fresh task that gets everything the other seats get, plus
`C/blind-revealed.md`. It returns the JSON object: a witness for each hypothesis,
or an advisory entry that withdraws it with the reason. When `change.diff` is
empty, skip the blind seat.

When the seats finish or are cancelled, remove whatever is left under
`C/worktrees` with `git -C <repo> worktree remove --force <path>`, then run
`git -C <repo> worktree prune`. Write `C/seats.json` with every seat, including
failed and missing ones:

```json
{"seats": [
  {"name": "correctness", "required": true, "status": "completed", "provider": "codex", "model": "<model id>"},
  {"name": "blind", "required": false, "status": "missing", "provider": "", "model": "", "note": "change.diff is empty"},
  {"name": "style", "required": false, "status": "completed", "provider": "claudeAgent", "model": "<model id>"}
]}
```

`required` is true for the blocking seats, except a skipped blind seat. `status`
is `completed`, `failed` or `missing`. `provider` and `model` are strings, empty
for a seat that never ran.

### 3. Review and run the witnesses

Before running anything, read every witness (`build`, `run`, `patch`, `files`)
and every setup command. Send a witness back, as you would a broken one, when it
reaches the network, real services or accounts, credentials, or files outside
the trees. The runner passes witnesses only an allowlist of environment
variables (paths, locale and toolchain settings) without secret-looking names,
and gives each run a private runtime directory. `HOME` stays, so file-based
credentials remain reachable, and nothing sandboxes the network or the
filesystem.

```sh
python3 -I -B $S/witness.py run --trees $C/trees --seats $C/seats.json \
  --entries $C/entries/*.json --setup "<setup command>" \
  --report-json $C/report.json --report-md $C/report.md
```

Run it in the background, since entries run one after another. Repeat `--setup`
for several commands. Each witness runs three times (`--runs`) in a tree reset to
its snapshot, and on the base only when every candidate run fails. The script
assigns each entry one status:

| Status | Rule |
| --- | --- |
| `introduced` | Fails on every candidate run; passes, cannot run (`n/a`), or varies on the base |
| `pre-existing` | Fails on every run on both trees |
| `flaky` | Passes and fails across candidate runs |
| `not reproduced` | Passes on every candidate run, so the claim is dropped |
| `held` | An attack the seat reported as held, and it passes |
| `broken witness` | Cannot run: bad schema, quote not found, failed build, unapplied patch, missing `cwd`, a timeout without `timeout_is_failure`, or a `broken_exit` code (126 and 127 by default) |
| `advisory` | No witness; never blocks |

A `held` entry whose witness fails is a break: it gets one of the failing
statuses, with severity `should`.

Send these back to their seat once each, as a fresh task with the entry and the
script's output, then rerun:

- every `broken witness`;
- every `flaky` entry, asking for a deterministic witness, such as operation
  counts or a ratio with a margin;
- every `pre-existing` entry whose `cause` is on a line the change adds or
  modifies. That is usually a witness for new code without a `build` step.

An entry that comes back the same way a second time stays in the report.

The gate is `fail` when an unwaived `introduced` entry has an effective severity
of blocker or should; otherwise `unverified` when any entry is `flaky` or
`broken witness`, or a required seat did not complete; otherwise `pass`. The
exit code follows it: 0 pass, 1 fail, 2 unverified, 3 input or environment
error.

### 4. Judge only what the script cannot

The script cannot tell whether a witness's expectation follows from the intent,
whether a severity is right, or why the base could not run it. For each blocking
`introduced` entry:

- read its intent quote and its witness;
- check the severity against `~/.agents/roles/reviewer.md` and the seat's rule in
  [seats.md](seats.md);
- when its base runs are `n/a`, check from their output that the code under test
  is really absent on the base. A base that failed for another reason, such as
  a broken environment, is not evidence; send the entry back.

Record each disagreement in `C/waivers.json`, with a reason that names its
evidence, or write `{}` when there is none:

```json
{"correctness:C2": {"action": "waive", "reason": "intent.md asks for X; the witness expects Y"},
 "testing:T1": {"action": "severity", "severity": "nit", "reason": "equivalent mutant: both branches return the cached value"}}
```

Never waive to reach a pass. Recompute the gate without rerunning anything:

```sh
python3 -I -B $S/witness.py gate --report-json $C/report.json --waivers $C/waivers.json \
  --report-json-out $C/final.json --report-md $C/final.md
```

Pre-existing failures never block. Council applies no fixes; the caller handles
them under its own rules.

### 5. Report and clean up

`C/final.md` is the report. Add the candidate id, the required-check results,
and a coverage line: seats completed and requested with provider and model;
unsupported languages with their issue links or `issues_error`; `garble_errors`;
and the blind seat's prompt-only isolation. A pass means the required checks
passed, every required seat ran, and no witnessed claim broke the change; it is
not a quality score. Keep the `Withdrawn:` and `Could not run:` entries from the
advisory section. Advisory entries follow [Advice and scope](#advice-and-scope).

Remove the trees with `python3 -I -B $S/witness.py cleanup --out $C/trees`, also
when the council is aborted or cancelled.

### Break-it requests

When the user asks to interrogate a change or to break it, judge the change as
it stands with the six attacking seats: correctness, blind, security,
performance, data-structures, and testing. Skip the rules and advisory seats.

A design or plan with no code change yet (no commits past the base and nothing
uncommitted) has nothing to compare. Quote the plan into `C/intent.md` and
materialize with `--base HEAD`. Correctness, security, performance and
data-structures then probe the current tree or prototype the claim the plan
rests on; the blind and testing seats have nothing to examine. The gate line
does not apply. The verdict is broken when any blocker or should entry is
`pre-existing` or `introduced`, and otherwise "not broken by the attacks under
Held". Without a repository, use the panel council.

## Panel council

Use the user's rubric and requested scale. Otherwise derive a short rubric from
the brief and state it. Define what weak, acceptable, and excellent mean for
each scored criterion. Only add numeric scores when useful or requested.
An evaluation can provide critique without a pass target; do not invent one.
When a verdict is needed, fix the acceptance threshold, mandatory constraints,
and aggregation rule before collecting reviews. Keep these stable across rounds.

Default to three independent reviewers with complementary focuses chosen by the
caller for the current artifact (such as domain quality, user impact, and
failure modes); any agreed smaller panel still needs at least two. Across
repeated rounds, keep the **rubric and acceptance rule** stable, not the
model-to-focus mapping: let the caller choose which lenses fit each round,
rotate which model family covers which focus automatically so lenses do not go
stale (avoid keeping one model family on the same lens for more than two
rounds), and avoid re-running untouched or already-clean domains unless a fresh
pass is warranted. Send each reviewer the same candidate, rubric anchors, and
raw evidence—**never** the numeric target, pass threshold, round number, or
prior scores; the caller alone compares the aggregated result against any
target. Ask each for criterion-level judgments and requested scores; concrete
evidence or locations; important strengths, defects, and uncertainties; and the
highest-impact improvements. Reviewers identify real defects without
manufacturing objections or awarding unsupported praise.

Keep individual judgments visible. Separate agreement, dissent, and missing
coverage. Check factual objections against the artifact; do not vote away a
verified mandatory failure or let an unsupported objection become a veto.

Unless the caller specifies another rule, require at least two independent
completed reviews and a strict majority of completed reviewers meeting every
required criterion. For numeric targets, also require the median score on each
required criterion to meet its threshold. Report the score range and dissent;
an overall average cannot conceal a failed mandatory criterion. A required check
failure or substantiated blocking defect means fail. Missing required evidence,
fewer than two reviews, or unresolved material factual uncertainty means
unverified. Missing requested reviewers or perspectives must be disclosed and
make the verdict unverified unless a demonstrated mandatory failure already
establishes fail. Do not shrink the panel after seeing results to obtain a pass.

Return a compact evaluation that a human or Hillclimb can consume:

- **Candidate and coverage:** identifier, artifacts inspected, completed/requested
  reviewers and their provider/model and focus, missing evidence.
- **Rubric and results:** criterion judgments, individual scores when requested,
  medians/ranges, required-check results, agreement and dissent.
- **Verdict:** pass, fail, or unverified against the stated target, with reasons.
  If no pass target was requested, give the assessment and say acceptance was
  not evaluated.
- **Blockers and next changes:** substantiated failures and a short ranked list
  of concrete improvements.

## Advice and scope

Advisory entries and panel findings are advice. The code owner or builder
decides which are true, relevant, and worth acting on, and may accept, defer, or
reject them based on its own inspection and judgment. Reviewer agreement does
not make a suggestion binding. Briefly explain material rejections or deferrals,
such as an incorrect premise, an out-of-scope concern, or an unfavorable
tradeoff. Guard the scope: repeated rounds tend to grow the change one cheap-looking
suggestion at a time. Decline extra tests, rare-case guards, added
compatibility, and small performance work the goal does not need, and list them
as follow-ups. When repeated findings stem from an unbounded spec promise
(e.g., claiming heuristic detection catches every possible secret spelling),
narrow or restructure the contract instead of chasing infinite edge-case
patches. Rejecting advice does not erase a confirmed required-check failure, a
blocking witnessed claim, the agreed acceptance criteria, or the Council's
recorded judgments.

Council finishes with the evaluation. In a Hillclimb run, return it to the loop
owner; do not start a second improvement loop or alter its limits or rubric.
