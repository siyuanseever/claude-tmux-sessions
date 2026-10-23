#!/bin/sh
# 装常驻进程（launchd，开机自启、崩了自动拉起）。重复跑没关系，会重新加载。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
PL="$HOME/Library/LaunchAgents/com.claude-tmux.sidebar.plist"
mkdir -p "$HOME/.local/state/claude-trail"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claude-tmux.sidebar</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$HERE/trail_daemon.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/.local/state/claude-trail/daemon.log</string>
  <key>StandardErrorPath</key><string>$HOME/.local/state/claude-trail/daemon.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/com.claude-tmux.sidebar" 2>/dev/null || true
# bootout 是异步的，紧跟着 bootstrap 会报 5: Input/output error，等它真退干净再装
for _ in 1 2 3 4 5 6 7 8 9 10; do
  launchctl bootstrap "gui/$(id -u)" "$PL" 2>/dev/null && break
  sleep 0.5
done
launchctl print "gui/$(id -u)/com.claude-tmux.sidebar" >/dev/null
echo "侧栏常驻进程已装好（$PY $HERE/trail_daemon.py）"
