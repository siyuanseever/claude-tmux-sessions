#!/usr/bin/env python3
"""ctx_to_tmux.py — 把这个 Claude 的模型和上下文大小写进它所在 tmux pane 的选项。

为什么不用 statusLine：只要 settings.json 里配了 statusLine，Claude Code 就在输入框
下面给它留一整行，脚本什么都不输出也留（实测两次：一行空白；2026-10-07 为拿订阅额度配过半天，
又因为额度 tmux 右下角已经有了、那一行是重复的，拿掉了）。我们要的是 Claude
窗口里只剩它自带的 `⏵⏵ auto mode on` 那一行，数字放到 tmux 最底下那条去。
所以不配 statusLine，改由 hook 在每次工具调用、每轮开始/结束、会话启动（含压缩后）
时，从会话记录里读最新一条回复的 token 用量。

写三个 pane 选项：
  @claude_ctx    "11% (111k)"  给程序读：agent-fleet 的 parse_ctx_usage 认这个格式
  @claude_bar    显示用、带 tmux 颜色：模型/推理力度 + 一格柱子 + k 数
                 （tmux.conf.local 的 status-right 显示当前 pane 的这个）
  @claude_model  "Opus 5.5/high"  纯文本的模型名，不带颜色标记：claude-trail 的侧栏
                 表头要在自己那边上色（ANSI），tmux 的 #[...] 到了 fzf 里是乱码
  @claude_config 这个 Claude 的 CLAUDE_CONFIG_DIR，默认账号是空串。claude2 用
                 ~/.claude-l2 登另一个账号、额度各算各的，侧栏按这个找对应账号的额度

上下文 = 最新一条主线（非子 Agent）回复的 input + cache_creation + cache_read +
output token；如果它之后有压缩边界，就用边界记下的压缩后大小。
柱子：满格 = 300k，也就是该马上压缩的那条线；150k 起变橙、300k 起变红。
百分比只进 @claude_ctx，按 1M 窗口算（haiku 按 200k）。
"""
import json
import os
import subprocess
import sys

GAUGE = "▁▂▃▄▅▆▇█"
FULL_AT = 300_000
SOFT, HARD = 150_000, 300_000
TRACK = "#444444"  # 柱子那一格的底色 = 满格的高度
TAIL_BYTES = 4 * 1024 * 1024


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout.strip()


def last_usage(path):
    """(tokens, model, effort) from the newest main-thread entry that says how big the context is.

    effort：每条 assistant 记录顶层都有 "effort"（"high" / "medium" …），是那一轮
    实际用的推理力度，/effort 切过也跟着变。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return None, None, None
    model = effort = None
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("isSidechain"):
            continue
        if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
            post = (e.get("compactMetadata") or {}).get("postTokens")
            if post is not None:
                return int(post), model, effort
        msg = e.get("message") or {}
        if e.get("type") == "assistant" and msg.get("usage"):
            u = msg["usage"]
            model = model or msg.get("model")
            effort = effort or e.get("effort")
            if model == "<synthetic>":
                model = None
                continue
            n = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens"))
            return n, model, effort
    return None, model, effort


def short_model(mid):
    """claude-opus-5-5 → Opus 5.5, claude-haiku-4-5-20251001 → Haiku 4.5"""
    if not mid:
        return ""
    parts = mid.replace("claude-", "").split("[")[0].split("-")
    parts = [p for p in parts if not (p.isdigit() and len(p) == 8)]
    if not parts:
        return ""
    ver = ".".join(p for p in parts[1:] if p.isdigit())
    return (parts[0].capitalize() + (" " + ver if ver else "")).strip()


def main():
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return
    try:
        hook = json.load(sys.stdin)
    except ValueError:
        return
    # 放在读上下文之前：新会话第一条回复出来之前 tokens 还是 None，账号也要先认出来
    config = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if tmux("show", "-pqv", "-t", pane, "@claude_config") != config:
        tmux("set", "-p", "-t", pane, "@claude_config", config)
    # Stop fires before the final reply is flushed to the transcript (a brand-new
    # session showed nothing at all after its first answer), so read it again a
    # moment later from a detached child — the hook itself must return at once.
    if hook.get("hook_event_name") == "Stop" and not os.environ.get("CTX_TO_TMUX_LATER"):
        later = subprocess.Popen([sys.executable, __file__], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True,
                                 env={**os.environ, "CTX_TO_TMUX_LATER": "1"})
        later.stdin.write(json.dumps(hook).encode())
        later.stdin.close()
    if os.environ.get("CTX_TO_TMUX_LATER"):
        import time
        time.sleep(1.5)
    tokens, mid, effort = last_usage(hook.get("transcript_path") or "")
    if tokens is None:
        return
    show(pane, tokens, mid, effort)


def show(pane, tokens, mid, effort, window=None):
    """写 @claude_ctx / @claude_bar。"""
    window = window or (200_000 if mid and "haiku" in mid else 1_000_000)
    pct = tokens * 100 // window
    k = tokens // 1000

    level = min(len(GAUGE) - 1, tokens * len(GAUGE) // FULL_AT)
    if tokens >= HARD:
        colour = "#[fg=#d75f5f,bold]"
    elif tokens >= SOFT:
        colour = "#[fg=#d78700]"
    else:
        colour = "#[fg=#8a8a8a]"
    name = short_model(mid)
    if name and effort:
        name += "/" + effort
    lead = f"#[fg=#6c6c6c]{name} " if name else ""
    # 那一格垫灰底：柱子只占格子下面一截时，看得出顶在哪、还差多少到满格（10-07 用户：「上方我看不到顶」）
    bar = f"{lead}{colour}#[bg={TRACK}]{GAUGE[level]}#[bg=default] {k}k#[fg=default,nobold]"
    ctx = f"{pct}% ({k}k)"

    if (tmux("show", "-pqv", "-t", pane, "@claude_ctx") == ctx and
            tmux("show", "-pqv", "-t", pane, "@claude_bar") == bar and
            tmux("show", "-pqv", "-t", pane, "@claude_model") == name):
        return
    tmux("set", "-p", "-t", pane, "@claude_ctx", ctx, ";",
         "set", "-p", "-t", pane, "@claude_bar", bar, ";",
         "set", "-p", "-t", pane, "@claude_model", name)
    # Otherwise the bar only catches up at the next status-interval.
    for c in tmux("list-clients", "-F", "#{client_name}").splitlines():
        tmux("refresh-client", "-S", "-t", c)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
