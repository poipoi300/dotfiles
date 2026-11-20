# shell

zsh as the login shell, without a framework: autosuggestions, syntax highlighting, extra
completions, fzf (Ctrl-R history, Ctrl-T files, Alt-C dirs), zoxide (`z`), starship prompt.
History lives in `~/.local/state/zsh/history`.

`~/.zprofile` runs `exec niri-session -l` on tty1, so the desktop starts as a systemd user session.
The `-l` matters: without it niri-session re-execs `$SHELL -l`, which sources `.zprofile` again and
loops forever (`test/zprofile-niri-session.sh` guards this).

arcus doesn't change login shells; after turning the module on, once:
`sudo chsh -s /usr/bin/zsh "$USER"`.
