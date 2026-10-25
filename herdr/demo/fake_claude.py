#!/usr/bin/env python3
"""演示用的假 Claude Code：把一份对话记录画成 Claude 全屏界面的样子，能翻页、到顶、到底、滚轮，
往上翻了就在底部显示「Jump to bottom」——statetrail 的跳转靠的就是这些，所以演示里的跳转是真跳。

    fake_claude.py <对话记录.jsonl> <会话 id>

起来后向 herdr 报上自己是 claude（herdr pane report-agent），herdr 就把这一格当成 Claude，轨迹也就认得它。
只是个画面：不调任何模型，也不响应输入框里的打字。
"""
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import textwrap
import tty
import unicodedata

PATH, SID = sys.argv[1], sys.argv[2]
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
ORANGE, GREY, BLUE = "\033[38;2;215;119;87m", "\033[38;2;136;136;136m", "\033[38;2;87;105;247m"


def cols(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def body_lines(width):
    """对话记录 → 屏幕上的行：你的话 ❯ 开头，回复 ⏺ 开头，回复后面接一行耗时，和 Claude 的样子一样。"""
    lines = [f" {ORANGE}✻{RESET} {BOLD}Claude Code{RESET}", f"   {GREY}~/work/photo-tidy{RESET}", ""]
    for raw in open(PATH):
        e = json.loads(raw)
        msg = e.get("message") or {}
        if e.get("type") == "user":
            for i, ln in enumerate(textwrap.wrap(msg["content"], width - 4) or [""]):
                lines.append((f"{GREY}❯{RESET} " if i == 0 else "  ") + f"{GREY}{ln}{RESET}")
            lines.append("")
        elif e.get("type") == "assistant":
            text = "".join(b.get("text", "") for b in msg.get("content", []))
            first = True
            for para in text.split("\n"):
                for ln in textwrap.wrap(para, width - 4) or [""]:
                    lines.append(("⏺ " if first else "  ") + ln)
                    first = False
            lines += ["", f"{GREY}✻ Worked for 38s{RESET}", ""]
    return lines


class Screen:
    def __init__(self):
        self.offset = 0                 # 从底往上翻了几行

    def draw(self):
        w, h = shutil.get_terminal_size()
        lines = body_lines(w)
        foot = [f"{GREY}{'─' * w}{RESET}", f"{GREY}❯{RESET} ", f"{GREY}{'─' * w}{RESET}",
                f"  {GREY}⏵⏵ auto mode on (shift+tab to cycle){RESET}"]
        room = h - len(foot)
        self.offset = max(0, min(self.offset, len(lines) - room))
        end = len(lines) - self.offset
        view = lines[max(0, end - room):end]
        if self.offset:                 # 往上翻了：和 Claude 一样，底部提示怎么回去
            view = view[1:] + [" " * (w - 26) + f"{BLUE}Jump to bottom (End) ↓{RESET}"]
        view = [""] * (room - len(view)) + view
        sys.stdout.write("\033[H\033[2J" + "\r\n".join(view + foot))
        sys.stdout.flush()

    def scroll(self, n):
        self.offset += n
        self.draw()


def report(state):
    """告诉 herdr 这一格是 claude、会话 id 是多少。状态用自定义来源报；会话 id 要用 herdr 自带 Claude 集成的
    来源 herdr:claude 报——内置的 claude 只认它报的会话（实测用 custom:demo 报，agent_session 一直是空的）。"""
    pane = os.environ.get("HERDR_PANE_ID")
    if pane:
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        subprocess.run([herdr, "pane", "report-agent", pane, "--source", "custom:demo", "--agent", "claude",
                        "--state", state], capture_output=True)
        subprocess.run([herdr, "pane", "report-agent-session", pane, "--source", "herdr:claude", "--agent", "claude",
                        "--agent-session-id", SID, "--agent-session-path", PATH], capture_output=True)


def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    sys.stdout.write("\033[?1049h\033[?25l\033[?1000h\033[?1006h\033]0;photo-tidy CLI\007")
    scr = Screen()
    signal.signal(signal.SIGWINCH, lambda *_: scr.draw())
    scr.draw()
    report("idle")
    keys = {b"\x1b[5~": "pgup", b"\x1b[6~": "pgdn", b"\x1b[1;5H": "top", b"\x1b[1;5F": "end"}
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 30)
            if not r:
                continue
            data = os.read(fd, 4096)
            if not data or data in (b"\x03", b"\x04"):
                break
            h = shutil.get_terminal_size().lines - 4
            for seq in re.findall(rb"\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[[\d;]*[~A-Za-z]|.", data, re.S):
                act = keys.get(seq)
                m = re.match(rb"\x1b\[<(\d+);\d+;\d+M", seq)
                if act == "pgup":
                    scr.offset += h // 2
                elif act == "pgdn":
                    scr.offset -= h // 2
                elif act == "top":
                    scr.offset = 10 ** 6
                elif act == "end":
                    scr.offset = 0
                elif m and m.group(1) in (b"64", b"65"):      # 滚轮：上 / 下，一格 3 行
                    scr.offset += 3 if m.group(1) == b"64" else -3
            scr.draw()
    finally:
        sys.stdout.write("\033[?1000l\033[?1006l\033[?25h\033[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


main()
