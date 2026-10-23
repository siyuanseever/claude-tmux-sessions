#!/usr/bin/env bash
# Install claude-tmux-sessions: symlinks the hook scripts into ~/.claude/hooks
# and merges the required hooks into ~/.claude/settings.json.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "==> checking dependencies"
missing=()
for bin in tmux fzf python3; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "缺少依赖: ${missing[*]}" >&2
  echo "macOS: brew install ${missing[*]}" >&2
  exit 1
fi

mkdir -p "$CLAUDE_HOOKS_DIR"

echo "==> linking scripts into $CLAUDE_HOOKS_DIR"
ln -sf "$REPO_DIR/hooks/tmux_status_update.py" "$CLAUDE_HOOKS_DIR/tmux_status_update.py"
ln -sf "$REPO_DIR/bin/claude-tmux-picker.sh" "$CLAUDE_HOOKS_DIR/claude-tmux-picker.sh"
ln -sf "$REPO_DIR/bin/jump-top.sh" "$CLAUDE_HOOKS_DIR/jump-top.sh"
ln -sf "$REPO_DIR/bin/status-badge.sh" "$CLAUDE_HOOKS_DIR/status-badge.sh"
ln -sf "$REPO_DIR/bin/restore-claude.sh" "$CLAUDE_HOOKS_DIR/restore-claude.sh"
ln -sf "$REPO_DIR/bin/resurrect_hooks.py" "$CLAUDE_HOOKS_DIR/resurrect_hooks.py"
ln -sf "$REPO_DIR/bin/float-picker.sh" "$CLAUDE_HOOKS_DIR/float-picker.sh"
ln -sf "$REPO_DIR/bin/usage-refresh.py" "$CLAUDE_HOOKS_DIR/usage-refresh.py"
ln -sf "$REPO_DIR/bin/status-click.sh" "$CLAUDE_HOOKS_DIR/status-click.sh"
# git / 文件弹窗、资源显示、侧栏的模型和上下文、切账号（原来分散在别的仓库，2026-10-23 并进来）
ln -sf "$REPO_DIR/bin/float-tool.sh" "$CLAUDE_HOOKS_DIR/float-tool.sh"
ln -sf "$REPO_DIR/bin/tmux-sysstat.sh" "$CLAUDE_HOOKS_DIR/tmux-sysstat.sh"
ln -sf "$REPO_DIR/bin/sys-monitor.py" "$CLAUDE_HOOKS_DIR/sys-monitor.py"
ln -sf "$REPO_DIR/hooks/ctx_to_tmux.py" "$CLAUDE_HOOKS_DIR/ctx_to_tmux.py"

echo "==> updating $SETTINGS_FILE"
python3 - "$SETTINGS_FILE" <<'PYEOF'
import json, os, sys

path = sys.argv[1]
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
else:
    settings = {}

hooks = settings.setdefault("hooks", {})

def ensure_hook(event, command, timeout=None):
    entries = hooks.setdefault(event, [])
    for entry in entries:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return  # already installed
    h = {"type": "command", "command": command}
    if timeout:
        h["timeout"] = timeout
    entries.append({"hooks": [h]})

ensure_hook("UserPromptSubmit",
            "python3 ~/.claude/hooks/tmux_status_update.py running 2>/dev/null || true")
ensure_hook("Stop",
            "python3 ~/.claude/hooks/tmux_status_update.py done 2>/dev/null || true")
ensure_hook("Notification",
            "python3 ~/.claude/hooks/tmux_status_update.py notify 2>/dev/null || true")
# 一个工具真的跑起来了，说明那个权限确认已经被回答掉了 —— 这是「不再 blocked」
# 唯一的即时信号。它每次工具调用都会触发，所以 unblock 走的是只读快路径：
# 这个 pane 不是 blocked 就立刻退出，不写盘也不刷 badge。
ensure_hook("PostToolUse",
            "python3 ~/.claude/hooks/tmux_status_update.py unblock 2>/dev/null || true")
ensure_hook("SessionEnd",
            "python3 ~/.claude/hooks/tmux_status_update.py clear 2>/dev/null || true")
# 主 agent Stop 了，后台 subagent 可能还在跑：记下在跑的 subagent，最后一个停了才算
# 这个会话跑完（见 hooks/tmux_status_update.py 的 subagent-start / subagent-stop）。
ensure_hook("SubagentStart",
            "python3 ~/.claude/hooks/tmux_status_update.py subagent-start 2>/dev/null || true")
ensure_hook("SubagentStop",
            "python3 ~/.claude/hooks/tmux_status_update.py subagent-stop 2>/dev/null || true")

