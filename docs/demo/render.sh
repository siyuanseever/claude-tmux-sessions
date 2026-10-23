#!/usr/bin/env bash
# ansi -> html -> png. Headless Chrome silently ignores --window-size when it
# reuses a lingering headless process, so kill any first and verify the result
# is the size we asked for, retrying once.
# usage: render.sh <in.ansi> <out.png> [font_px]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN="${1:?input .ansi}"
OUT="${2:?output .png}"
FONT="${3:-15}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Keep the intermediate HTML out of the output directory — docs/ is
# committed, and a stray picker.html next to picker.png just invites someone
# to wonder whether it's part of the docs.
HTML="${TMPDIR:-/tmp}/$(basename "${OUT%.png}").html"
# 固定的一个配置目录：只杀用它的那个 Chrome，不碰机器上别人（别的会话跑测试）起的 headless Chrome；
# 也不每次新建——全新的配置目录会让 Chrome 在后台下载本地 AI 模型（几个 GB）。
PROFILE="${TMPDIR:-/tmp}/claude-tmux-render-chrome"
mkdir -p "$PROFILE"
if [ "$(defaults read com.google.Chrome GenAILocalFoundationalModelSettings 2>/dev/null)" != 1 ]; then
  echo "先设 Chrome 策略再渲染，免得它下载本地 AI 模型：defaults write com.google.Chrome GenAILocalFoundationalModelSettings -int 1" >&2
  exit 1
fi

read -r W H <<<"$(python3 "$DIR/ansi2html.py" "$IN" "$HTML" "$FONT")"

shoot() {
  pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null || true
  sleep 1
  rm -f "$OUT"
  "$CHROME" --headless=old --disable-gpu --hide-scrollbars --user-data-dir="$PROFILE" \
    --force-device-scale-factor=2 --window-size="$W,$H" \
    --screenshot="$OUT" "$HTML" >/dev/null 2>&1 || true
  sips -g pixelWidth "$OUT" 2>/dev/null | awk '/pixelWidth/{print $2}'
}

got="$(shoot)"
if [ "$got" != "$((W * 2))" ]; then
  echo "retry (got ${got}px, wanted $((W * 2))px)"
  got="$(shoot)"
fi
[ "$got" = "$((W * 2))" ] || { echo "FAILED: got ${got}px, wanted $((W * 2))px"; exit 1; }
echo "$OUT  ${W}x${H} css / $((W * 2))x$((H * 2)) px"
