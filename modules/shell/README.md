# shell

zsh as the login shell, without a framework: autosuggestions, syntax highlighting, extra
completions, fzf (Ctrl-R history, Ctrl-T files, Alt-C dirs), zoxide (`z`), starship prompt.
History lives in `~/.local/state/zsh/history`.

`~/.zprofile` runs `exec niri-session` on tty1, so the desktop starts as a systemd user session.

arcus doesn't change login shells; after turning the module on, once:
`sudo chsh -s /usr/bin/zsh "$USER"`.
