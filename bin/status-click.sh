#!/usr/bin/env bash
# status-click.sh — what a click on one of status-badge.sh's segments does.
#
#   status-click.sh <mouse_status_range> <pane> <client>
#
# status-badge.sh wraps each clickable segment in #[range=user|<name>], and
# tmux reports that name as #{mouse_status_range} on MouseDown1Status:
#
#   quota        a card with the 5h/7d usage at once, refreshed in place
#   w%<pane>     jump to that waiting (blocked) Claude
#   done         picker, just the finished-unread panes, from the right
#   running      picker, just the running panes
#   read         picker, just the finished-and-seen panes
#                （这三个只有一个 Claude 时不弹框，直接跳过去；多个才弹选择框）
#
# Anything else (plain status-right text) is ignored. The window list is not
# routed here — the binding keeps tmux's own switch-client for that.

BIN_DIR="$(cd "$(dirname "$(readlink "$0" || echo "$0")")" && pwd)"
range="${1:-}"; pane="${2:-}"; client="${3:-}"

case "$range" in
  quota)        exec "$BIN_DIR/usage-refresh.py" --force --notify --client "$client" ;;
  w%*)          exec "$BIN_DIR/jump-top.sh" "${range#w}" ;;
  done|running|read)
    export CLAUDE_TMUX_SESSION="$(tmux display -p -t "$pane" '#{session_name}' 2>/dev/null)" CLAUDE_TMUX_USAGE_FOOTER=0
    # 选中的那一类只有一个 Claude：弹个框只有一行没意义，直接跳过去。
    # 数法和 float-picker.sh 一致（同样的过滤、同一个 CALLER_PANE）；行的第 2 列是窗格号，
    # 会话标题行那一列是空的，只数有窗格号的。
    mine=$(env -u CLAUDE_TMUX_EXTRA_CMD CLAUDE_TMUX_ONLY="$range" CALLER_PANE="$pane" "$BIN_DIR/list-rows.sh" 2>/dev/null \
           | awk -F'\t' '$2 ~ /^%/ {print $2}')
    if [ -n "$mine" ] && [ "$(printf '%s\n' "$mine" | wc -l | tr -d ' ')" -eq 1 ]; then
      exec "$BIN_DIR/jump-top.sh" "$mine"
    fi
    exec "$BIN_DIR/float-picker.sh" "$pane" "$range" ;;
esac
