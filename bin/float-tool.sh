#!/usr/bin/env bash
# float-tool.sh — 状态栏上 `git` / `文件` / `轨迹` 按钮背后的东西（`轨迹` 转给
# claude-tmux-sessions/sidebar/trail-sidebar.sh，是侧栏不是浮窗）：在当前 Claude 所在的目录，
# 用浮动 pane（tmux 3.7+）整窗弹出 lazygit 或 yazi（文件浏览），点外面就关。
#
#   float-tool.sh <caller-pane> git|files   (tool:git 等带前缀的也认)
#   float-tool.sh --buttons      状态栏上的按钮（status-left 里用 #() 调）
#
# 和 claude-tmux-sessions 的 float-picker.sh 是同一套约定，所以几个浮窗能互相切换：
#   @picker_float  这个浮窗是什么（picker 用 all/done/…，这里用 tool:git / tool:files）
#   @picker_caller 它是从哪个 pane 打开的（浮窗开着时它自己就是活动 pane，状态栏点击
#                  报上来的 caller 会是浮窗本身，得换回原来那个）
# 再点同一个按钮 = 关；点另一个 = 换过去。
#
# 「文件」用 yazi：浮窗里只看不编辑——点外面就会关掉浮窗，用编辑器的话没存的改动会
# 被一起关掉；yazi 原生支持鼠标（单击选中、滚轮翻页、右边直接预览内容）。要编辑或复制
# 就右键点文件：在新 tmux 窗口里用编辑器打开（--edit，见 yazi/yazi.toml）。

set -uo pipefail
# 装好以后这个脚本是 ~/.claude/hooks/float-tool.sh 这条软链：找回仓库里的真身，同仓库的 sys-monitor.py、
# sidebar/ 才找得到。
self="$0"; [ -L "$self" ] && self="$(readlink "$self")"
BIN="$(cd "$(dirname "$self")" && pwd)"
. "$BIN/../lib/i18n.sh"

# 按钮得由脚本输出：oh-my-tmux 按 `|` 切状态栏的段，而 #[range=user|…] 里正好有 `|`，
# 写死在 tmux.conf.local 里会被它拆坏；#() 的输出它不碰。
if [ "${1:-}" = --buttons ]; then
  chip='#[bg=#303030,fg=#d0d0d0]'
  # git / 文件 搬到侧栏表头上了（只跟那个 Claude 有关，2026-10-22 用户：「底部的这个 Git 文件是不是就可以撤销了」），
  # 底栏只留开关侧栏这一个，图标是 Nerd Font 的 cod-layout_sidebar_right（U+EBF4，右边带侧栏的窗口）。
  printf '%s' "#[range=user|tool:trail]${chip}  #[default]#[norange]"
  # 在「这个」和「上一个」session 之间来回切（双线工作最常见）。点击直接走 tmux 的 switch-client -l，
  # 见 tmux.conf.local 的 MouseDown1Status。只画图标、不写 session 名：写了名字，看着就像「当前 session 叫这个」
  # （2026-10-23 用户原话），而它其实是点了要去的那个。
  # 没有上一个（刚开机、只有一个 session）就画暗的图标，点了什么也不发生。
  # #() 里没有「是哪个客户端」的信息，display -p 取的是最近活动的那个；单客户端（你只有一个 iTerm 窗口）下没问题。
  last=$(tmux display -p '#{client_last_session}' 2>/dev/null)
  swap=$(printf '\356\257\214')   # U+EBCC cod-arrow-swap，写成 UTF-8 字节：macOS 自带 bash 3.2 不认 \u
  if [ -n "$last" ]; then
    printf '%s' " #[range=user|tool:swap]${chip} ${swap} #[default]#[norange]"
  else
    printf '%s' " #[range=user|tool:swap]#[bg=#303030,fg=#6c6c6c] ${swap} #[default]#[norange]"
  fi
  exit 0
fi

# 浮窗的 pane-focus-out 钩子走到这里：判断这次失焦到底是不是「点了浮窗外面」。
# tmux 开着 focus-events，整个终端窗口失焦（全屏 iTerm 切去 Chrome、切去别的 App）
# 也会给活动 pane 发一次 pane-focus-out，和点浮窗外面长得一模一样。以前钩子里直接
# kill-pane，于是「切出去再切回来，浮窗自己没了」——2026-09-28 用户报的就是这个。
# 现在等 0.4 秒再看：浮窗仍是它那个窗口的活动 pane、那个窗口也仍是当前窗口，就说明
# 没人点别处，是终端自己失焦，留着不动；焦点真挪到了别的 pane/窗口才关。
if [ "${1:-}" = --maybe-close ]; then
  fp="$2"
  sleep 0.4
  still=$(tmux display -p -t "$fp" '#{&&:#{pane_active},#{window_active}}' 2>/dev/null) || exit 0
  [ "$still" = 1 ] && exit 0
  tmux kill-pane -t "$fp" 2>/dev/null || true
  exit 0
