
## Build order

`kcc` depends on the own build `python-mozjpeg-lossless-optimization`. arcus builds all own
builds before installing any, so on a fresh machine apply once with only the library in `local`,
then add `kcc` and apply again.

## T3 Code

T3 Code runs from its vendor AppImage at `~/Applications/T3-Code.AppImage` (not in git; download
it from the official `pingdotgg/t3code` GitHub releases). Keep the file name without a version:
the built-in updater then replaces the file in place. `~/.local/bin/t3code` launches it, and
`t3code.desktop` puts it in fuzzel. The app writes its own hidden
`com.t3tools.T3Code.desktop` on every launch as the `t3code://` handler; that file is app state,
not part of this module.
