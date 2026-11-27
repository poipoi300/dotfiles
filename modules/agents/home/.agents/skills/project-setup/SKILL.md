---
name: project-setup
description: Use when creating, scaffolding or bootstrapping a project or repository, choosing its stack, language toolchain or package manager, laying out or adding crates, packages, workspace members, modules or services, adding a dependency, or restructuring an existing project's architecture, build tooling, dependency management or cross-platform support. Covers Rust workspaces and sccache, Python with uv or pixi, TypeScript with Bun, Vue or React, shadcn and Convex, and modular, service-oriented, data-oriented design. Gates, CI and forge.project.toml are the forge skill; issues and plans are project-management; code style is coding.
---

# Project setup

A project starts from its data model, its module and service boundaries, and
one home for every fact. Pitch the stack and the architecture before
scaffolding and get a yes: these are the material architectural choices the
global instructions say to discuss.

## One source of truth

Every config, CI step, instruction, plan and doc has exactly one home. Every
other place references that home or is generated from it. Never copy.

| What | Its one home |
| --- | --- |
| Tool settings | The file the tool reads (`Cargo.toml`, `pyproject.toml`, `tsconfig.json`, `biome.json`). Members inherit from the root through `[workspace.*]` tables or `extends`, never by repeating values. |
| Machine-wide settings (compiler caches, global tool config) | The user's dotfiles, per the system-changes skill. Never in a repository. |
| Gates and CI | forge, per the forge skill. |
| Personal working rules | The global `~/.agents/AGENTS.md`. A project's `AGENTS.md` holds that project's contracts, commands and boundaries, in forge's structure (forge skill). |
| Work items and plans | GitHub issues and a Project board, per the project-management skill. |
| Setup and run instructions | `README.md`. Other docs link to it instead of repeating it. |
| Derived artifacts (JSON Schema from models, API types, lockfiles) | Generated from their source, committed when consumers need them, and checked by a test that regenerating changes nothing. |

When you find two copies of a fact, keep the one its owner reads, delete the
other, and point to the survivor.

## Cross-platform

Everything targets Linux, macOS and Windows. A platform is out of scope only
when the README states the reason.

- Build paths with `pathlib`, `std::path` or `node:path`. Resolve per-user
  directories through `platformdirs`, the `directories` crate, or the
  platform's API. Never hardcode separators, `/tmp`, `~` or drive letters.
- Pass commands as argv arrays, not shell strings.
- Write project scripts in the project's language, or in Python run with
  `uv run`. A shell script that a gate runs must work under macOS's Bash 3.2
  and Git Bash on Windows.
- Commit `.gitattributes` with `* text=auto eol=lf`. Mark binary fixtures
  `binary`.
- Prefer native dependencies that ship builds for all three platforms. The
  README lists any system packages each platform needs.
- CI tests on all three platforms (forge skill).

## Architecture

### Modular

- Split modules by responsibility. Each module's root names what it owns, its
  public contract and the dependencies it may use. A test enforces that
  allow-list. In Rust, run it over `cargo metadata`. In Python and
  TypeScript, check imports.
- Dependencies point inward. A pure core holds the domain model and has no
  IO. Adapters wrap IO: files, network, codecs, databases and devices. The
  entry point and UI wire adapters to the core. This is the functional core
  inside an imperative shell, with ports and adapters at the boundary.
- A contract is a type, a schema or an interface definition: a Rust trait,
  a Convex `schema.ts`, a WIT world or a JSON Schema. Commit the contract
  before parallel work starts against it (vcs skill).

### Service-oriented

- Put each long-running process and each external system behind a service
  interface. The service owns its state, speaks typed messages or a typed
  API, and can be started, stopped and tested alone.
- Draw a service boundary as a module boundary first. Split it into its own
  process only for isolation (untrusted input, crash containment), a
  different runtime, or independent scaling. Start as a modular monolith.
- Declare every runnable service in `forge.project.toml` (forge skill), so
  launching it, finding its port and checking its health need no knowledge
  outside the manifest.

### Data-oriented

- Begin with the data: what exists, how much of it, who reads and writes
  it, and how often. Lay out code around those transformations, not around
  noun hierarchies.
- Keep data as plain types (Rust structs, dataclasses, types derived from a
  schema). Keep behaviour in functions that transform collections of them.
  Avoid objects that bundle per-item state with methods.
- Process in batches. Use columnar layouts for bulk data: Polars or Arrow
  tables, and struct-of-arrays in hot Rust paths.
- Relate records by stable identifiers, not by pointers or positions. Every
  record and field stays identifiable wherever it travels: across processes,
  in files and in logs.
- Represent state by membership in a collection rather than by flags on a
  record, where the access pattern allows it.
- Measure before optimizing a layout. A hot path gets a benchmark in the
  repository.

