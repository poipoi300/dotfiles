
## Build order

`kcc` depends on the own build `python-mozjpeg-lossless-optimization`. arcus builds all own
builds before installing any, so on a fresh machine apply once with only the library in `local`,
then add `kcc` and apply again.

## T3 Code

`[appimages.t3code]` installs the vendor AppImage (official `pingdotgg/t3code` GitHub release,
pinned sha256 from the release's published digest) at `~/Applications/T3-Code.AppImage`. It is
`self_update`: after the first install the app's own updater replaces the file in place and arcus
leaves its content alone, so the pin only matters on a fresh machine. Bump it to the latest
release now and then. `~/.local/bin/t3code` launches it, and `t3code.desktop` puts it in
fuzzel. The app writes its own hidden `com.t3tools.T3Code.desktop` on every launch as the
`t3code://` handler; that file is app state, not part of this module.
