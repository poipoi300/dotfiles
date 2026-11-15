---
name: designing-tuis
description: Use when creating a terminal UI (TUI) from scratch, or when a change reworks an existing TUI's layout, visual style, keybindings, or interaction model. Covers full-screen terminal apps, dashboards, runners, monitors, and pickers in Textual, Ink, Ratatui, or Bubble Tea. Not for small fixes inside an existing TUI, plain CLI argument parsing, or web/GUI work.
---

# Designing TUIs

## Overview

A TUI is a tool someone watches and drives from the keyboard. Design it around that person's actual loop in this specific app, then spend every cell on information. This skill owns visuals and interaction. For a runner that supervises services, also use building-runner-tuis.

## Start from the app

Read the project before choosing anything, and answer:

1. What does the user do most often here, and what do they scan for at a glance?
2. Which data is primary, and which is detail on demand?
3. Which actions are specific to this project, beyond the generic ones?
4. What stack does the project use? Pick the framework from it: Textual for Python, Ink for TypeScript/Bun, Ratatui for Rust, Bubble Tea for Go.

Layout, columns, and actions come from these answers, not from a template. A column, pane, or action this project does not need is clutter.

## Visual contract

| Area | Rule |
|---|---|
| Density | Pack. Use the full width. Split side by side (list left, detail right) and keep it that way as long as possible: as width shrinks, drop low-value columns first, and stack only when the detail pane would fall under 40 columns. No fixed breakpoint like 120. Separate with alignment, not padding. |
| Theme | The framework's default theme, unmodified. Do not repaint the screen background, color borders or titles with accent colors, add zebra stripes, or define a palette, unless the project already has one. Color only encodes state. |
| Borders | At most one border between the terminal edge and content. Single-line only. No double lines, bevels, 3D or tall buttons, or shadows. Prefer whitespace or one rule line. |
| Title | One quiet title, centered in the header. No clock or wall time. A subtitle only if it carries information. |
| Casing | Lowercase column headers and status words: `service`, `running`. No Title Case or ALL CAPS in tables, labels, or values. |
| Hierarchy | One primary region gets most of the space. Sections explain themselves through content: no captions, intros, or prose between sections. |
| Status | Symbol or word plus color, never color alone. One encoding per state, not marker plus icon plus word plus color. |
| Labels | Every label renders on one line. Size the column to the longest label, or truncate with `…`. A label never wraps across rows. |
| Time | No wall clock. Every duration (uptime, countdown) recomputes from one shared tick, so all of them change in the same frame. |

## Interaction contract

- Keyboard first. Every action has a key. The mouse may select and scroll; nothing requires it.
- Controls are visible and clickable in one place: a single command row at the bottom showing the main actions as key plus name. Clicking an entry runs the same action as its key. Generate the row from the binding table.
- The command row is exactly one line. Actions that do not fit are left out of it and live in the `?` modal, which lists every binding grouped by area. Never wrap to a second row.
- No key hints anywhere else: not in status, detail, or log text.
- Each action has exactly one on-screen surface: its command row entry, or the modal for overflow actions. No buttons, no per-row action links, no second action list.
- Actions never look like links: plain text, no underline, no link color.
- Conventions: `q` and Ctrl+C both quit through the same shutdown path, `?` help, `/` filter, `Esc` close, arrows plus `j`/`k`. Confirm only destructive actions, with focus defaulting to cancel.
- Exactly one element has focus, and it is visible.

## Engineering contract

- Rendering is a pure function of state and terminal size. I/O runs in workers or effects, never in render.
- Restore the terminal on every exit path: quit, error, Ctrl+C, panic.
- No TTY means plain output or a clear exit message. Honor `NO_COLOR`.
- Redraw on change or on the shared tick, never in an unconditional loop. Any growing list is virtualized or ring-buffered.
- Tests run headless: unit tests for state transitions, a snapshot of the main screen at a fixed size with a frozen clock, and scripted key sequences.

## Review before calling it done

1. **Clutter audit.** Count border nesting depth, duplicate encodings of one state, markers present on every row, and cells spent on chrome versus data. Name each element to remove. For each decorative element ask: does deleting it lose information?
2. **Floor test.** State what happens at 80×24 and at 60 columns: which pane wins, what truncates, and when a "terminal too small" message appears. Every side-by-side layout needs a single-pane fallback.
3. **Contract check.** Walk both contracts above row by row against the running app or its snapshot.

**REQUIRED:** before writing UI code, read the section of [references/frameworks.md](references/frameworks.md) for the chosen framework. It maps these contracts onto framework defaults that break them.
