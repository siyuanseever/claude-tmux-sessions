#!/usr/bin/env bash
# trail-sidebar.sh — Claude 窗格右边的多轮对话导航栏，全程用鼠标：每项一轮（时间正序，打开时
# 落在最后一轮），滚轮翻；单击一项，侧栏里展开你那一轮说的话，主界面的 Claude 滚到那一轮的
# 最终回复。表头的按钮可以点。
#
#   trail-sidebar.sh <pane>         开/关 <pane> 旁边的侧栏（状态栏「轨迹」按钮调这个）
#   trail-sidebar.sh --run <pane>   侧栏本体：只有一个 fzf（exec，没有外壳进程）
#
# 侧栏上面还切一条命令行（高度 @trail_shell_height 行，默认 8，设 0 不开），跟侧栏同生共死。
#
# 刷新、展开、Claude 窗格没了就关、默认自动打开，都由常驻的 trail_daemon.py 管
# （launchd：com.claude-tmux.sidebar，见 install.sh）。侧栏这边不轮询、不起 python。

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SELF="$HERE/$(basename "$0")"
T="python3 '$HERE/claude_trail.py'"
RUN="$HOME/.local/state/claude-trail/run"

if [ "${1:-}" = --run ]; then
  pane=$2 me=$TMUX_PANE
  mkdir -p "$RUN"
  rows="$RUN/$me.rows"; [ -f "$rows" ] || rows=/dev/null
  # 表头和列表由常驻进程推过来（它看到 .port 文件就推一次完整的，并落到最后一轮）。
  # 滚轮只滚列表、不挪选中行（offset-*），滚起来快。
  # left-click 在 fzf 把选中行挪到被点的那项之后才触发，所以 {1} 就是点中的那一轮：
  # 写进 .open（常驻进程 0.1 秒内重画，把这一项展开），同时后台让主界面跳过去。
  exec fzf --read0 --ansi --no-input --no-sort --layout=reverse --sync \
      --track --id-nth=1 --delimiter='\t' --with-nth='2..' \
      --highlight-line --gap --info=hidden --pointer='▌' --no-scrollbar --ellipsis='…' \
      --listen --header $'加载中…\n' --header-border=bottom \
      --bind "start:execute-silent(echo \$FZF_PORT > '$RUN/$me.port')" \
      --bind "left-click:execute-silent(echo {1} > '$RUN/$me.open'; $T jump $pane {1} >/dev/null 2>&1 &)" \
      --bind "click-header:transform($T click $pane \$FZF_CLICK_HEADER_LINE \$FZF_CLICK_HEADER_COLUMN {1})" \
      --bind 'scroll-up:offset-up+offset-up+offset-up,scroll-down:offset-down+offset-down+offset-down' \
      --bind 'enter:ignore,double-click:ignore,esc:ignore' < "$rows"
fi

caller="${1:?usage: trail-sidebar.sh <pane>}"

# 侧栏上面那条命令行：要敲 git / 看文件的时候不用离开这个窗口。切在侧栏**上方**（-b）：侧栏列表
# 点得多，放下面离手近（2026-10-23 用户：「把这个命令行放上面，然后把这个侧边栏放下面」）。打在 @trail_shell 上（值是
# Claude 窗格），这样它既不会被当成侧栏，也不会被算进「窗口里还有别的平铺窗格」里去——
# 算进去的话常驻进程会以为队友来了，开出来的侧栏下一秒就被收走。
# 它不设 @nofocus：点它就是要在里面打字。
open_shell() {   # $1=侧栏窗格 $2=Claude 窗格
  local h cwd sh
  h=$(tmux show -gqv @trail_shell_height); h=${h:-8}
  case "$h" in ''|*[!0-9]*) return 0 ;; esac
  [ "$h" -gt 0 ] || return 0
  cwd=$(tmux display -p -t "$2" '#{pane_current_path}')
  sh=$(tmux split-window -v -b -d -l "$h" -t "$1" ${cwd:+-c "$cwd"} -P -F '#{pane_id}') || return 0
  tmux set -p -t "$sh" @trail_shell "$2"
}
find_pane() {    # $1=窗口里的任一窗格 $2=选项名 $3=要匹配的值
  tmux list-panes -t "$1" -F "#{pane_id} #{$2}" | awk -v c="$3" '$2==c{print $1; exit}'
}

