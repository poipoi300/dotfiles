# autoscroll

Windows-style middle-button autoscroll in every app.

- `pkg/midscroll` packages [midscroll](https://github.com/gnhen/midscroll) from its release
  tarball (pinned sha256). A root service grabs the mice through evdev and re-emits them through
  a uinput clone, adding wheel events. Hold the middle button and drag: the scroll speed follows
  the distance from where you pressed (Chromium's Windows curve), keeps going while you hold
  still, and stops on release. A click without a drag still reaches the app as a middle click.
  The settings GUI, its polkit action and `midscroll-apply` are not packaged.
- `niri.patch` adds niri support to the session helper (`midscroll-overlay`, a user unit):
  - The focused window comes from one long-lived `niri msg --json event-stream` (upstream polls
    kdotool or xprop every second). The daemon pauses while a `BLACKLIST` app is focused, so apps that use
    middle-drag themselves (Blender, games) get the raw button.
  - niri has no IPC for the pointer position. When a drag starts, the full-screen overlay takes
    pointer input until niri sends it an enter event carrying the position (one or two frames),
    then turns click-through again. The app under the pointer sees a brief leave/enter.
  - It draws a ring with four arrowheads at the press point and, at the hand position, a
    triangle pointing the way it scrolls (a dot inside the dead zone). The real pointer stays
    visible at the ring: on Wayland only the app under the pointer chooses its image.
  - The overlay opens on niri's focused output, so with several monitors the marks only show on
    that one (scrolling works everywhere). Upstream's badge and ghost-cursor code is left in the
    file unused, to keep the patch small.
- `system/etc/midscroll.conf` holds the settings. Restart after editing:
  `sudo systemctl restart midscroll` and `systemctl --user restart midscroll-overlay` (the helper
  reads the dead zone, speed curve and `GHOST_SCALE` for its marks). `GHOST_CURSOR` turns the marks on or off. `TOGGLE_MODE = true` switches
  to click-to-start/click-to-stop everywhere, but then middle click no longer opens links in new
  tabs.
- Click-to-autoscroll (a click on an empty spot starts scrolling, a click on a link still
  opens it in a new tab) is the apps' own feature: Zen has `general.autoScroll` (browser
  module), and Chromium/Electron apps get `--enable-blink-features=MiddleClickAutoscroll`,
  through `~/.config/electron-flags.conf` for Arch's `electronNN` wrappers and through a
  desktop-file override for Discord, which ships its own Electron. Restart those apps after a
  change.
