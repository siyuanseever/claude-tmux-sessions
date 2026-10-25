#!/usr/bin/env bash
# docs/img/demo.gif — the whole toolkit in ~10 seconds, rendered from the real scripts on invented data.
#
#   docs/demo/gif.sh            → docs/img/demo.gif (+ the frames under $TMPDIR/claude-tmux-gif/)
#
# How: stage-demo.sh builds a private tmux server (-L shotdemo) with fake sessions and a fake $HOME.
# stage-screen.sh adds a sidebar and a status bar to it, then attaches to it from a *second* private
# server (-L shotouter); capturing that outer pane gives the demo server's whole
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

OUT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/docs/img/demo.gif}"
WORK="${TMPDIR:-/tmp}/claude-tmux-gif"
source "$(dirname "${BASH_SOURCE[0]}")/stage-screen.sh"

n=0
: > "$WORK/frames.txt"
shot() {   # $1 = seconds this frame stays on screen
  sleep 2
  n=$((n + 1))
  capture "f$n"
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
# the message switch_account.py ends with, in the UI's language
if [ "${CLAUDE_TMUX_LANG:-}" = en ]; then msg="Switch account: switched to home, same session"
else msg="切账号：已切到 home，接着原会话"; fi
tmux "${IN[@]}" display-message -c "$client" -d 8000 "$msg"
shot 3.0
printf "file '%s'\n" "$WORK/f$n.png" >> "$WORK/frames.txt"   # concat demuxer: repeat the last one

teardown

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
