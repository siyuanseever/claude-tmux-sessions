#!/usr/bin/env bash
# After tmux-resurrect rebuilds the layout (panes, cwds, window names),
# it can't bring the Claude Code sessions back — this can. It walks the
# restore map (~/.claude/tmux-claude-restore.json, maintained by the
# hooks: stable "session:window.pane" coordinates -> Claude session_id),
# falls back to the `claude --resume <id>` the resurrect save recorded for
# panes the map has forgotten, and types `claude --resume <session_id>` into
# every matching pane that is sitting at a plain shell in the recorded cwd.
#
# Safe to run repeatedly: panes already running something (including a
# resumed Claude) are skipped, as are coordinates whose cwd no longer
# matches and sessions in @resurrect-skip-sessions. Pass --dry-run to see
# what it would do without touching anything.
#
# On a restore it isn't called directly any more: resurrect_hooks.py
# post-restore runs the same thing (only into panes that restore created),
# after putting back sessions it moved out of the way and before closing
# restored sidebar shells. Wire all three hooks (see README):
#   set -g @resurrect-hook-post-save-layout '~/.claude/hooks/resurrect_hooks.py save-layout'
#   set -g @resurrect-hook-pre-restore-all  '~/.claude/hooks/resurrect_hooks.py pre-restore'
#   set -g @resurrect-hook-post-restore-all '~/.claude/hooks/resurrect_hooks.py post-restore'
# Run this one by hand after `prefix + Ctrl-r` if needed.
set -euo pipefail
BIN_DIR="$(cd "$(dirname "$(readlink "$0" || echo "$0")")" && pwd)"
exec python3 "$BIN_DIR/resurrect_hooks.py" resume "$@"
