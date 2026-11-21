---
name: forge
description: Use when adding, changing or debugging a project's quality gates, pre-commit or pre-push hooks, coverage gate, CI workflow or gate registry, or writing or changing a forge.project.toml (checks, services, metrics, actions, V1 milestones). Also use when adopting forge in a project, bringing one up to forge's standards, or changing forge or the forge dashboard themselves. Not needed for ordinary code changes in a forge project. Stack and layout choices are project-setup.
---

# forge

forge supplies the quality gates for the user's projects. The forge dashboard
watches and runs those projects. Describe and use both as they are: read
their docs before relying on a detail, and fetch first, because local clones
fall behind.

## The two repositories

- **forge**, at `~/src/forge` (`poipoi300/forge`), is the standards source.
  - It publishes the gates projects use by reference:
    `.pre-commit-hooks.yaml` (hygiene hooks, `no-todo`,
    `tests-and-diff-coverage`; code in `src/forge/hooks/`) and the reusable
    workflow `.github/workflows/gates.yml`. Its `AGENTS.md` holds the
    working-rule sections projects link.
  - `src/forge/gates.py` is forge's own gate registry. forge's
    `.pre-commit-config.yaml` and `ci.yml` run the published hooks and
    workflow on forge itself.
  - It also holds an evaluation runner (`forge.toml`, `prompts/`). The runner
    measures how agents move a project's trajectory.
  - forge's `AGENTS.md` governs changes to forge itself.
- **forge dashboard**, at `~/src/forge-dashboard`, is a local control room
  for every forge project. Start it with `./run.sh`, `run.ps1` or `run.cmd`.
  - It shows health, checks, CI, issues and PRs, git and fork state,
    services, deployments, metrics, actions, agent sessions, V1 progress, and
    compliance with forge's standards.
  - Its actions are Launch, Bring up to standards, Adopt, fork sync, and
    Delegate issue. Delegate issue has an agent implement an issue on a
    worktree branch.
  - Its CLI is `uv run forge-dashboard list | status | check | sync`.
  - It finds projects by their `forge.project.toml`. It also lists forge
    itself, and repositories derived from forge that lack a manifest, which
    it offers to Adopt. For a managed project it runs the manifest's check,
    service, metric and action commands and the `.pre-commit-config.yaml`
    gates. It reads git, GitHub, and the files its standards check.

## The manifest

`forge.project.toml` sits at the repository root. Its contract is the
dashboard's `docs/project-manifest.md`, with the JSON Schema in
`schema/forge.project.schema.json`. Read the contract before writing or
changing a manifest, and validate the result:

```sh
uv run --project ~/src/forge-dashboard forge-dashboard manifest validate <repo>
```

- `[v1]` takes `source = "github-milestone"`, so V1 progress is the GitHub
  milestone's closed/total and the plan stays in GitHub (project-management
  skill). Inline `[[v1.milestones]]` duplicate the plan.
- Every runnable service is a `[[services]]` entry. Commands are argv arrays.
  Ports are `dynamic` unless something outside the project hardcodes one,
  which the `pinned` policy's `evidence` names.
- Tools write the `[forge]` table (`template_revision`, `aligned_at`). Never
  edit it by hand.

## Gates

- **Commit stage**, which runs in seconds: forge's hygiene hooks, then
  format, lint and types.
- **Push stage:** tests under coverage, then `diff-cover` requiring at least
  90% coverage on changed lines against `origin/main`. Set
  `DIFF_COVER_COMPARE_BRANCH` for another base.
- **CI** re-runs every hook on push and PR through forge's `gates.yml`: each
  OS lane runs both stages, and one job gates the merged coverage of all
  lanes. A project's CI covers Linux, macOS and Windows (project-setup
  skill). forge's own `ci.yml` runs on Ubuntu only.
- **Hooks:** forge's gates come from forge's public repository at a pinned
  `rev`, fetched with no credentials. Project-specific gates are `repo: local` with
  `language: system`, run through the project's own toolchain (`uv run`,
  `cargo`, `bun`). Install both stages:
  `uv run pre-commit install --install-hooks -t pre-commit -t pre-push`.
