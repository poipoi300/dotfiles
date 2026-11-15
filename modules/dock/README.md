# dock

An auto-hiding, magnifying dock for niri, written in QML for Quickshell. It runs as its own
process (`quickshell -c dock`), separate from the DankMaterialShell bar. The tray stays in the bar.

## Run

```sh
systemctl --user enable --now dock.service     # arcus does this through [units] user
quickshell -c dock                              # by hand, from a terminal
quickshell -p ~/dotfiles/modules/dock/home/.config/quickshell/dock   # a checkout, without deploying
```

`dock.service` is bound to `niri.service` the same way `dms.service` is, sets
`QS_ICON_THEME=breeze-dark` (change it if the icon theme changes) and uses `KillMode=process` so
apps started from the dock survive a dock restart. Commands such as `start-tavern` must be on the
`PATH` of the user manager (for example `~/.local/bin`).

Logs: `journalctl --user -u dock`. Config changes are picked up live (no restart).

For testing a checkout, `DOCK_DATA_DIR=<dir>` replaces `~/.local/share/dock`. Scripting hooks:
`qs -c dock ipc call dock reveal|hide|simulate <x>|release|open <i>|close|enter <name>|back`.

## Editing items

Everything is in `home/.config/quickshell/dock/config.json` (linked to `~/.config/quickshell/dock/`).
Top level: `style`, `icon` (size, peak, spacing, waveSlots), `behavior`, `items`.

Item types:

| type | fields |
|---|---|
| `app` | `name`, `desktop` (a desktop file id or a list of candidates, first one that exists wins), optional `icon`, `fallbackIcon`, `match` |
| `command` | `name`, `command` (argv list), `icon`, optional `fallbackIcon`, `match`, `requiresIcon` |
| `group` | `name`, `items` (any item types, groups nest), optional `icon` |
| `dir` | `name`, `path` (`~` allowed), optional `icon` |

- `icon` is a theme icon name, an absolute or `~/` path, or a path relative to
  `~/.local/share/dock/` (such as `icons/tavern.png`). Apps use their desktop entry's icon unless
  `icon` is set. A group or directory without `icon` shows a 2x2 preview of its first children.
- `match` lists extra window app_ids (lower case) that count as "this app is running". Desktop
  entries match on their id and `StartupWMClass` automatically.
- A missing app, a missing command, a missing directory, or (with `requiresIcon`) a launcher whose
  icon is not installed yet is drawn dimmed; clicking it does nothing.
- Lutris launchers use `lutris:rungame/<slug>`; Lutris installs `lutris_<slug>` icons, which the dock
  picks up through the icon theme. Change the slugs in `config.json` to match your Lutris entries.

Icons live in `home/.local/share/dock/icons/` (converted to PNG from the Windows originals).

## Behaviour

- Bottom edge, centred, layer-shell overlay, exclusive zone 0. Hidden until the pointer touches the
  bottom edge; hides 350 ms after the pointer leaves. It is not revealed at all while the focused
  window is fullscreen or maximized (`hideOnFullscreen`, `hideOnMaximized`).
- Icons are 30 px and swell to 128 px under the pointer with a cosine wave over 3 neighbours; the
  slot under the pointer stays under it.
- Label above the hovered icon. White dot under running apps (wlr-foreign-toplevel). Click focuses
  the app's window (repeated clicks cycle through several windows), otherwise launches it with a
  single bounce. An app asking for attention hops repeatedly (urgency comes from niri's event stream,
  since the foreign-toplevel protocol has no urgent state).
- Folders open on hover (350 ms) or click, as a grid that grows out of the icon. Sub-folders open in
  the same panel; use the back arrow, the breadcrumb or Backspace. Close by clicking outside, Esc, or
  moving the pointer away (450 ms). A click-opened folder takes keyboard focus so Esc works at once;
  a hover-opened one only after you click it.
- Directory folders list one level at a time (`FolderListModel`, never recursive). Icons are chosen by
  file extension; png/jpg/webp/gif/bmp get thumbnails (`behavior.thumbnails`). Files open with
  `xdg-open`.

## Design notes

- Performance: no timers poll anything. The window is idle unless something moves; icons are fixed
  128 px textures that are only scaled (GPU transform, no relayout), and only the wave maths runs
  per pointer movement. The layer surface is 1400x680, transparent, and its input region is just the
  dock zone (a 4 px strip while hidden) plus the open panel, so it never blocks other windows.
- Window state, urgency and the config are event driven (foreign-toplevel signals, one long-lived
  `niri msg event-stream`, file watching). Directory existence is checked on config load and each
  time the dock is revealed, so a late mount shows up without polling.
- Not done: drag reordering (locked by design), tray, show-dock hotkey, per-monitor docks (it uses
  `behavior.output` or the first screen), right-click menus.
