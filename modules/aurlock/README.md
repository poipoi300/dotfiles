# aurlock

[aurlock](https://github.com/GrunclePug/aurlock) is the AUR helper
(`aur_helper` in `config.toml`). It installs an AUR package only at a commit
approved in `home/.config/aurlock/approved`, which is tracked here: every
approval shows up as a diff in this repository. Updates show only the diff
since the approved commit, red flags need the package name typed, and builds
run offline in bubblewrap with `$HOME` hidden.

`arcus apply -y` installs approved packages and skips the rest; a plain
`arcus apply` (or `aurlock install <pkg>`) prompts for review.

Day to day:

    sudo pacman -Syu && aurlock upgrade   # AUR updates, each reviewed as a diff
    aurlock outdated                      # what's waiting
    aurlock list                          # approvals and untracked AUR packages

`home/.config/aurlock/local` names the own builds from the modules' `pkg/`
directories; `aurlock list` shows them as local builds and never offers them
for adoption, even when the AUR has a package of the same name. Add a name
there when a module gains a `pkg/` directory.

aurlock upgrades only packages it installed. Packages yay installed before the
switch are adopted once with `aurlock install <pkg>...` (review and rebuild);
`aurlock list` shows which are still untracked. yay stays in base-cli until
none are left.
