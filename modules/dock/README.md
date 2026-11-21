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
`qs -c dock ipc call dock reveal|hide|simulate <x>|point <x> <y>|release|open <i>|close|enter <name>|back|bench <events-per-s> <ms>|layout|state|theme|shot <file.png>`
(`bench` sweeps a synthetic pointer across the dock for profiling; pair it with `QSG_RENDER_TIMING=1`.
`point` puts a synthetic pointer anywhere in the window, `layout` dumps the icon geometry, `state`
the intent and hide state, `theme` the resolved palette, `shot` saves the dock's own rendering).

The intent maths (`Intent.js`) and dock state machine (`Dock.qml`) have unit tests: `bun test`
from the module directory.

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

- Bottom edge, centred, layer-shell overlay, exclusive zone 0. Hidden until the pointer rests on
  the bottom edge for `revealDelayMs` (150 ms by default, preventing accidental edge triggers;
  re-entering mid-fold catches immediately). Leaving it hides it at once in one motion: the icons
  shrink back on the same curve as the bar slides down (no pause, no settle step first). It is not
  revealed at all while the focused window is fullscreen or maximized (`hideOnFullscreen`,
  `hideOnMaximized`).
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
- Folders open by intent or click, as a grid that grows out of the icon. Intent (`Intent.js`): the
  pointer's trajectory (position and sustained horizontal velocity relative to the icon, which
  filters out 1–3 px hand tremor and wave-settling lag while preserving steady sideways glides)
  is tested against each folder icon's visible box (not its label, the slot gaps or the bar below
  it). How soon the pointer gets there (ETA) and how long it would then stay on the icon (or in the
  panel funnel above it once engaged by a brief slow touch or upward deceleration) give a commit
  level; open progress follows it exponentially, preloads the folder contents at 0.15 so the panel
  opens at its true size, and begins a single unbroken ease-out growth + centring motion once it
  crosses 0.5 (cancelling back to 0 only if integrated intent drops before latching). Sideways
  sweeps and slow glides (including 5–8 px/s) as well as plain vertical exits over a folder never
  start or flash the panel. Stopping on an icon starts opening in ~0.22 s and finishes in ~0.36 s
  (matching `folderHoverDelayMs`); rising into the panel at up to ~30° or curving up from a gentle
  ease-out arc onto the icon freezes the wave and opens it in ~0.15–0.22 s.
- Opening centres the wave and the panel on the folder icon while the icon itself stays where it
  is (the row shifts around it), so the pointer is never pulled off it. Switching to another folder
  is brisk (~0.10–0.15 s) and keeps the new icon where it is the same way.
- An open folder closes by intent too: outside the panel and the folder's column (which ends at the
  panel's top, so leaving upward closes too) a close level builds up, faster the farther and the
  faster the pointer moves away (and faster when resting directly on another app icon on the bar),
  and drains while the trajectory heads back within 1.5 s. A still pointer just outside closes it
  in about `folderLeaveDelayMs`; a decisive move away in about 0.03–0.10 s. Heading into another
  folder icon switches to it instead. Heading down onto the dock bar holds off the drop until
  arrival, shrinks the panel into its icon, keeps the dock up and centred (easing any residual
  switch offset back to 0 as the panel shrinks), and lets the magnification wave follow the
  pointer immediately; leaving any other way (or a click outside, Esc, launching from it) drops
  dock and panel off the screen together as one rigid unit in 0.15 s with immediate frame-1 launch.
  During that drop nothing brings them back; resting on the bottom edge brings the dock back alone.
- Sub-folders open in the same panel; use the back arrow, the breadcrumb or Backspace. The panel
  widens to fit the breadcrumb up to the window width; if a path is even longer, the back button
  stays pinned on the left and middle crumbs elide first. A level change that shrinks the panel away
  from the pointer does not count as leaving it. A click-opened folder takes keyboard focus so Esc
  works at once; a hover-opened one only after you click it. Clicking the open folder's icon closes
  it, and it does not reopen until the pointer leaves the icon.
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
  only scaled (GPU transform, no relayout), and only the wave and intent maths run per rendered
  frame: pointer events only store the pointer and one `FrameAnimation` derives the velocity,
  updates the intent, advances the wave and lays out once per frame (it stops when settled). Shadows are `RectangularShadow` (one analytic shader quad, no offscreen
  blur pass), so resizing the bar per frame stays cheap. The layer surface is 1400x680
  (wider only for very long rows; it changes with the config, never per frame), transparent, and its
  input region is just the dock zone (a 4 px strip while hidden) plus the panel once a folder
  starts to open, so it never blocks other windows. A persistent full-screen backdrop layer
  (`WlrLayer.Top`, unmapped when suppressed, zero-size input mask unless a folder is open) tracks
  the pointer for the close intent and takes clicks outside without a per-open EGL surface
  allocation hitch. The compositor applies a grown input region a frame late, so a leave right
  after the region grew is checked again 50 ms later instead of hiding at once.
- Window state, urgency and the config are event driven (foreign-toplevel signals, one long-lived
  `niri msg event-stream`, file watching). Directory existence is checked on config load and each
  time the dock is revealed, so a late mount shows up without polling.
- Not done: drag reordering (locked by design), tray, show-dock hotkey, per-monitor docks (it uses
  `behavior.output` or the first screen), right-click menus.
