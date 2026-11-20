#!/bin/sh
# Regression test: a tty1 zsh login must reach niri-session's systemd step.
# niri-session re-execs itself through `$SHELL -l -c "exec niri-session -l"`
# unless called with -l; that nested login shell sources .zprofile again, so
# .zprofile must not send it round the loop.
#
# Runs the real /usr/bin/niri-session up to its service-manager section, with
# a depth guard, against this module's .zprofile in a scratch HOME.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
zsh=$(command -v zsh) || { echo "SKIP: zsh not installed"; exit 0; }
[ -x /usr/bin/niri-session ] || { echo "SKIP: niri-session not installed"; exit 0; }
# The re-exec (and so the loop) only happens when $SHELL is a listed shell.
grep -qx "$zsh" /etc/shells || { echo "FAIL: $zsh not in /etc/shells, test cannot exercise the re-exec"; exit 1; }
# The stub keeps niri-session only up to this marker, so it never runs systemctl.
grep -q '^# Try to detect' /usr/bin/niri-session || { echo "FAIL: niri-session layout changed, update the stub"; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin" "$tmp/home"
cp "$here/../home/.zprofile" "$tmp/home/.zprofile"
{
    echo '#!/bin/sh'
    echo "n=\$((\$(cat '$tmp/count' 2>/dev/null || echo 0) + 1)); echo \$n > '$tmp/count'"
    echo '[ "$n" -gt 10 ] && { echo "FAIL: exec loop (depth $n)"; exit 1; }'
    sed -n '2,/^# Try to detect/p' /usr/bin/niri-session
    echo 'echo "PASS: reached the systemd step at depth $n"; exit 0'
} > "$tmp/bin/niri-session"
chmod +x "$tmp/bin/niri-session"

cd "$tmp/home"
env -i HOME="$tmp/home" ZDOTDIR="$tmp/home" XDG_VTNR=1 SHELL="$zsh" \
    PATH="$tmp/bin:/usr/bin" timeout 20 "$zsh" -l -c 'echo "FAIL: .zprofile did not start niri-session"; exit 1' </dev/null
