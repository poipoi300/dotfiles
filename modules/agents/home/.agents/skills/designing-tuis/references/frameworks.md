# Framework notes

Load the section for the chosen framework only. Version-specific claims drift; check the installed version before relying on one.

Sources: rules adapted from gfargo/tui-design-skill and pageton/tui-design-skill (both MIT), filtered through the user's own preferences in SKILL.md, which win on conflict.

## Textual (Python)

Applying the SKILL.md contracts:
- `Header()` centers the title; leave `show_clock` off. Set `TITLE`; set `SUB_TITLE` only when it carries information.
- The command row is `Footer(show_command_palette=False, compact=True)`. It is one line, clickable, and generated from `BINDINGS`. Mark only the main actions `show=True`; overflow actions get `show=False` and appear in the `?` modal. Keep binding descriptions lowercase and short. The footer clips silently when it overflows, so screenshot it at 80 columns and confirm quit and help are still visible.
- Build the `?` modal as a `ModalScreen` whose table is generated from the app's and screen's `BINDINGS`, so it cannot drift.
- Textual's default Ctrl+C only shows a "press ctrl+q to quit" notice, and Ctrl+Q calls `App.action_quit`, which skips the app's own shutdown. Bind `q`, `ctrl+c` and `ctrl+q` with `priority=True` to the app's quit action, and test that Ctrl+C exits.
- Avoid `Button`: its default style is tall and beveled. Actions are bindings.
- Leave the default theme alone. Do not set `App.theme`, and keep CSS free of `Screen { background: ... }`, `border: ... $primary`, `color: $accent` on titles, and `zebra_stripes=True`. CSS is for layout: sizes, docking, and margins.
- `DataTable` column labels lowercase. Fix column widths to the longest value; keep row height 1 so labels never wrap.
- One `set_interval(1.0, self._tick)` on the app drives every duration. `_tick` takes one `now` and updates all rows from it.
- Child process output: `RichLog(max_lines=...)` plus `Text.from_ansi(line)` keeps the child's colors.
- The built-in command palette (Ctrl+P) is hidden until summoned, so it does not violate the one-surface rule.

Gotchas:
- Reactives assigned in `__init__` fire watchers before mount. Set initial values via the reactive default or in `on_mount`.
- `compose()` runs once. Changing structure later needs `recompose=True` on the reactive or manual mount/remove.
- Never `print()` inside a running app. Use `self.log` and the `textual console` from `textual-dev`.
- TCSS type selectors also match subclasses.
- `push_screen_wait` must run inside a `@work` worker.
- Blocking calls go in `@work(thread=True)`, or use asyncio subprocess APIs.

Testing: `async with app.run_test(size=(160, 45)) as pilot:` then `await pilot.press("s")`; snapshots with `pytest-textual-snapshot`.

## Ink (TypeScript)

- ESM only; `require('ink')` fails.
- Every string must sit inside `<Text>`; a bare string as a `<Box>` child throws.
- Running under file watchers like nodemon breaks raw-mode input such as arrow keys.
- Layout is flexbox (Yoga). Use `wrap="truncate-end"` on `<Text>` for one-line labels.
- Use `useInput` for bindings; keep the binding table as data so the `?` modal renders from it.
- Testing: `ink-testing-library` renders frames without a TTY.

## Ratatui (Rust)

- Use `ratatui::init()` and `ratatui::restore()` (or the run helper in recent versions), which install a panic hook that restores the terminal. Do not stack a second custom panic wrapper on top.
- Keep a single crossterm version in the tree: check with `cargo tree -p crossterm`.
- `String::len()` is bytes, not cells. Use `unicode-width` for layout math.
- Testing: `TestBackend` with fixed size and buffer assertions.

## Bubble Tea (Go)

- Keep `Update` free of I/O; return `tea.Cmd` for effects.
- Lipgloss width math: use `lipgloss.Width`, not `len`.
- Testing: `teatest` for scripted input and golden output.
