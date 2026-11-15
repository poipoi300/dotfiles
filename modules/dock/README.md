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
`qs -c dock ipc call dock reveal|hide|simulate <x>|release|open <i>|close|enter <name>|back|bench <events-per-s> <ms>|layout|theme|shot <file.png>`
(`bench` sweeps a synthetic pointer across the dock for profiling; pair it with `QSG_RENDER_TIMING=1`.
`layout` dumps the icon geometry, `theme` the resolved palette, `shot` saves the dock's own rendering).

## Editing items

Everything is in `home/.config/quickshell/dock/config.json` (linked to `~/.config/quickshell/dock/`).
Top level: `style`, `icon` (size, peak, spacing, waveSlots), `behavior`, `items`.

`style` is normally empty: colours, font and corner radius follow the DankMaterialShell theme (see
below). Any of `font`, `radius`, `background`, `border`, `text`, `labelBackground`,
`panelBackground`, `accent` set there overrides the theme.

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
- Icons are 30 px and swell to 128 px under the pointer with a cosine wave over 3 neighbours. Only
  real icons grow: the icon under the pointer stays under it and the row grows outward from it on
  each side by what that side grew, so the background is asymmetric near an end. At or past an end
  the end icon is the largest and stays put (centred where it rests); once the pointer passes its
  outer edge the wave fades out over ~100 px. The resting row is always centred on the screen and
  never slides. See the comment on `relayout()`.
- Label above the hovered icon. Accent-coloured dot under running apps (wlr-foreign-toplevel), a
  short bar under the focused app and under an open folder. Click focuses the app's window (an
  urgent window first, then repeated clicks cycle through several windows), otherwise launches it
  with a single bounce. An app asking for attention hops repeatedly while the dock is shown (urgency
  comes from niri's event stream, since the foreign-toplevel protocol has no urgent state).
- Folders open on hover (350 ms) or click, as a grid that grows out of the icon. Sub-folders open in
  the same panel; use the back arrow, the breadcrumb or Backspace. Close by clicking outside, Esc, or
  moving the pointer away (450 ms). A click-opened folder takes keyboard focus so Esc works at once;
  a hover-opened one only after you click it.
- Directory folders list one level at a time (`FolderListModel`, never recursive). Icons are chosen by
  file extension; png/jpg/webp/gif/bmp get thumbnails (`behavior.thumbnails`). Files open with
  `xdg-open`.

## Theme

`Theme.qml` reads DankMaterialShell's files and watches them (no polling), so the dock follows theme
changes, light/dark switches and font/radius changes live:

- `~/.config/DankMaterialShell/settings.json`: theme name, custom theme file and its selected
  flavour/accent, `fontFamily`, `cornerRadius`, `dockTransparency`, `popupTransparency`.
- `~/.local/state/DankMaterialShell/session.json`: `isLightMode`.
- `~/.cache/DankMaterialShell/dms-colors.json`: the palette DMS generates for every theme
  (stock, wallpaper, custom). With a custom/registry theme the theme file itself is read too and
  resolved the way DMS does (flavour + accent), so the colours match the bar exactly.

Roles: the bar is `surfaceContainer` at `dockTransparency`, the folder panel `surfaceContainer`,
the label `surfaceContainerHigh`, text `surfaceText`, indicators and hover states `primary`. The
`XDG_*_HOME` variables are honoured. Without DMS a built-in dark palette is used.

## Design notes

- Performance: no timers poll anything. The window is idle unless something moves; icons are 128 px
  items backed by fixed 256 px mipmapped textures (sharp at the peak on scaled outputs) that are
  only scaled (GPU transform, no relayout), and only the wave maths runs per rendered frame: pointer
  events only store a target and one `FrameAnimation` advances the wave and lays out once per frame
  (it stops when settled). Shadows are `RectangularShadow` (one analytic shader quad, no offscreen
  blur pass), so resizing the bar per frame stays cheap. The layer surface is 1400x680
  (wider only for very long rows; it changes with the config, never per frame), transparent, and its
  input region is just the dock zone (a 4 px strip while hidden) plus the open panel, so it never
  blocks other windows.
- Window state, urgency and the config are event driven (foreign-toplevel signals, one long-lived
  `niri msg event-stream`, file watching). Directory existence is checked on config load and each
  time the dock is revealed, so a late mount shows up without polling.
- Not done: drag reordering (locked by design), tray, show-dock hotkey, per-monitor docks (it uses
  `behavior.output` or the first screen), right-click menus.