## Stack

The global instructions' "Preferred stack" lists the defaults. Choose by job:

- **Backend and realtime state:** Convex functions and live queries, not REST
  with hand-written websockets and client caches.
- **Web runtime, package manager, scripts and tests:** Bun.
- **Hot path inside a Python project:** a Rust crate exposed through PyO3 and
  maturin, not micro-optimized Python.
- **UI design:** the impeccable skill.

## Rust

- Use a virtual workspace. The root `Cargo.toml` has `[workspace]` with
  `members = ["crates/*"]` and `resolver = "3"`, and no `[package]`. Shared
  `edition`, `rust-version`, `license` and `repository` go in
  `[workspace.package]`. Every dependency version goes in
  `[workspace.dependencies]`, and members write `dep.workspace = true`.
- Lint policy lives in `[workspace.lints.rust]` and
  `[workspace.lints.clippy]`, with `[lints] workspace = true` in every member.
  It never lives in crate attributes or CI flags. Enable a lint group at
  `priority = -1` so individual lints can override it.
- `rust-toolchain.toml` pins `channel`, `profile = "minimal"`, `components`
  and `targets`. `rust-version` states the MSRV.
- Commit `Cargo.lock` and pass `--locked` to every cargo command in docs,
  hooks and CI.
- Tooling: `cargo-nextest` for tests. Nextest skips doctests, so also run
  `cargo test --doc` when the crates have them. `cargo-llvm-cov` collects
  coverage through nextest (`cargo llvm-cov nextest --lcov`). `cargo-deny`
  checks advisories, licenses, bans and sources from `deny.toml`. The forge
  skill wires these up as gates.
- **Dev build speed (`Cargo.toml`, portable across Linux/macOS/Windows):**
  set `[profile.dev] debug = "line-tables-only"` and
  `[profile.dev.package."*"] debug = false` in the workspace root
  `Cargo.toml`. That keeps panic backtraces readable while eliminating giant
  DWARF/PDB blobs from dev builds, shrinking `sccache` entries and cutting
  codegen and link time. Never put OS- or toolchain-specific flags
  (`split-debuginfo = "unpacked"`, `-fuse-ld=...`, `RUSTC_BOOTSTRAP`, `-Z`)
  in a repository's `Cargo.toml` or `.cargo/config.toml`: `unpacked` breaks
  Windows MSVC, and global linker flags break `wasm32` and cross-targets.
