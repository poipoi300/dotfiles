# autoscroll

Windows-style middle-button autoscroll in every app.

- `pkg/midscroll` packages [midscroll](https://github.com/gnhen/midscroll) from its release
  tarball (pinned sha256). A root service grabs the mice through evdev and re-emits them through
  a uinput clone, adding wheel events. Hold the middle button and drag: the scroll speed follows
  the distance from where you pressed (Chromium's Windows curve), keeps going while you hold
  still, and stops on release. A click without a drag still reaches the app as a middle click.
  The settings GUI, its polkit action and `midscroll-apply` are not packaged.
- `niri-focus.patch` lets the session helper (`midscroll-overlay`, a user unit) report the
  focused window from `niri msg --json focused-window`. Upstream only knows kdotool (KWin) and
  xprop. The daemon pauses while a `BLACKLIST` app is focused, so apps that use middle-drag
  themselves (Blender, games) get the raw button.
- `system/etc/midscroll.conf` holds the settings. Restart after editing:
  `sudo systemctl restart midscroll`. The ghost cursor is off because on niri the helper
  can't read the pointer position, so nothing would be drawn. `TOGGLE_MODE = true` switches
  to click-to-start/click-to-stop everywhere, but then middle click no longer pastes or opens
  links in new tabs.
- Click-to-autoscroll (a click on an empty spot starts scrolling, a click on a link still
  opens it in a new tab) is the apps' own feature: Zen has `general.autoScroll` (browser
  module), and Chromium/Electron apps get `--enable-blink-features=MiddleClickAutoscroll`,
  through `~/.config/electron-flags.conf` for Arch's `electronNN` wrappers and through a
  desktop-file override for Discord, which ships its own Electron. Restart those apps after a
  change.
