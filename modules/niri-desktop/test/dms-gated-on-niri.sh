#!/bin/sh
# DMS must only start inside a niri session. niri.service BindsTo
# graphical-session.target, so the dms.service that wins must be Requisite=,
# After= and PartOf= that target, enabled only from it (default.target ran it at
# login with no compositor, crash-looping until niri came up).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
user_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
fail=0
no() { echo "FAIL: $*"; fail=1; }

# A unit in ~/.config/systemd/user shadows the packaged one: neither this
# module nor a restored arcus backup may put one there.
[ -e "$here/../home/.config/systemd/user/dms.service" ] && no "module ships a dms.service override"
{ [ -e "$user_dir/dms.service" ] || [ -L "$user_dir/dms.service" ]; } && no "$user_dir/dms.service shadows the packaged unit"

unit=/usr/lib/systemd/user/dms.service
[ -e "$unit" ] || { echo "FAIL: dux-shell's dms.service is not installed"; exit 1; }
for line in Requisite After PartOf; do
    grep -qx "$line=graphical-session.target" "$unit" || no "$unit lacks $line=graphical-session.target"
done
grep -qx 'WantedBy=graphical-session.target' "$unit" || no "$unit is not WantedBy=graphical-session.target"

# Live enablement: graphical-session.target.wants is required and the only puller.
[ -e "$user_dir/graphical-session.target.wants/dms.service" ] || no "dms.service is not enabled"
for link in "$user_dir"/*.wants/dms.service; do
    [ -e "$link" ] || [ -L "$link" ] || continue
    case $link in
        */graphical-session.target.wants/dms.service) ;;
        *) no "stray enablement $link" ;;
    esac
done

[ $fail = 0 ] && echo "PASS: dms.service is gated on the niri session"
exit $fail