- **Hook ids follow the contract's per-language table** (Python
  `ruff-format`, `ruff-check`, `mypy`; Rust `cargo-fmt`, `cargo-clippy`;
  TypeScript `biome`, `tsc`).
  - The push stage has the single id `tests-and-diff-coverage`, forge's
    hook, with the project's languages as args (`[--python]`, `[--rust]`
    or both). It supports Python and Rust; a language it lacks is a forge
    issue, not a project script.
  - Other gates (`cargo-deny`, dependency policy, schema drift) get their
    own ids, at the earliest stage their run time allows.
- **One registry:** keep one machine-readable gate list per project, and a
  test that reads `.pre-commit-config.yaml` and compares the list with the
  hook stages and the CI steps. The dashboard's standards look for that
  test. Change the list, the hooks and CI together.
  - forge and the dashboard keep their list in a `gates.py` module.
  - In a project, make the manifest's `[[checks]]` the list. That avoids a
    second copy. Set each check's `argv` to run its hook
    (`uv run --locked pre-commit run <id> --hook-stage <stage> --all-files`)
    and its `stage` to match, so the command lives only in the hook.
- **Gates only tighten.** Never lower a threshold, add an exclusion or
  blanket-suppress a lint to land a change. This is the first entry in
  forge's `AGENTS.md` "The ways to hurt yourself". That section is rewritten
  per project, so keep the rule there when you rewrite it.

## CI comes from forge

The rule: projects consume CI, gate hooks and working rules from forge and
never carry a copy. A project pins one forge commit in two places and moves
both pins together:

- `.pre-commit-config.yaml` lists forge's published hooks under
  `repo: https://github.com/poipoi300/forge` with `rev: <sha>`.
- The CI workflow is a thin caller,
  `uses: poipoi300/forge/.github/workflows/gates.yml@<sha>`, passing only
  the project's inputs (OS matrix, apt/brew/choco packages, Rust toolchain
  and cargo tools, uv version, env, paths to prune before coverage). The
  inputs are listed at the top of `gates.yml`. Other jobs in the project
  workflow (release builds, artifacts) never run gates.
- forge is public, so neither pre-commit nor `gates.yml` needs a token or a
  secret.
- Pin a commit that is on forge's `main`. Never edit forge's code inside a
  project: change forge, then move the pins.
- The dashboard's standards check the pins (`forge-hooks`, `ci`), that every
  published hook is referenced from forge (`base-hooks`), and that
  `AGENTS.md` links forge's working-rule sections (`agents-sections`).

Workflow conventions for any workflow you write, forge's included:

- Pin every action to a full commit SHA, with the version in a comment.
- Set `permissions: contents: read` at the top, and widen per job only when
  needed.
- Use `persist-credentials: false` on checkout. Use `fetch-depth: 0` where
  diff-cover runs.
- Pass `--locked` or `--frozen-lockfile` to every install.
- Pin runner images (`ubuntu-24.04`, `macos-15`, `windows-2025`) rather
  than `-latest`.

## Adopting forge

1. **Manifest:** write `forge.project.toml`, or run the dashboard's Adopt,
   which drafts it from the repository. Validate it.
2. **`AGENTS.md`:** follow forge's structure. Rewrite the project sections
   (intro, what makes the project special, glossary, ways to hurt yourself,
   commands, where code lives). Link forge's working-rule sections ("A note
   from the maintainer", "Verifying", "Commits", "Taste") at the pinned
   commit under a `## Working rules from forge` section; never copy their text.
   `CLAUDE.md` contains only `@AGENTS.md`.
3. **Hooks:** `.pre-commit-config.yaml` with every hook forge publishes,
   referenced from forge, plus the language ids as local hooks. pre-commit,
   coverage and diff-cover come from the project's uv `dev` group, so a
   project in another language still has a `pyproject.toml` and `uv.lock`
   for its gate tools.
4. **CI:** follow "CI comes from forge" above.
5. **Standards:** run Bring up to standards from the dashboard. It works on
   a `forge-standards/<timestamp>` branch in a worktree, and it installs the
   git hooks in the main checkout. It writes the working-rule links and
   copies nothing. Review its diff, and fix each check it still reports.
6. **Commands:** list every gate command in `AGENTS.md`'s Commands table.
