#!/usr/bin/env python3
"""statusline.py — 可选的 Claude Code statusLine 桥接：把 Claude Code 交给状态栏的额度读数记下来，给 statetrail 用。

在 ~/.claude/settings.json（或别的 CLAUDE_CONFIG_DIR 里的）把 statusLine 接成：

    "statusLine": {"type": "command", "command": "python3 <插件目录>/statusline.py -- <你原来的状态栏命令>"}

没有原来的命令就只写到 `statusline.py` 为止。statetrail 自己不改 settings.json。

Claude Code 每次刷新状态栏（会话开始、每条回复之后……）都会把一份 JSON 从 stdin 交给这个命令，里面有
rate_limits.five_hour / seven_day 的 used_percentage 和 resets_at（Pro / Max 订阅才有，见
code.claude.com/docs/en/statusline.md）。这里先把它按 session_id 原子地写进 statetrail 的状态目录，
再把同一份 JSON 交给你原来的命令，它的输出和退出码原样转出去——你自己的状态栏一个字都不变。
不碰任何凭据，也不往外发任何东西。

要快：Claude Code 有新的刷新时会取消还在跑的这一个，所以只 import 标准库，写完立刻转给原命令。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("STATETRAIL_STATE_DIR")
             or Path.home() / ".local" / "state" / "herdr" / "plugins" / "statetrail") / "statusline"
KEEP_DAYS = 7                     # 这么久没更新的会话读数顺手删掉


def record(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return
    sid = str(d.get("session_id") or "")
    if not sid or "/" in sid or not d.get("rate_limits"):
        return                    # 没有额度（API key、还没第一次回复……）就不写，别用空读数盖掉旧的
    out = {"fetched_at_ms": int(time.time() * 1000), "session_id": sid, "rate_limits": d["rate_limits"]}
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / f"{sid}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(path)
    if int(time.time()) % 50 == 0:  # 偶尔清一次旧文件，不每次都扫目录
        cutoff = time.time() - KEEP_DAYS * 86400
        for f in STATE.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def main():
    raw = sys.stdin.read()
    try:
        record(raw)
    except OSError:
        pass                      # 记不下来也不能影响你的状态栏
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        return 0
    # 原来的命令，stdin 还是那一份 JSON。`--` 后面只有一个参数就当成整条 shell 命令（可以带管道，
    # 比如 -- 'my-status | tail -1'）；多个参数就按原样重新加引号，空格和引号不会被拆坏
    import shlex
    cmd = args[0] if len(args) == 1 else shlex.join(args)
    r = subprocess.run(cmd, shell=True, input=raw.encode(), stdout=sys.stdout.buffer, stderr=sys.stderr.buffer)
    return r.returncode


sys.exit(main())
