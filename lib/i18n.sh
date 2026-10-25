# i18n.sh — 界面文字的中英切换（shell 这一半；Python 那一半是同目录的 i18n.py）。用 `.` 引入。
#
#   t "等确认"; echo "$T"      → 等确认 / Waiting
#
# 中文原文就是键：zh 时 T 就是原文（和接入之前一字不差），en 时去同目录 en.tsv 里查，查不到也是原文。
# 结果放在变量 T 里而不是打印出来：$(t …) 要 fork 一个子 shell，picker 的按键处理一秒能跑几十次，
# 这里一个进程都不起（bash 3.2 没有关联数组，所以 en 时是逐行读词表，读到就停）。
#
# 取语言的顺序：环境变量 CLAUDE_TMUX_LANG > ~/.config/claude-tmux/lang > 默认 zh。结果在 CT_LANG 里（en / zh）。
# 要 bash（用了 BASH_SOURCE、$'\t'）；macOS 自带的 bash 3.2 就行。

CT_I18N_DIR="${BASH_SOURCE[0]%/*}"
[ "$CT_I18N_DIR" = "${BASH_SOURCE[0]}" ] && CT_I18N_DIR=.
CT_I18N_TSV="$CT_I18N_DIR/en.tsv"
CT_LANG_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/claude-tmux/lang"

ct_norm_lang() {  # ct_norm_lang <值> → CT_NORM=en|zh|空
  case "$1" in
    en*|EN*|En*) CT_NORM=en ;;
    '') CT_NORM= ;;
    *) CT_NORM=zh ;;
  esac
}

ct_norm_lang "${CLAUDE_TMUX_LANG:-}"
CT_LANG="$CT_NORM"
if [ -z "$CT_LANG" ] && [ -r "$CT_LANG_FILE" ]; then
  _ct_l=; IFS= read -r _ct_l < "$CT_LANG_FILE" || true
  ct_norm_lang "$_ct_l"; CT_LANG="$CT_NORM"
fi
[ -n "$CT_LANG" ] || CT_LANG=zh

t() {
  T="$1"
  [ "$CT_LANG" = en ] || return 0
  local _k _v
  while IFS=$'\t' read -r _k _v; do
    if [ "$_k" = "$1" ]; then T="$_v"; return 0; fi
  done < "$CT_I18N_TSV"
  return 0
}

# 安装时选语言（install.sh、sidebar/install.sh 都调）：
#   ct_lang_setup <en|zh|空>
# 给了值就写进 $CT_LANG_FILE；没给而文件已经有了就不动；都没有就看 $LANG 和 macOS 的系统语言列表，
# 含 zh 用 zh，否则 en。之后 CT_LANG 就是选定的语言（环境变量 CLAUDE_TMUX_LANG 仍然优先）。
ct_lang_setup() {
  local want="${1:-}" cur=
  case "$want" in
    ''|en|zh) ;;
    *) echo "--lang: en | zh (got: $want)" >&2; return 2 ;;
  esac
  if [ -n "$want" ]; then
    mkdir -p "${CT_LANG_FILE%/*}"
    printf '%s\n' "$want" > "$CT_LANG_FILE"
  elif [ -s "$CT_LANG_FILE" ]; then
    IFS= read -r cur < "$CT_LANG_FILE" || true
    ct_norm_lang "$cur"
    want="${CT_NORM:-zh}"
  else
    want=en
    case "${LANG:-}" in *zh*|*ZH*) want=zh ;; esac
    if [ "$want" = en ] && command -v defaults >/dev/null 2>&1; then
      case "$(defaults read -g AppleLanguages 2>/dev/null)" in *zh*) want=zh ;; esac
    fi
    mkdir -p "${CT_LANG_FILE%/*}"
    printf '%s\n' "$want" > "$CT_LANG_FILE"
  fi
  ct_norm_lang "${CLAUDE_TMUX_LANG:-}"
  CT_LANG="${CT_NORM:-$want}"
  return 0
}