# 点按钮时焦点可能在侧栏自己、也可能在上面那条命令行上：两种都是「关掉这一套」，
# 并记住是你关的（常驻进程就不再自动开）。
owner=$(tmux show -pqv -t "$caller" @trail_for 2>/dev/null)
[ -n "$owner" ] || owner=$(tmux show -pqv -t "$caller" @trail_shell 2>/dev/null)
if [ -n "$owner" ]; then
  tmux set -p -t "$owner" @trail_off 1
  # 两个窗格号都先查出来再杀：焦点在命令行里时 $caller 就是那条命令行，先杀了它，
  # 后面那次 find_pane 就会「can't find pane」，侧栏留在那儿变成孤窗格。
  sh=$(find_pane "$caller" @trail_shell "$owner")
  sb=$(find_pane "$caller" @trail_for   "$owner")
  [ -n "$sb" ] && tmux kill-pane -t "$sb"
  [ -n "$sh" ] && tmux kill-pane -t "$sh"
  exit 0
fi
open=$(find_pane "$caller" @trail_for "$caller")
if [ -n "$open" ]; then
  tmux set -p -t "$caller" @trail_off 1
  sh=$(find_pane "$caller" @trail_shell "$caller"); [ -n "$sh" ] && tmux kill-pane -t "$sh"
  tmux kill-pane -t "$open"
  exit 0
fi
if ! python3 "$HERE/claude_trail.py" has "$caller"; then
  tmux display-message -t "$caller" "轨迹：这个窗格里没找到 Claude 会话"
  exit 0
fi

tmux set -p -u -t "$caller" @trail_off

# 窗口里还有别的平铺窗格（Claude Code 的队友）：领队已经被挤成一条，再切一块没法看。
# 改成浮在窗口右边（盖住队友那一侧的一部分），领队那一条原样不动。
# 以前这里直接不开，结果开着队友时就没有任何办法让主界面回到最底（2026-10-03 用户原话：
# 「没有任何的方式能让翻到会话底部」）——侧栏表头的「主界面到最底」是他唯一的鼠标入口。
# 只数 Claude：队友（agent teams）开出来的每一格都是 Claude，而「平铺窗格多了一个」
# 会把你自己从命令行里再切的那一块也算进来，于是手一切就改走浮窗那一支。
# Claude Code 的进程名是版本号（2.1.286 这种）。
others=$(tmux list-panes -t "$caller" -F '#{pane_floating_flag}#{@picker_float}#{@trail_for}#{@trail_shell} #{pane_current_command}' \
         | grep -cE '^0 [0-9]+\.[0-9]+\.[0-9]+$')
if [ "$others" -gt 1 ]; then
  read -r ww wh < <(tmux display -p -t "$caller" '#{window_width} #{window_height}')
  # 浮窗四周各占一格边框：内容区最多 (宽-2)×(高-2)，左上角至少在 (1,1)。
  w=$(( ww * 34 / 100 )); [ "$w" -lt 40 ] && w=40; [ "$w" -gt $((ww - 2)) ] && w=$((ww - 2))
  sp=$(tmux new-pane -d -t "$caller" -P -F '#{pane_id}' -x "$w" -y $((wh - 2)) -X $((ww - 1 - w)) -Y 1 \
       "exec '$SELF' --run $caller") || exit 1
  tmux set -p -t "$sp" @trail_for "$caller"
  tmux set -p -t "$sp" @nofocus 1
  # Claude Code 再加队友时会 select-layout，把浮窗吸进平铺树（见 float-picker.sh 同名钩子的注释）；
  # 被吸进去就自己关掉，别留一个乱窗格。
  # 绕一层 run-shell 吞掉报错：窗格已经没了时布局一变钩子照样跑，直接 kill-pane 会在左下角报 Can't find pane
  tmux set-hook -p -t "$sp" window-layout-changed "if -F '#{!:#{pane_floating_flag}}' { run-shell -b \"tmux kill-pane -t $sp 2>/dev/null; true\" }"
  # 浮着的侧栏不切命令行：浮窗再切一块就更挤了，而且这会儿队友正占着屏幕
  exit 0
fi

# -d：焦点留在 Claude 那边，接着打字不受影响；要看轨迹直接用鼠标点侧栏。
sp=$(tmux split-window -h -d -l 34% -t "$caller" -P -F '#{pane_id}' "exec '$SELF' --run $caller") || exit 1
tmux set -p -t "$sp" @trail_for "$caller"
# 点侧栏不抢焦点：tmux.conf.local 里的 MouseDown1Pane 认这个标记，只转发点击、不 select-pane。
tmux set -p -t "$sp" @nofocus 1
open_shell "$sp" "$caller"
