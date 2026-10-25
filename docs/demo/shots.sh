#!/usr/bin/env bash
# docs/img/*.png — the screenshots in README and docs/workbench, from the real scripts on invented data.
#
#   docs/demo/shots.sh [out-dir]        → <out-dir>/*.png (default docs/img), the .ansi under $TMPDIR/claude-tmux-shots/
#   CLAUDE_TMUX_LANG=en docs/demo/shots.sh docs/img/en      the English UI, with the demo content in English
#
# Same stage as gif.sh (stage-screen.sh): a private tmux server with fake sessions, the real status
# bar, a sidebar. Each shot drives the real scripts the way a click would, then captures the screen:
#   screen        the whole screen: Claude, the sidebar on the right, the status bar
#   picker        prefix+g: every session, grouped by project, live preview
#   click-done    click ✔ on the status bar: the picker for just the finished-unread ones, floating at the bottom
#   git           click the git button: lazygit floating on the right, on a demo repo
#   files         click the files button: yazi floating on the right
#   statusbar     the status bar alone (the last line of `screen`)
#   sidebar       the sidebar alone, larger
# and renders the diagrams <out-dir>/*.svg to .png next to them.
set -euo pipefail

OUT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/docs/img}"
WORK="${TMPDIR:-/tmp}/claude-tmux-shots"
source "$(dirname "${BASH_SOURCE[0]}")/stage-screen.sh"
mkdir -p "$OUT_DIR"

# Panes opened from now on (floating pickers, lazygit, yazi) read their config from the fake home.
tmux "${IN[@]}" set-environment -g HOME "$H"
mkdir -p "$H/.config"
[ -d "$HOME/.config/yazi" ] && cp -R "$HOME/.config/yazi" "$H/.config/yazi"
# lazygit on a fresh home greets you with a "thanks for using lazygit" box, a
# "breaking changes" box when it doesn't know your last version, and random tips; skip them all.
lg="$H/Library/Application Support/lazygit"
mkdir -p "$lg"
printf 'startuppopupversion: 99\nlastversion: %s\n' "$(lazygit --version | sed -n 's/.*version=\([^,]*\),.*/\1/p')" > "$lg/state.yml"
printf 'gui:\n  showRandomTip: false\n  showCommandLog: false\n' > "$lg/config.yml"

# Clicking ✔ with a single finished-unread Claude in the session jumps straight to it instead of
# opening a list — give the `work` session a second one so the shot shows the list.
python3 - "$H/.claude/tmux-claude-status.json" "$(tmux "${IN[@]}" list-panes -t work:scratch -F '#{pane_id}')" <<'EOF'
import json, sys, time
f, pane = sys.argv[1], sys.argv[2]
d = json.load(open(f))
d[pane].update(status="done", read=False, updated_at=time.time() - 300)
json.dump(d, open(f, "w"), ensure_ascii=False, indent=2)
EOF

# ---- a demo repo where the sidebar's Claude works: a few commits, two files changed ----
api="$H/repos/api"
msg() { if [ "${CLAUDE_TMUX_LANG:-}" = en ]; then echo "$1"; else echo "$2"; fi; }
(
  cd "$api"
  export GIT_AUTHOR_NAME=alex GIT_AUTHOR_EMAIL=alex@example.com GIT_COMMITTER_NAME=alex GIT_COMMITTER_EMAIL=alex@example.com
  git init -q -b main
  mkdir -p handlers internal/paging
  printf 'package handlers\n\nfunc Health() string { return "ok" }\n' > handlers/health.go
  git add -A && git commit -q -m "$(msg "Health check endpoint" "健康检查接口")"
  printf 'package handlers\n\n// Users lists users, one page at a time.\nfunc Users(limit, offset int) {}\n' > handlers/users.go
  printf 'package handlers\n\n// Orders lists orders, one page at a time.\nfunc Orders(limit, offset int) {}\n' > handlers/orders.go
  git add -A && git commit -q -m "$(msg "users / orders list endpoints" "users / orders 列表接口")"
  printf 'package paging\n\nconst MaxLimit = 200\n\n// Clamp keeps limit within 1..MaxLimit.\nfunc Clamp(limit int) int {\n\tif limit < 1 {\n\t\treturn 1\n\t}\n\tif limit > MaxLimit {\n\t\treturn MaxLimit\n\t}\n\treturn limit\n}\n' > internal/paging/paging.go
  git add -A && git commit -q -m "$(msg "Extract paging helper, cap orders limit" "抽出分页 helper，补 orders 上限校验")"
  printf 'package handlers\n\nimport "api/internal/paging"\n\n// Users lists users, one page at a time.\nfunc Users(limit, offset int) {\n\tlimit = paging.Clamp(limit)\n}\n' > handlers/users.go
  printf '# api\n\nDemo service for the screenshots.\n' > README.md
)