- **sccache and fast linking are machine-wide.** The dotfiles `dev` module
  sets them up: `modules/dev/home/.cargo/config.toml` holds `rustc-wrapper`
  and target-scoped Linux x86_64 flags under
  `[target.x86_64-unknown-linux-gnu]` (for example `-C link-arg=-fuse-ld=mold`
  and `-C split-debuginfo=unpacked`; note `rustc >= 1.90` already defaults to
  `rust-lld` on `x86_64-unknown-linux-gnu`, and `[build] rustflags` must not
  carry Linux linker flags because it breaks `wasm32`/cross builds), and
  `modules/dev/home/.config/sccache/config` holds the cache size. Change them
  there, through the system-changes skill. Never set `rustc-wrapper`,
  `RUSTC_WRAPPER` or `CARGO_BUILD_RUSTC_WRAPPER` in a repository's
  `.cargo/config.toml`, scripts or docs (Cargo merges repo config into every
  contributor's build, failing without sccache). Avoid overriding
  `SCCACHE_DIR` per-repo without also setting `SCCACHE_CACHE_SIZE`: the
  default `~/.cache/sccache` uses the configured cap (20 GiB, on the small
  root disk), whereas overriding `SCCACHE_DIR` alone
  reverts the singleton daemon to its 10 GiB default and causes thrashing.
- **Combining `sccache`, `incremental`, and `-Z threads`:** `sccache` always
  caches third-party dependencies (Cargo disables incremental for them), but
  skips any crate compiled with `-C incremental=...`. When running multiple
  parallel checkouts/worktrees of the same workspace, pass
  `CARGO_INCREMENTAL=0` (or set `incremental = false` in `profile.dev`) so
  `sccache` shares workspace `.rlib`s across worktrees; single-checkout edit
  loops can keep incremental on. `RUSTC_BOOTSTRAP=1` with `-Z threads=N`
  caches cleanly in `sccache`, but both hash into the cache key (keep flags
  uniform so builds share cache, keep `N` modest such as `4` when multiple
  `cargo` processes run concurrently, and treat `RUSTC_BOOTSTRAP=1` as opt-in
  for heavy single-crate bottlenecks rather than unconditional global config
  because it unlocks unstable `#![feature(...)]` probes for `autocfg`).
- Release binaries build on each platform's native runner. Release
  automation belongs to forge, like the rest of CI. If forge has no release
  workflow, raise that in forge. Never let a packaging tool generate a
  workflow into the project.

## Python

- **Choose uv by default.** Choose pixi when any dependency must come from
  conda: the CUDA toolkit, GDAL or PROJ, compilers, or native libraries
  without wheels for all three platforms. Use one manager per project, never
  two lockfiles for the same environment. pixi resolves PyPI dependencies
  through uv itself.
- **uv:** start with `uv init --package`, or `--lib` for a library. That
  gives a `src/` layout. Development tools go in `[dependency-groups] dev`.
  Commit `uv.lock` and `.python-version`. Use `uv sync --locked` in hooks and
  CI, and `uv run` for every command. When several packages share one lock,
  list them in `[tool.uv.workspace] members`. When a dependency ships only
  wheels, list the three platforms in `[tool.uv] required-environments`.
- **PyTorch on uv:** follow uv's PyTorch guide. Declare explicit
  `[[tool.uv.index]]` entries per variant, and select them per platform with
  markers in `[tool.uv.sources]`, or with conflicting extras declared in
  `[tool.uv] conflicts`.
- **pixi:** for a Python project, keep the manifest in `pyproject.toml`,
  where every pixi table is prefixed `tool.pixi.`. Otherwise use
  `pixi.toml`, where the same tables are unprefixed.
  - Set `channels = ["conda-forge"]` and
    `platforms = ["linux-64", "osx-arm64", "win-64"]` in the workspace table
    (`[tool.pixi.workspace]` or `[workspace]`). Add
    `osx-64` or `linux-aarch64` only when they are targets.
  - PyPI dependencies stay in `[project] dependencies`.
  - CPU and CUDA builds are separate features, combined in `[environments]`.
    Declare the CUDA requirement on the platform entry.
  - Commit `pixi.lock`. Run commands through `pixi run` and named
    `[tasks]`.
  - When pixi is missing from the machine, install it through the
    system-changes skill.
- Gates (ruff, mypy, pytest under coverage) are forge's. Configure them in
  `pyproject.toml`.

## TypeScript

- **Bun** is the package manager, script runner and test runner. Commit the
  text `bun.lock` and install with `bun install --frozen-lockfile` in hooks
  and CI. A multi-package repository uses Bun workspaces from the root
  `package.json`.
- **Vue:** scaffold with `create-vue` (Vite and TypeScript), extend
  `@vue/tsconfig` with `strict: true`, and type-check with
  `vue-tsc --noEmit`. **React:** React 19 on Vite, type-checked with
  `tsc --noEmit`. Vite does not type-check.
- **Format and lint** with Biome. forge's hook ids are `biome` and `tsc`.
- **shadcn (React) and shadcn-vue:** the CLI adds component source to the
  repository, recorded in `components.json`, on Tailwind v4. Editing that
  source is the coding skill.
- **Convex:** `convex/schema.ts` is the data contract. Write and commit it
  before the functions. Test with `convex-test` on Vitest's edge-runtime
  environment; it does not run under `bun test`. Vue binds through the
  community `convex-vue` package. Function rules are the coding skill.

## New project

1. **Pitch:** the data model, the modules and services with their contracts,
   the stack, and what is out of scope. Wait for a yes.
2. **Repository:** README with setup and run sections, LICENSE, `.gitignore`
   and `.gitattributes`. `AGENTS.md` and `CLAUDE.md` come with adopting
   forge in step 4.
3. **Toolchain:** the manifest, toolchain pin and lockfile for each language,
   from the sections above.
4. **Gates and dashboard:** adopt forge (forge skill).
5. **Plan:** file the approved breakdown as issues on a Project board
   (project-management skill).
6. **Commit:** commit the skeleton before building on it (vcs skill).

### Worked example: *"Build me a habit tracker with realtime sync."*

1. **Pitch:** Convex (schema, functions, live queries), Vue with TypeScript,
   and Bun. Data: habits and check-ins related by id. Streaks are derived in
   a query, never stored. Present this with the tradeoffs and get a yes.
2. **Scaffold:** `bun create vue@latest`, `bun add convex convex-vue`, and
   `bunx convex dev`, which creates `convex/` and links a deployment. Adopt
   forge, then file the breakdown as issues on the board.
3. **Contract first:** the lead writes and commits `convex/schema.ts`. Only
   then does work run in parallel against it, each task with a narrow
   contract. One task writes the Convex functions and their tests. One
   writes the habit-list and streak components, derived from live queries.
   One writes seed data and fixtures. Use runtime-native delegation and
   isolation. The lead owns integration and reviews every diff against the
   coding skill.
4. **Integrate and verify:** wire the components to live queries, run the
   gates, and check realtime sync with two sessions.
