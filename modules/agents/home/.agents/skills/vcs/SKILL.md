---
name: vcs
description: Use before committing, pushing, opening a pull request, or writing a commit message or PR title. Says where a change goes (direct push, upstream PR, or carried on the fork, decided by `fix-route`), how to write the message, and how to split work into commits.
---

# Version control

## Where a change goes

Before the first commit of a change, run `fix-route` in the repository and do
what it prints. Don't decide the route yourself: it reads the repository's
remotes and settings, and a hook blocks pushes and PRs that go against it.

| Route | Meaning | What to do |
|---|---|---|
| `direct` | The user's own repository (dotfiles, scripts, projects) | Commit on the default branch and push. No PR, no branch. |
| `upstream` | A fork whose main matches upstream | Branch from `upstream/<default>`, push the branch to origin, `gh pr create --repo <upstream>`. The fork's main doesn't get the change. Ask the user whether the fork should carry it until upstream merges. |
| `upstream+carry` | A fork whose main already has changes upstream lacks | The same upstream PR, then merge the branch into the fork's main and push it. No PR on the fork. |
| `external` | Someone else's repository, not a fork of it | Branch, push the branch (or fork first if you can't push), and open a PR against origin. Never push its default branch. |

- Run `git fetch --all` first: the fork routes compare the fork's main with
  upstream through the local remote-tracking refs.
- `git config agents.route <route>` sets a repository's route for good. It's
  local and never committed. Set it only when the user says which they want,
  for example after they answer the carry question.
- The user merges upstream PRs; never merge them yourself. A `direct` or
  carried change is pushed by you once its tests pass.
- If the hook blocks a push or `gh pr create`, its message says what the
  route needs instead. Follow it; don't work around the hook.
- Exit codes: 2 means the route can't be decided (not a repository, no
  origin, or the user's GitHub login is unknown); 3 means a fork has no
  `upstream` remote yet. The message gives the command that fixes either.

## Commit messages

Imperative mood, capitalized subject, ≤50 chars, no trailing punctuation. Body
only when it adds information the subject can't (wrap at 72, blank line after
subject, don't restate the subject): usually why the change was needed and
what it fixes. Output only the commit message, with no commentary and no diff.

Example: `Fix leaderboard ties to break by submission time`.

Follow the repository's own conventions when it has them: read
`git log --oneline -20` first. Use the commit identity the user's instructions
give, and end the message with the attribution lines the harness asks for.

Pull request titles and descriptions follow the `file-pr` skill. Where a
repository has no title convention of its own, use the subject rules above.

## Commit cadence

- One logical change per commit. A feature spanning layers commits per layer
  (API, UI, tests), not as one blob.
- New projects commit the scaffold skeleton before building on it.
- A shared contract (a schema, an interface) is committed before parallel
  work starts against it, so every branch builds on the same version.
- Commit incrementally throughout; never one giant final commit.
- Stage paths explicitly (`git add <paths>`), never `git add -A` or
  `git commit -a`: other sessions may have uncommitted work in the same
  checkout, and `git diff --cached --stat` before committing shows whether
  anything else was already staged.
