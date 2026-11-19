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

    update                                # the whole system, approvals synced (below)
    sudo pacman -Syu && aurlock upgrade   # the same upgrades without the sync
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

## update

`update` (`home/.local/bin/update`, adapted from GrunclePug's) keeps every machine on
the same approved AUR commits:

1. commits a pending change to the approvals file and pulls this repository, so
   approvals made on another machine install without asking;
2. `sudo pacman -Syu`, `rustup update stable`, `aurlock upgrade` (review prompts as
   usual), `claude update`;
3. commits new approvals and pushes them, only when every unpushed commit touches
   nothing but the approvals file, every added line has the approvals format, and
   gitleaks passes;
4. ends with what `arcus plan` says; it never applies.

Other uncommitted work in the checkout is left alone: the pull fast-forwards around it
or stops. Packages pacman owns are never self-updated (`codex update`, `agy update`),
and own builds in `pkg/` update when their PKGBUILD is bumped. Two machines approving
different packages both append to the file, so `.gitattributes` gives it
`merge-approvals`, a per-package three-way merge driver that `update` registers in the
clone's git config; the same package approved differently on two machines is a
conflict to resolve by hand. Each clone needs the noreply commit identity in its git
config for the approvals commit.

Tests: `python3 -I -B -m unittest discover -s modules/aurlock/test` (temporary
repositories, every system tool stubbed).
