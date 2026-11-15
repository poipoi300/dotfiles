# browser

Zen browser with its extensions and the yt-dlp downloader helper.

- `system/etc/zen/policies/policies.json` is Zen's system policy file (it replaces the package's
  bundled `distribution/policies.json`, so it repeats `DisableAppUpdate`). Extensions listed under
  `ExtensionSettings` with `normal_installed` are installed from addons.mozilla.org on Zen's next
  start and can be disabled but not removed. Check `about:policies` after a change.
- The French language pack is pinned to a file for Firefox 160, Zen's current Gecko base. Language
  packs only load on an exact version match: when Zen moves to a new Gecko version
  (`/opt/zen-browser-bin/platform.ini`, `Milestone`), pick the matching file from
  `https://addons.mozilla.org/api/v5/addons/addon/langpack-fr@firefox.mozilla.org/versions/`.
- `pkg/yt-dlp-firefox-host` installs the native messaging host of the "yt-dlp downloader"
  extension in `/usr/lib/mozilla/native-messaging-hosts`, which all Gecko browsers read. It is
  pinned to an upstream commit; "show in folder" opens the folder with `xdg-open`.
- `layout.frame_rate` defaults to -1 (vsync). The profile itself (bookmarks, settings, sessions)
  is personal data and is not in this repository.