fi

# tmux.conf.local 的 MouseDown1Pane 在「点了浮窗外面」时调这里：关掉这个窗口里的 git / 文件 / 资源弹窗。
# picker 的浮窗（@picker_float 是 all/done/…）不归这里管，它有自己的钩子。
if [ "${1:-}" = --close-tools ]; then
  tmux list-panes -t "$2" -F '#{pane_id} #{@picker_float}' 2>/dev/null |
    awk '$2 ~ /^tool:/{print $1}' | while read -r p; do tmux kill-pane -t "$p" 2>/dev/null; done
  exit 0
fi

# yazi 里「打开」文件（右键点它、或回车；见 yazi/yazi.toml）走到这里：浮窗一点外面就关，
# 不能在里面编辑，所以在调用方那个 session 新开一个窗口跑编辑器，再关掉浮窗。
if [ "${1:-}" = --edit ]; then
  shift
  [ $# -gt 0 ] || exit 0
  self="${TMUX_PANE:-}"
  target=$(tmux show -pqv -t "$self" @picker_caller 2>/dev/null)
  target="${target:-$self}"
  session=$(tmux display -p -t "$target" '#{session_name}')
  tmux new-window -t "$session:" -c "$(dirname "$1")" "${EDITOR:-nvim}" "$@"
  [ -n "$(tmux show -pqv -t "$self" @picker_float 2>/dev/null)" ] && tmux kill-pane -t "$self"
  exit 0
fi

# git 按钮点在一个不是仓库的目录（比如 ~/work，下面一堆仓库）：lazygit 会直接报错。
# 改成在浮窗里往下找 3 层的仓库，一个就直接开，多个就列出来单击选（最近动过的在上面），
# 一个都没有就说一声。
if [ "${1:-}" = --pick-repo ]; then
  dir="$2"
  shown="$dir"; case "$dir" in "$HOME"/*) shown="~${dir#"$HOME"}" ;; esac
  repos=()
  while IFS= read -r r; do repos+=("$r"); done < <(
    find "$dir" -maxdepth 4 \( -name node_modules -o -name .venv -o -name venv \) -prune \
      -o -name .git -print 2>/dev/null |
    while IFS= read -r g; do printf '%s\t%s\n' "$(stat -f %m "$g" 2>/dev/null || echo 0)" "${g%/.git}"; done |
    sort -rn | cut -f2)
  if [ ${#repos[@]} -eq 0 ]; then
    t '%s 不是 git 仓库，下面 3 层也没有仓库。'; msg="$T"; t '按任意键或点浮窗外面关闭。'
    printf "\n  $msg\n\n  %s\n" "$shown" "$T"
    # -sn1：Esc 也算数。`read -r` 等的是一整行，按 Esc 什么也不会发生。
    read -rsn1 _
    exit 0
  fi
  if [ ${#repos[@]} -eq 1 ]; then
    exec lazygit -p "${repos[0]}"
  fi
  t '仓库'; prompt="$T > "; t '%s 不是 git 仓库，点一个打开：'; header=$(printf "$T" "$shown")
  pick=$(printf '%s\n' "${repos[@]#"$dir"/}" |
    fzf --reverse --no-sort --info=hidden --prompt="$prompt" \
        --header="$header" --bind 'left-click:accept') || exit 0
  exec lazygit -p "$dir/$pick"
fi

# The binding passes the status range as is (`tool:git`): stripping the prefix
# in the tmux format with #{s|…||:…} came out empty inside the nested if.
caller="$1"; tool="${2#tool:}"; kind="tool:$tool"

if [ -n "$(tmux show -pqv -t "$caller" @picker_float 2>/dev/null)" ]; then
  orig=$(tmux show -pqv -t "$caller" @picker_caller 2>/dev/null)
  [ -n "$orig" ] && caller="$orig"
fi

# 「轨迹」不是浮窗：是贴在 Claude 窗格右边、一直开着的侧栏（每一轮的最终回复），
# 开关和其余逻辑都在 claude-tmux-sessions/sidebar 里。
if [ "$tool" = trail ]; then
  exec "$BIN/../sidebar/trail-sidebar.sh" "$caller"
fi

window=$(tmux display -p -t "$caller" '#{window_id}')

read -r open open_kind < <(tmux list-panes -t "$window" -F '#{pane_id} #{@picker_float}' \
  | awk '$2!=""{print; exit}')
if [ -n "${open:-}" ]; then
  tmux kill-pane -t "$open"
  [ "$open_kind" = "$kind" ] && exit 0
fi

dir=$(tmux display -p -t "$caller" '#{pane_current_path}')
case "$tool" in
  git)   if git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
           cmd="lazygit -p '$dir'"
         else
           cmd="'$(cd "$(dirname "$0")" && pwd)/$(basename "$0")' --pick-repo '$dir'"
         fi ;;
  files) cmd="cd '$dir' && yazi" ;;
  sys-*) cmd="python3 '$BIN/sys-monitor.py' --only ${tool#sys-}" ;;
  *)     exit 2 ;;
esac

# 这两个要的是空间，不是贴合内容：整窗铺满。tmux 允许的最大浮动 pane 是
# 宽 window_width-1、高 window_height-2（上下两行留给边框），实测再大就报
# "size or position too large"。
win_h=$(tmux display -p -t "$caller" '#{window_height}')
win_w=$(tmux display -p -t "$caller" '#{window_width}')
h=$(( win_h - 2 ))
y=1
# 一律浮在右边、贴右边缘，宽度占窗口的百分之几（默认 60，lazygit 要这么宽才排得下；
# 改 ~/.local/state/float-tool-width 里的数字即可）。浮着不挤 Claude 的窗格，左边的 Claude 宽度不变、
# 不会重新换行。以前还有「整窗铺满」，可铺满了就没有「浮窗外面」可点，而侧栏上的 git 按钮
# 又正好被浮窗盖住，只能回底栏去点（2026-10-22 用户：「全屏我怎么点浮窗外呢？点不了」），去掉了。
pct=$(cat "${XDG_STATE_HOME:-$HOME/.local/state}/float-tool-width" 2>/dev/null)
case "$pct" in ''|*[!0-9]*) pct=60 ;; esac
[ "$pct" -lt 30 ] && pct=30; [ "$pct" -gt 95 ] && pct=95
w=$(( (win_w - 1) * pct / 100 )); x=$(( win_w - 1 - w ))

# 资源监视（sys-cpu / sys-mem / sys-swap / sys-disk）：只显示那一项，小窗贴在右下角、紧挨状态栏。
# 不按上面的宽度。点浮窗外面就关，再点同一段也关。
case "$tool" in
  sys-*)
    case "$tool" in sys-disk) h=44; w=108 ;; sys-cpu) h=36; w=108 ;; *) h=38; w=108 ;; esac
    [ "$h" -gt $(( win_h - 2 )) ] && h=$(( win_h - 2 ))
    [ "$w" -gt $(( win_w - 1 )) ] && w=$(( win_w - 1 ))
    x=$(( win_w - 1 - w )); y=$(( win_h - 1 - h )); [ "$y" -lt 1 ] && y=1 ;;
esac

fp=$(tmux new-pane -t "$caller" -P -F '#{pane_id}' -x "$w" -y "$h" -X "$x" -Y "$y" \
  -c "$dir" "$cmd") || exit 1
tmux set -p -t "$fp" @picker_float "$kind"
tmux set -p -t "$fp" @picker_caller "$caller"
# `--edit` 分支里的 $self 是 pane，这里是脚本自己，别混。
script=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
# 点浮窗外面就关（左边的 Claude、别的窗格）：用户不需要开着它跟 Claude 打字（2026-10-22）。
tmux set-hook -p -t "$fp" pane-focus-out \
  "run-shell -b '\"$script\" --maybe-close $fp'"
# Claude Code 建 teammates 的时候会把这个浮窗吸进平铺布局，变成右边一个普通窗格
# （2026-09-29 用户报，已复现）。根因在它那边：`TmuxBackend.createTeammatePaneWithLeader`
# 用 `tmux list-panes` 数窗格，而 list-panes 会把浮动窗格一起列出来；随后
# `rebalancePanesWithLeader` 的 `select-layout main-vertical` 就把浮窗当成平铺树里的一格
# 摆走了（实测 pane_floating_flag 从 1 变成 0）。
# 我们这边拦不住那次 select-layout，但可以让浮窗在被摆走的那一刻自己关掉 —— 结果是
# picker 干脆消失，而不是留下一个乱七八糟的窗格。
# 判据是「这一格不再是浮的」，不是「布局变了」：浮窗自己被 new-pane 建出来时也会触发
# 这个钩子，用布局变没变做判据它会当场自杀。
# 挂成窗格级钩子（-p），所以 if -F 判的就是这一格自己，全程 tmux 内部完成，不起进程。
tmux set-hook -p -t "$fp" window-layout-changed \
  "if -F '#{!:#{pane_floating_flag}}' { run-shell -b \"tmux kill-pane -t $fp 2>/dev/null; true\" }"
