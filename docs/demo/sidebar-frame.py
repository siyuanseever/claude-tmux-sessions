#!/usr/bin/env python3
"""Render one frame of the sidebar for the demo, using the sidebar's real code on invented data.

    HOME=<fake home> sidebar-frame.py <out.ansi> <width> <height> [switch-state]

switch-state: "" (normal), "running" (⇄ shows …), "home" (already switched to the other account).
Writes into the fake home: two invented accounts (alex.work / alex.home), a quota cache, and an
8-turn transcript whose first 3 turns sit before a /compact. Nothing here touches the real ~/.claude.
"""
import json
import os
import sys
import time
from pathlib import Path

H = Path(os.environ["HOME"])
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sidebar"))

out, width, height = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
state = sys.argv[4] if len(sys.argv) > 4 else ""
now = time.time()

# ---- accounts: default dir logs in as alex.work, ~/.claude-home as alex.home ----
(H / ".claude-home").mkdir(parents=True, exist_ok=True)
(H / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "alex.work@example.com"}}))
(H / ".claude-home" / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "alex.home@example.com"}}))
(H / ".config" / "claude-tmux").mkdir(parents=True, exist_ok=True)
(H / ".config" / "claude-tmux" / "accounts.json").write_text(json.dumps(
    {"accounts": {"work": {"config_dir": "~/.claude"}, "home": {"config_dir": "~/.claude-home"}}}))
cfg = str(H / ".claude-home") if state == "home" else ""


def utc(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


usage = {"fetched_at_ms": int((now - 4 * 60) * 1000), "attempted_at_ms": int((now - 4 * 60) * 1000),
         "five_hour": {"utilization": 32.0, "resets_at": utc(now + 2.4 * 3600)},
         "seven_day": {"utilization": 41.0, "resets_at": utc(now + 3.2 * 86400)}}
if state == "home":   # the other account has its own, fresher quota
    usage.update(five_hour={"utilization": 6.0, "resets_at": utc(now + 4.6 * 3600)},
                 seven_day={"utilization": 18.0, "resets_at": utc(now + 5.1 * 86400)},
                 fetched_at_ms=int(now * 1000))
for d in (H / ".claude", H / ".claude-home"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "tmux-usage-live.json").write_text(json.dumps(usage))

# ---- an invented transcript: what a day on one "api" session looks like ----
turns = [
    ("给 api 加一个健康检查接口", "加好了：GET /healthz，查数据库连接和队列长度。"),
    ("顺便把超时配置收到一个地方", "都挪进 config/timeouts.go 了，原来散在 4 个文件里。"),
    ("跑一下全部测试", "全过了，126 个用例，用时 41 秒。"),
    None,  # /compact here
    ("把 api/handlers 里重复的分页逻辑抽成一个 helper", "抽成 internal/paging，三个 handler 都改成调用它。"),
    ("orders 那个接口为什么没有上限校验", "原来漏了：limit=100000 会直接打到数据库，已经补上最大 200。"),
    ("给 paging 写单测", "加了 9 个用例，边界值（0、负数、超上限）都覆盖了。"),
    ("提交一下", "提交了 3f2a91c「抽出分页 helper，补 orders 上限校验」。"),
    ("下一步把 users 列表也接上游标分页", "可以：游标用（创建时间, id），要不要连前端一起改？"),
]
if os.environ.get("CLAUDE_TMUX_LANG") == "en":
    turns = [
        ("add a health check endpoint to api", "Done: GET /healthz checks the DB connection and the queue depth."),
        ("while you're at it, put the timeouts in one place", "Moved them all into config/timeouts.go; they were spread over 4 files."),
        ("run the whole test suite", "All green: 126 tests in 41 s."),
        None,  # /compact here
        ("pull the duplicated paging logic in api/handlers into a helper", "Extracted internal/paging; the three handlers now call it."),
        ("why does the orders endpoint have no upper limit", "It was missing: limit=100000 went straight to the DB. Capped at 200 now."),
        ("write unit tests for paging", "Added 9 cases covering the edges (0, negative, over the cap)."),
        ("commit it", "Committed 3f2a91c \"Extract paging helper, cap orders limit\"."),
        ("next, move the users list to cursor paging too", "Sure: cursor on (created_at, id). Change the frontend as well?"),
    ]
proj = H / ".claude" / "projects" / "demo"
proj.mkdir(parents=True, exist_ok=True)
tf = proj / "00000000-0000-4000-8000-000000000000.jsonl"
lines, t = [], now - 3 * 3600
for i, turn in enumerate(turns):
    t += 15 * 60
    if turn is None:
        lines.append({"type": "system", "subtype": "compact_boundary", "timestamp": utc(t)})
        continue
    ask, reply = turn
    lines.append({"type": "user", "uuid": f"u{i}", "timestamp": utc(t), "origin": {"kind": "human"},
                  "message": {"role": "user", "content": ask}})
    lines.append({"type": "assistant", "uuid": f"a{i}", "timestamp": utc(t + 90),
                  "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
tf.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")

import claude_trail as ct  # noqa: E402  (after HOME and the fixtures are in place)
import trail_daemon as td  # noqa: E402

tr = td.Transcript(tf)
tr.update()
sw = {"state": "running"} if state == "running" else {}
extra = ct.status_lines(width, "Opus 4.8/high", "34% (340k)", usage, "", cfg)
header = td.header_of(tr, tf, extra, usage, None, 3, cfg, sw).split("\n")
rows = [r.split("\t", 1)[1] for r in td.rows_of(tr, False, None, width).split("\0")]

POINTER, RESET, BOLD, SEL = "▌", "\033[0m", "\033[1m", "\033[48;5;236m"
screen = ["  " + ln for ln in header]
screen.append("\033[38;5;240m" + "─" * width + RESET)
body = []
for i, r in enumerate(rows):
    last = i == len(rows) - 1
    for j, ln in enumerate(r.split("\n")):
        pre = (f"\033[38;5;161m{POINTER}{RESET} " if j == 0 else "  ") if last else "  "
        body.append(pre + (SEL + ln + RESET if last else ln))
    if not last:
        body.append("")          # fzf --gap: a blank line between items
room = height - len(screen)
screen += body[-room:] if len(body) > room else body


def clip(line, cols):
    """Cut a line to `cols` display columns (CJK = 2), keeping colour codes; mark the cut with … like fzf."""
    out, used, i = "", 0, 0
    while i < len(line):
        m = ct.ANSI.match(line, i)
        if m:
            out += m.group(0); i = m.end(); continue
        w = ct._cols(line[i])
        if used + w > cols - 1 and ct._cols(ct.ANSI.sub("", line[i:])) > cols - used:
            return out + "…" + RESET
        out += line[i]; used += w; i += 1
    return out


Path(out).write_text("\n".join(clip(ln, width + 2) for ln in screen) + "\n")