# ensure_ascii=False：settings.json 里常有中文（权限说明、别的钩子的注释），默认的
# \uXXXX 转义虽然等价，但会把整个文件改得面目全非。
# 侧栏表头的模型 / 上下文 / 账号：每个 Claude 把这些写进自己所在 tmux 窗格的选项
for ev in ("SessionStart", "UserPromptSubmit", "Stop", "PostToolUse"):
    ensure_hook(ev, "python3 ~/.claude/hooks/ctx_to_tmux.py 2>/dev/null || true", timeout=5)
with open(path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("hooks merged into", path)
PYEOF

# 侧栏的常驻进程（launchd：开机自启、崩了自动拉起）。只在 macOS 上；别的系统先手动跑 sidebar/trail_daemon.py。
# CLAUDE_TMUX_NO_DAEMON=1 跳过这一步（在临时目录里试装、或者你想自己管常驻进程的时候）。
if [ "$(uname)" = Darwin ] && [ -z "${CLAUDE_TMUX_NO_DAEMON:-}" ]; then
  echo "==> installing the sidebar daemon (launchd: com.claude-tmux.sidebar)"
  sh "$REPO_DIR/sidebar/install.sh"
fi

if ! command -v terminal-notifier >/dev/null 2>&1; then
  echo "==> (可选) brew install terminal-notifier — 装了之后点击 blocked 通知能直接跳到那个 pane"
fi

cat <<'EOF'

==> 安装完成，还差最后一步（手动，因为每个人的 tmux 配置不一样）：

1. 在你的 tmux 配置（~/.tmux.conf 或 ~/.tmux.conf.local）最后加一行，装上按键、鼠标和钩子
   （prefix+g 选择器、prefix+a 跳到最该处理的、点底栏、点浮窗外面关弹窗、切窗格标记已读）：

    source-file ~/projects/claude-tmux-sessions/tmux/claude-tmux.conf

   （仓库不在 ~/projects 下就改成你 clone 的位置。）

2. 把底栏要显示的几段拼进你的 status-left / status-right（各家主题不同，所以不替你改）：

    #(~/.claude/hooks/status-badge.sh states)    各会话状态计数 ⏸ ✔ ▶ ✓，能点
    #(~/.claude/hooks/float-tool.sh --buttons)   开关侧栏的按钮
    #(~/.claude/hooks/tmux-sysstat.sh)           整机 CPU / 内存 / 交换 / 磁盘，能点

   改完 `tmux source-file ~/.tmux.conf`。

3. 想用侧栏的 ⇄ 换账号：在 ~/.config/claude-tmux/accounts.json 里写上你的几个账号
   （格式见 bin/switch_account.py 的 load_accounts）。

已经在跑的 Claude Code 会话，要在里面执行一次 /hooks 才会用上新写进 settings.json 的 hook。

可选：让每个窗口在窗口列表里直接显示自己的 Claude 状态（⏸ 等你确认 / ✔ 跑完未读 /
▶ 正在跑 / ✓ 已看过），不用打开 picker 就知道是哪个窗口：

    set -g window-status-format         '#I#{E:@claude_win} #W'
    set -g window-status-current-format '#I#{E:@claude_win} #W'

用 gpakosz/.tmux 的话，改成放进 ~/.tmux.conf.local 里的
tmux_conf_theme_window_status_format 和 ..._current_format。
渲染时不起进程：hooks 直接把 badge 写进窗口选项。

如果你的窗口名跟着 Claude 的 pane title 走，名字开头已经有 Claude 自己的状态字符
（✳ 空闲 / ◑◐ 转圈），它只分得清在不在跑。显示时剥掉它，只留信息更全的 badge：

    set -g window-status-format '#I#{E:@claude_win} #{s|^[^ -~] ||:window_name}'

可选：如果你用 tmux-resurrect/continuum，加上这几行，恢复布局后自动
claude --resume 回到每个 pane 原来的会话（正常退出的不会被复活）；
@resurrect-skip-sessions 里的会话（别的程序自己管的，比如 launchd 起的服务）不存也不恢复，
打了 @resurrect_skip 的窗格（sidebar/ 的侧栏）恢复后关掉，由侧栏自己重开：

    set -g @resurrect-skip-sessions 'my-servers'
    set -g @resurrect-hook-post-save-layout '~/.claude/hooks/resurrect_hooks.py save-layout'
    set -g @resurrect-hook-pre-restore-all '~/.claude/hooks/resurrect_hooks.py pre-restore'
    set -g @resurrect-hook-post-restore-all '~/.claude/hooks/resurrect_hooks.py post-restore'
EOF