shoot() {   # $1 = name: capture the screen and copy the png out
  sleep "${2:-2.5}"
  capture "$1"
  cp "$WORK/$1.png" "$OUT_DIR/$1.png"
  echo "$OUT_DIR/$1.png"
}
run() { tmux "${IN[@]}" run-shell -b "HOME=$H $*"; }
close_floats() {
  tmux "${IN[@]}" list-panes -a -F '#{pane_id} #{pane_floating_flag}' | awk '$2==1{print $1}' \
    | while read -r p; do tmux "${IN[@]}" kill-pane -t "$p"; done
  tmux "${IN[@]}" select-pane -t "$main"
}

shoot screen
# the status bar alone: the last line of the screen
tail -n 1 "$WORK/screen.ansi" > "$WORK/statusbar.ansi"
"$DEMO/render.sh" "$WORK/statusbar.ansi" "$OUT_DIR/statusbar.png" 15 >/dev/null && echo "$OUT_DIR/statusbar.png"

# Full-size here (prefix+g opens it at 95%×85%): the panes behind a smaller popup bleed in at its edges.
tmux "${IN[@]}" display-popup -c "$client" -w 100% -h 100% -E \
  "HOME=$H CALLER_PANE=$main $REPO/bin/claude-tmux-picker.sh" &
shoot picker
tmux "${OUTER[@]}" send-keys -t o Escape
sleep 1
tmux "${IN[@]}" switch-client -c "$client" -t work:api-refactor
tmux "${IN[@]}" select-pane -t "$main"

run "$REPO/bin/status-click.sh" done "$main" "$client"
shoot click-done 4
close_floats


run "$REPO/bin/float-tool.sh" "$main" tool:git
shoot git 4
close_floats

run "$REPO/bin/float-tool.sh" "$main" tool:files
shoot files 4
close_floats

# the sidebar alone, bigger than on the screen shot
HOME="$H" python3 "$DEMO/sidebar-frame.py" "$WORK/sidebar.ansi" 60 24 ""
"$DEMO/render.sh" "$WORK/sidebar.ansi" "$OUT_DIR/sidebar.png" 20 >/dev/null && echo "$OUT_DIR/sidebar.png"

teardown

# ---- the diagrams: <out-dir>/*.svg → .png at 2x, for places that don't take SVG (Zhihu, Xiaohongshu) ----
PROFILE="${TMPDIR:-/tmp}/claude-tmux-render-chrome"
for svg in "$OUT_DIR"/*.svg; do
  # (not `read`: an SVG without a trailing newline makes it fail, and set -e ends the script there)
  vb=$(grep -o 'viewBox="0 0 [0-9]* [0-9]*"' "$svg" | head -1 | tr -dc '0-9 ')
  w=$(echo "$vb" | awk '{print $3}'); h=$(echo "$vb" | awk '{print $4}')
  html="$WORK/$(basename "${svg%.svg}").html"
  printf '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#0f1419}svg{display:block;width:%spx;height:%spx}</style>' "$w" "$h" > "$html"
  cat "$svg" >> "$html"
  pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null || true; sleep 1
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=old --disable-gpu --hide-scrollbars \
    --user-data-dir="$PROFILE" --force-device-scale-factor=2 --window-size="$w,$h" \
    --screenshot="${svg%.svg}.png" "$html" >/dev/null 2>&1 || true
  echo "${svg%.svg}.png"
done
