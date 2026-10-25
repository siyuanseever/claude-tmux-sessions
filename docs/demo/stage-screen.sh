# docs/demo/stage-screen.sh — sourced by gif.sh and shots.sh, not run on its own.
#
# On top of stage-demo.sh (private tmux server -L shotdemo, fake $HOME, fake sessions): a permission
# prompt on the waiting session, the real status bar, a sidebar next to work:api-refactor, and a second
# private server (-L shotouter) attached to it — so capturing the outer pane gives the demo server's
# whole screen: splits, status bar, floating panes, pop-ups.
#
# Sets: REPO DEMO H COLS ROWS WORK IN OUTER main sb sbw sbh client
# Defines: show_sidebar [state], capture <name> (→ $WORK/<name>.ansi + .png), teardown

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO="$REPO/docs/demo"
H="/private/tmp/shotroom"
COLS="${COLS:-176}"
ROWS="${ROWS:-42}"
WORK="${WORK:?set WORK to a scratch directory}"
IN=(-L shotdemo -f /dev/null)
OUTER=(-L shotouter -f /dev/null)
rm -rf "$WORK"; mkdir -p "$WORK"

COLS="$COLS" ROWS="$ROWS" "$DEMO/stage-demo.sh" >/dev/null

# ---- the session that's waiting: give it a permission prompt on screen ----
cat > "$H/frame-wait.txt" <<'FRAME'
> 把 staging 的 DNS 切到新的负载均衡

● 先确认一下现在的记录，再改。

  Bash(dig +short api.staging.example.com)
  ⎿  10.0.3.17

● 记录对得上。下面这条会改线上 DNS：

╭──────────────────────────────────────────────────────────────────────╮
│ Bash command                                                         │
│                                                                      │
│   ./scripts/dns-cutover.sh --zone staging --to lb-2                  │
│                                                                      │
│ Do you want to proceed?                                              │
│ ❯ 1. Yes                                                             │
│   2. Yes, and don't ask again for this command                       │
│   3. No, and tell Claude what to do differently (esc)                │
╰──────────────────────────────────────────────────────────────────────╯
FRAME
if [ "${CLAUDE_TMUX_LANG:-}" = en ]; then
  cat > "$H/frame-wait.txt" <<'FRAME'
> point staging DNS at the new load balancer

● Let me check the current record first, then change it.

  Bash(dig +short api.staging.example.com)
  ⎿  10.0.3.17

● The record matches. This next command changes live DNS:

╭──────────────────────────────────────────────────────────────────────╮
│ Bash command                                                         │
│                                                                      │
│   ./scripts/dns-cutover.sh --zone staging --to lb-2                  │
│                                                                      │
│ Do you want to proceed?                                              │
│ ❯ 1. Yes                                                             │
│   2. Yes, and don't ask again for this command                       │
│   3. No, and tell Claude what to do differently (esc)                │
╰──────────────────────────────────────────────────────────────────────╯
FRAME
fi
wait_pane=$(tmux "${IN[@]}" list-panes -t infra:deploy-script -F '#{pane_id}')
tmux "${IN[@]}" respawn-pane -k -t "$wait_pane" "clear; cat $H/frame-wait.txt; exec tail -f /dev/null"

# ---- status bar: the real segments, reading the fake $HOME ----
tmux "${IN[@]}" set -g status on
tmux "${IN[@]}" set -g status-style "bg=#262626,fg=#bcbcbc"
tmux "${IN[@]}" set -g status-left-length 140
tmux "${IN[@]}" set -g status-right-length 80
tmux "${IN[@]}" set -g status-interval 1
tmux "${IN[@]}" set -g status-left " ❐ #S │ #(HOME=$H $REPO/bin/status-badge.sh states '' '#S')│#($REPO/bin/float-tool.sh --buttons) "
tmux "${IN[@]}" set -g status-right "#($REPO/bin/tmux-sysstat.sh) "
# the window list as README recommends: each window carries its own Claude badge (#{E:@claude_win})
tmux "${IN[@]}" set -g window-status-format " #I#{E:@claude_win} #W "
tmux "${IN[@]}" set -g window-status-current-format "#[bg=#3a3a3a,fg=#ffffff] #I#{E:@claude_win} #W #[default]"

# ---- the sidebar on work:api-refactor: a shell strip on top, the sidebar below ----
main=$(tmux "${IN[@]}" list-panes -t work:api-refactor -F '#{pane_id}')
sb=$(tmux "${IN[@]}" split-window -h -d -l 34% -t "$main" -P -F '#{pane_id}' "tail -f /dev/null")
if [ "${CLAUDE_TMUX_LANG:-}" = en ]; then log2='3f2a91c Extract paging helper, cap orders limit\n9c41d07 Health check endpoint'
else log2='3f2a91c 抽出分页 helper，补 orders 上限校验\n9c41d07 健康检查接口'; fi
sh=$(tmux "${IN[@]}" split-window -v -b -d -l 6 -t "$sb" -P -F '#{pane_id}' \
     "printf '\033[38;5;245m~/repos/api\033[0m \033[38;5;39m(main)\033[0m $ git log --oneline -2\n$log2\n\033[38;5;245m~/repos/api\033[0m \033[38;5;39m(main)\033[0m $ '; exec tail -f /dev/null")
sbw=$(tmux "${IN[@]}" display -p -t "$sb" '#{pane_width}')
sbh=$(tmux "${IN[@]}" display -p -t "$sb" '#{pane_height}')
show_sidebar() {   # $1 = switch state: "" / running / home
  HOME="$H" python3 "$DEMO/sidebar-frame.py" "$WORK/sb.ansi" $((sbw - 3)) "$sbh" "${1:-}"
  tmux "${IN[@]}" respawn-pane -k -t "$sb" "cat $WORK/sb.ansi; exec tail -f /dev/null"
}
show_sidebar ""
tmux "${IN[@]}" select-pane -t "$main"

# ---- attach from the outer server; a frame = capture of the outer pane ----
tmux "${OUTER[@]}" kill-server 2>/dev/null || true
tmux "${OUTER[@]}" new-session -d -s o -x "$COLS" -y "$ROWS" \
  "TMUX= tmux -L shotdemo -f /dev/null attach -t work:api-refactor"
tmux "${OUTER[@]}" set -g status off
sleep 2.5
client=$(tmux "${IN[@]}" list-clients -F '#{client_name}' | head -1)

capture() {   # $1 = name → $WORK/$1.ansi, $WORK/$1.png
  tmux "${OUTER[@]}" capture-pane -p -e -t o > "$WORK/$1.ansi"
  "$DEMO/render.sh" "$WORK/$1.ansi" "$WORK/$1.png" 15 >/dev/null
}

teardown() {
  tmux "${OUTER[@]}" kill-server 2>/dev/null || true
  tmux "${IN[@]}" kill-server 2>/dev/null || true
}
