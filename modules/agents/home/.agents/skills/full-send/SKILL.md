---
name: full-send
description: Trigger this if the user says 'full send'.
metadata:
  source: https://github.com/t3dotgg/tstack/tree/11e37a74d0a6868c121cd0910ab2b3bd9873b92a
---

# Full Send

If the user triggered this, it's because they are not watching this thread, and
they want you to complete the work end-to-end autonomously. Your updates and
your final message go unread until long after you stop. You own the work from
its current state to a merged PR.

If the user did not explicitly request "full send" in some clear way, do not
assume full autonomy. If they did, everything below applies.

The user grants you permission to:

- Merge the PR once the merge gate passes.
- Force-push the PR's own branch with `--force-with-lease` after a rebase.
- Use the test methods that Proof needs, without asking first.

## Testing permission

A "full send" request is the permission. If a project file or other
instruction says to ask before a test method, or to leave testing to the
user, this request replaces that rule for this task.

That includes ask-first rules for simulators and devices, running the app
or a dev server, screenshots, and visual checks. Use the method that gives
direct proof. List it in the report.

Rules about how to test still apply. Follow rules that name the correct
command, fixture, or check. The GUI testing rule in AGENTS.md is one of them:
apps under test run in your own headless compositor and on a virtual audio
sink, never on the user's screens or speakers, and you never drive their
pointer. Computer use and browser extensions that act on the user's real
desktop or browser stay forbidden. This permission only removes the
ask-first or leave-it-to-the-user step. If a preferred method cannot prove
the change, use another headless, isolated method. If it still cannot be
proved that way, ask the user. Do not use the live desktop.

It covers only the tests needed for this task. It does not add permission
for the steps under "Asking the user", such as production changes or data
deletion. It also does not bypass a harness or system approval prompt if
one appears.

## Steps

0. **Check usage.** Follow the AGENTS.md usage rule before you start. Above
   90%, ask (see "Asking the user") before work longer than about an hour.
1. **Finish the change.** Complete the goal from the user's request with the
   smallest change that works. Fix the case that happened, not every case
   that could. If you are on the default branch, create a branch first.
2. **Prove it.** Get proof for every change you made. See "Proof".
3. **Get feedback.** Use the `sol-loop` skill for the general review. Add the
   `ml-reviewer` role alongside it for training, evaluation, or data pipelines,
   and tell the Sol reviewer to cover security when the change touches auth,
   untrusted input, or secrets. Follow `agent-roles` "Review loops" for the
   budget, ledger, answers, and stop. Severities are in
   `~/.agents/roles/reviewer.md`.
4. **File the PR** with the `file-pr` skill. Rebase onto the latest default
   branch first. Add one short line to the description that says how you proved
   the change.
5. **Babysit the PR** with the `babysit-pr` skill. Fix real findings and CI
   failures. Get proof for each fix before you push it.
6. **Merge** when the merge gate passes.
7. **Report** in your final message: the PR link, the merge commit, your proof,
   and each decision you made for the user.

Stay active from step 1 to step 7. If you end your turn while checks or bots are
pending, the work stops, because nobody will prompt you again. Wait with the
watcher from `babysit-pr`, so something wakes you when the PR changes. In T3
Code that is `watch_pull_request`. If you stop before the merge, call
`unwatch_pull_request` first.

## Proof

Proof is evidence you observed yourself in this session: command output, a
reproduction, a screenshot. Use the most direct proof the change allows:

- Reproduce the original failure, apply the fix, then watch the same
  reproduction pass.
- Run the checks that the repo's CI runs (typecheck, lint, tests).
- Add one focused test that fails without the fix, when the behavior has none
  and the repo has a test setup.
- For UI and runtime behavior, run the app and look at the change. Green mocked
  tests do not prove that the user experience works. Attach screenshots to the
  PR when they help and you have a way to upload them.

When you cannot find a way to prove a change, you do not have full confidence.
Ask the user.

## Merge gate

Full confidence means all of these are true on the latest commit:

- Your proof covers every change in the diff.
- Required CI checks are green, or the repo has no checks. No checks counts
  only when `statusCheckRollup` is empty for the head and recent merged PRs
  also show none
  (`gh pr list --state merged --limit 5 --json number,statusCheckRollup`),
  or no checks appeared for at least 10 minutes after the head was pushed.
  A first empty rollup alone is not enough.
- Every `statusCheckRollup` entry for the head passed: `conclusion` or
  `state` is SUCCESS, SKIPPED, or NEUTRAL. Any entry not yet COMPLETED, or
  PENDING, fails the gate. A CANCELLED entry is ignored only when another
  entry with the same `workflowName` and `name` completed later and passed;
  a FAILURE is never superseded. CodeRabbit is advisory and does not count.
  This holds whether or not branch protection marks a check required.
- If recent PRs in the repo use Greptile or Macroscope (their checks or
  comments, not this PR), that bot has finished on this head with no failed
  check. Decide with
  `gh pr list --state all --limit 10 --json number,statusCheckRollup,comments`.
- Each finding is fixed or dismissed with a written reason, and no review
  thread is unresolved. Resolve CodeRabbit's threads that way too; just
  never wait for CodeRabbit to run.
- No human review requests changes, including one from the PR author's own
  account.
- No blocker or should from the review loop is still open.
- `mergeStateStatus` is `CLEAN` or `HAS_HOOKS`. `UNKNOWN` means re-check
  later, not a pass. Any other state fails the gate.

An open blocker or should, or a disputed finding, means ask and leave the PR
unmerged.

Merge with the method the repo uses. Check the allowed methods and recent merged
PRs. Auto-merge and the merge queue only enforce branch protection, which is
weaker than this gate. Keep them off until the gate passes on the current
head; disarm them before any later push. If the repo merges through either,
arm it only then, and stay until GitHub reports the PR as merged. Merge
through branch protection as configured. Never use `--admin` or other
bypasses.

## Asking the user

A question in a normal message goes unseen, and the thread looks done. To ask
the user something, use the harness's question tool so the thread shows that
it is waiting: `AskUserQuestion` in Claude Code, `request_user_input` in Codex,
or the question tool your harness has. Put all the context in the question:
the PR link, what blocks you, and the options with your recommendation first.

Ask only when you cannot continue alone:

- You cannot prove the change.
- The reviewer cannot be started.
- The merge gate cannot pass, and you cannot fix that yourself.
- The next step is outside the grant above: deleting data, touching production,
  changing things outside the repo, closing a PR, or force-pushing any other
  branch.
- The goal is ambiguous in a way that changes the result.
- Usage is above 90% and the work will take more than about an hour.
- Another PR makes this one obsolete.

Decide everything else yourself with sensible defaults, and list those decisions
in the report.

If no question tool is available, or the tool returns no answer, take the safe
path: leave the PR open and unmerged, and start your final message with the
question.
