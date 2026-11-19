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

1. checks that the checkout is on the default branch with nothing in progress and that
   its git identity is the GitHub noreply address, then commits a pending approvals
   change and pulls this repository;
2. `sudo pacman -Syu`, `rustup update stable`, `aurlock upgrade`, `claude update`;
3. rechecks the checkout, commits new approvals and pushes them to the upstream branch
   only, when every unpushed commit is an `aurlock: approvals` commit by the noreply
   identity touching only the approvals file, every line of that file has the format
   aurlock writes (comments only when the remote already has them), and gitleaks passes;
4. ends with what `arcus plan` says; it never applies.

An approval pulled from another machine upgrades without a prompt while its commit is
still the AUR head; if the AUR moved on, aurlock shows the diff since the approved
commit. `aurlock upgrade` only upgrades installed packages, so a package another machine
added installs on `arcus apply`. Exit status: aurlock's when it failed, else 1 when the
approvals couldn't be committed or pushed.

Other uncommitted work in the checkout is left alone: the pull fast-forwards around it,
and rebasing local commits onto the other machines' waits for a clean checkout. Packages
pacman owns are never self-updated (`codex update`, `agy update`), and own builds in
`pkg/` update when their PKGBUILD is bumped. Two machines approving different packages
both append to the file, so `.gitattributes` gives it `merge-approvals`, a per-package
three-way merge driver that `update` registers in the clone's git config; one package
approved at different commits on two machines is a conflict to resolve by hand, leaving
one line per package.

Tests: `python3 -I -B -m unittest discover -s modules/aurlock/test` (temporary
repositories, every system tool stubbed).
