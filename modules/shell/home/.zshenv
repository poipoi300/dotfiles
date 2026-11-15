# Read by every zsh (login, interactive, scripts): keep it to environment only.
typeset -U path PATH
path=("$HOME/.local/bin" $path)
export EDITOR=micro VISUAL=micro
