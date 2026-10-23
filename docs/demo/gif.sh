#!/usr/bin/env bash
# docs/demo.gif — the whole toolkit in ~10 seconds, rendered from the real scripts on invented data.
#
#   docs/demo/gif.sh            → docs/demo.gif (+ the frames under $TMPDIR/claude-tmux-gif/)
#
# How: stage-demo.sh builds a private tmux server (-L shotdemo) with fake sessions and a fake $HOME.
# This script adds a sidebar and a status bar to it, then attaches to it from a *second* private
# server (-L shotouter) and captures that outer pane — so a frame is the demo server's whole
# screen: splits, status bar, pop-ups. Frames go ansi → png (render.sh) → gif (ffmpeg).
# No real session, path or account appears anywhere.
#
# Storyboard:
#   1. a Claude working, the sidebar on its right, the status bar counting ⏸ ✔ ▶ ✓
#   2. prefix+g: the picker, every session at a glance, one of them ⏸ WAIT
#   3. type its number: you land on the session that's waiting for a confirmation
#   4. back on the first one, click ⇄ on the sidebar: …
#   5. switched: the button now says home, the quota is the other account's, a message confirms
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO="$REPO/docs/demo"
H="/private/tmp/shotroom"
COLS="${COLS:-176}"
ROWS="${ROWS:-42}"
OUT="${1:-$REPO/docs/demo.gif}"
WORK="${TMPDIR:-/tmp}/claude-tmux-gif"
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
tmux "${IN[@]}" set -g window-status-format " #I #W "
tmux "${IN[@]}" set -g window-status-current-format "#[bg=#3a3a3a,fg=#ffffff] #I #W #[default]"

# ---- the sidebar on work:api-refactor: a shell strip on top, the sidebar below ----
main=$(tmux "${IN[@]}" list-panes -t work:api-refactor -F '#{pane_id}')
sb=$(tmux "${IN[@]}" split-window -h -d -l 34% -t "$main" -P -F '#{pane_id}' "tail -f /dev/null")
sh=$(tmux "${IN[@]}" split-window -v -b -d -l 6 -t "$sb" -P -F '#{pane_id}' \
     "printf '\033[38;5;245m~/repos/api\033[0m \033[38;5;39m(main)\033[0m $ git log --oneline -2\n3f2a91c 抽出分页 helper，补 orders 上限校验\n9c41d07 健康检查接口\n\033[38;5;245m~/repos/api\033[0m \033[38;5;39m(main)\033[0m $ '; exec tail -f /dev/null")
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
n=0
: > "$WORK/frames.txt"
shot() {   # $1 = seconds this frame stays on screen
  sleep 2
  n=$((n + 1))
  tmux "${OUTER[@]}" capture-pane -p -e -t o > "$WORK/f$n.ansi"
  "$DEMO/render.sh" "$WORK/f$n.ansi" "$WORK/f$n.png" 15 >/dev/null
  printf "file '%s'\nduration %s\n" "$WORK/f$n.png" "$1" >> "$WORK/frames.txt"
}

shot 2.4                                                     # 1. working, sidebar, status bar
tmux "${IN[@]}" display-popup -c "$client" -w 95% -h 85% -E \
  "HOME=$H CALLER_PANE=$main $REPO/bin/claude-tmux-picker.sh" &
sleep 2
shot 2.6                                                     # 2. the picker
tmux "${OUTER[@]}" send-keys -t o 7                          # 3. type the WAIT row's number
sleep 1
shot 2.4
tmux "${IN[@]}" switch-client -c "$client" -t work:api-refactor
show_sidebar running                                         # 4. clicked ⇄
shot 1.4
show_sidebar home                                            # 5. switched
sleep 2                                                      # let the sidebar draw before the message
tmux "${IN[@]}" display-message -c "$client" -d 8000 "切账号：已切到 home，接着原会话"
shot 3.0
printf "file '%s'\n" "$WORK/f$n.png" >> "$WORK/frames.txt"   # concat demuxer: repeat the last one

tmux "${OUTER[@]}" kill-server 2>/dev/null || true
tmux "${IN[@]}" kill-server 2>/dev/null || true

# ---- frames → gif: one palette for all frames, 1200px wide ----
# render.sh sizes each png to its own widest line, and ffmpeg stops at the first frame whose size
# differs — so pad every frame to the largest one first: anchored top-left (centring would make the
# picture jump between frames), in the terminal's background colour.
python3 - "$WORK" <<'PY'
import glob, sys
from PIL import Image
fs = sorted(glob.glob(sys.argv[1] + "/f*.png"))
ims = [Image.open(f).convert("RGB") for f in fs]
W, H = max(i.width for i in ims), max(i.height for i in ims)
for f, im in zip(fs, ims):
    c = Image.new("RGB", (W, H), (0x1b, 0x1d, 0x1e)); c.paste(im, (0, 0)); c.save(f)
PY
ffmpeg -loglevel error -y -f concat -safe 0 -i "$WORK/frames.txt" \
  -vf "fps=10,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=none" \
  -loop 0 "$OUT"
echo "$OUT  $(du -h "$OUT" | cut -f1)  ($n frames in $WORK)"
