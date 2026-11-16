Requires Wallpaper Engine (Steam app 431960) installed through Steam on Linux so
that steamapps/common/wallpaper_engine/assets exists, and the wanted wallpapers
are downloaded under steamapps/workshop/content/431960.

Launch WPE Gallery to choose an installed wallpaper and a connected display.
It writes ~/.config/linux-wallpaperengine/assignments.json and restarts the
linux-wallpaperengine user unit. Each output keeps its own wallpaper; the launcher
builds one renderer invocation with repeated --screen-root / --bg pairs.
WPE_FPS from wallpaper.env and fullscreen pause behaviour are preserved.
Per-wallpaper properties are reserved until the renderer supports their scope.

Until the first Apply, wallpaper.env supplies WPE_BG and optional WPE_OUTPUT.
Delete assignments.json to return to that legacy configuration.
Do not run alongside another wallpaper renderer on the same output.

The launcher now requires wpe-gallery-launch (gallery 0.2 or newer). Both GUI and
launcher require a safe, hash-matching review in ~/.config/wpe-gallery/whitelist.json.
This module supplies no whitelist; wire private reviews separately. Missing or
changed reviews fail closed. On a rejected requested assignment, the launcher
revalidates the corresponding last-safe assignment before falling back, logging
the reason. If no output verifies, no renderer starts.
