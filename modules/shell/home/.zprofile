# Login shell: on tty1, start the niri session (systemd user session, so DMS, the dock and
# other user services get WAYLAND_DISPLAY). Other ttys stay plain shells.
if [[ -z $WAYLAND_DISPLAY && $XDG_VTNR == 1 ]] && command -v niri-session >/dev/null; then
    exec niri-session
fi
