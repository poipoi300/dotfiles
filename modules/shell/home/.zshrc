# Interactive zsh. Kept lean on purpose: no framework, starts in a few milliseconds.

# History
HISTFILE=${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history
[[ -d ${HISTFILE:h} ]] || mkdir -p ${HISTFILE:h}
HISTSIZE=100000 SAVEHIST=100000
setopt share_history hist_ignore_all_dups hist_ignore_space hist_reduce_blanks extended_history

# Behaviour
setopt auto_cd interactive_comments no_beep
bindkey -e
bindkey '^[[1;5C' forward-word '^[[1;5D' backward-word '^[[H' beginning-of-line '^[[F' end-of-line '^[[3~' delete-char

# Completion: rebuild the dump at most once a day.
fpath=(/usr/share/zsh/site-functions $fpath)
autoload -Uz compinit
_zcomp=${XDG_CACHE_HOME:-$HOME/.cache}/zsh/zcompdump
[[ -d ${_zcomp:h} ]] || mkdir -p ${_zcomp:h}
if [[ -n $_zcomp(#qN.mh+24) || ! -e $_zcomp ]]; then compinit -d $_zcomp; else compinit -C -d $_zcomp; fi
unset _zcomp
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'
zstyle ':completion:*' list-colors ${(s.:.)LS_COLORS}

# Aliases
alias ls='ls --color=auto' ll='ls -lh --color=auto' la='ls -lAh --color=auto'
alias grep='grep --color=auto'
alias zed=zeditor

# Tools (each init is cheap; uv's completion is cached and regenerated when uv changes)
source <(fzf --zsh)
eval "$(zoxide init zsh)"
_uvc=${XDG_CACHE_HOME:-$HOME/.cache}/zsh/uv-completion.zsh
if [[ ! -s $_uvc || /usr/bin/uv -nt $_uvc ]]; then uv generate-shell-completion zsh >| $_uvc; fi
source $_uvc; unset _uvc
eval "$(starship init zsh)"

# Plugins last (syntax highlighting must be the final one).
source /usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh
source /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

# Private helpers (gpt-claude model-alias functions) when the private scripts repo is cloned.
[[ -r ~/src/scripts/shell/gpt-claude.zsh ]] && source ~/src/scripts/shell/gpt-claude.zsh
