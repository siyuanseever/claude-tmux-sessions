#!/usr/bin/env python3
"""docs/demo.gif — claude-trail 在 herdr 里的样子，十来秒：真插件、真 herdr、假数据。

    python3 demo/gif.py            → docs/demo.gif（各帧在 $TMPDIR/claude-trail-gif/）

怎么做的：在一个私有 tmux server（-L herdrdemo）里，用假 $HOME（/private/tmp/herdrdemo）起一个完全隔离的
herdr（环境变量用 env -i 清空，不会连到你正在用的 herdr）。窗格里跑 fake_claude.py——把一份编出来的对话记录
画成 Claude 全屏界面的样子，能翻页——轨迹就是真插件自己开出来的。点击是往 herdr 客户端发真实的鼠标序列，
抓的是外层 tmux 那一格（= herdr 的整个屏幕），ANSI → PNG（render.sh：headless Chrome）→ GIF（ffmpeg）。

分镜：
  1. 轨迹在 Claude 右边打开，选中最新一轮
  2. 点第 3 轮：Claude 滚到那一轮的回复
  3. 点左上角 »：拉到窄版
  4. 点 «：拉回宽版
  5. 点底部的 Git 按钮：lazygit 浮窗

诚实边界：Claude 是假的（不调模型）；对话、账号、额度数字是编的；配色是 render.sh 里的一套调色板加上 herdr
catppuccin 主题的真颜色；图标用 unicode 档（渲染时不依赖 Nerd Font）。布局、轨迹、跳转、宽窄切换、浮窗都是真代码。
"""
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(ARGS[0]) if ARGS else PLUGIN / "docs" / "demo.gif"
HOME = Path("/private/tmp/herdrdemo")          # macOS 的 /tmp 是软链，herdr 报的是真实路径
WORK = Path(os.environ.get("TMPDIR", "/tmp")) / "claude-trail-gif"
HERDR = shutil.which("herdr") or str(Path.home() / ".local" / "bin" / "herdr")
W, H = 200, 44                     # 窄了宽版放不下：轨迹要给 Claude 留 110 列
STAGE = "--stage" in sys.argv      # 只搭好环境、不录，留着手动试（tmux -L herdrdemo attach）
# 演示用的 herdr 只认假 $HOME 里的 socket：显式写上，所有 herdr 命令（包括最后的 server stop）都只打到它
ENV = {"HOME": str(HOME), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
       "TERM": "xterm-256color", "LANG": "en_US.UTF-8",
       "HERDR_SOCKET_PATH": str(HOME / ".config" / "herdr" / "herdr.sock")}
TMUX = ["tmux", "-L", "herdrdemo", "-f", "/dev/null"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def sh(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw).stdout


def herdr(*args):
    return sh("env", "-i", *(f"{k}={v}" for k, v in ENV.items()), HERDR, *args)


def screen():
    return sh(*TMUX, "capture-pane", "-p", "-t", "demo")


def cols(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def find(text, right_of=0):
    """屏幕上 text 第一次出现的位置 (列, 行)，从 1 数；right_of：只找这一列右边的（避开 herdr 自己的侧栏）。"""
    for y, line in enumerate(screen().splitlines(), 1):
        i = line.find(text)
        while i >= 0:
            x = cols(line[:i]) + 1
            if x > right_of:
                return x, y
            i = line.find(text, i + 1)
    raise SystemExit(f"屏幕上找不到 {text!r}:\n{screen()}")


def click(x, y):
    """真实的鼠标点击：按下、松开分两次发（fzf 认表头点击要这样，见 PHASE1-RESULT「herdr 的限制」）。"""
    sh(*TMUX, "send-keys", "-t", "demo", "-l", f"\x1b[<0;{x};{y}M")
    time.sleep(0.15)
    sh(*TMUX, "send-keys", "-t", "demo", "-l", f"\x1b[<0;{x};{y}m")


frames = []


def shot(secs):
    n = len(frames) + 1
    ansi = WORK / f"f{n}.ansi"
    ansi.write_text(sh(*TMUX, "capture-pane", "-e", "-p", "-t", "demo"))
    png = WORK / f"f{n}.png"
    subprocess.run(["bash", str(HERE / "render.sh"), str(ansi), str(png), "14"], check=True,
                   stdout=subprocess.DEVNULL)
    frames.append((png, secs))


def trail_x():
    """轨迹窗格的左边缘在第几列：herdr 的 pane.layout 是 tab 区域里的坐标，加上 herdr 左侧栏的宽度。"""
    import json
    panes = json.loads(herdr("pane", "list"))["result"]["panes"]
    trail = next(p for p in panes if p.get("label") == "Trail")
    lay = json.loads(herdr("pane", "layout", "--pane", trail["pane_id"]))["result"]["layout"]
    rect = next(p["rect"] for p in lay["panes"] if p["pane_id"] == trail["pane_id"])
    return rect["x"] + (W - lay["area"]["width"]) + 1


def main():
    if sh("defaults", "read", "com.google.Chrome", "GenAILocalFoundationalModelSettings").strip() != "1":
        raise SystemExit("先设 Chrome 策略，免得它下载本地 AI 模型："
                         "defaults write com.google.Chrome GenAILocalFoundationalModelSettings -int 1")
    sh(*TMUX, "kill-server")
    if (HOME / ".config" / "herdr" / "herdr.sock").exists():   # 上次没收干净的演示 server（tmux 关了它还在）
        herdr("server", "stop")
        time.sleep(1)
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    HOME.mkdir(parents=True)
    WORK.mkdir(parents=True)
    sid = sh("env", "-i", *(f"{k}={v}" for k, v in ENV.items()), sys.executable, "-I",
             str(HERE / "fixture.py"), str(HOME), str(PLUGIN)).strip()
    transcript = next((HOME / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
    work = HOME / "work" / "photo-tidy"
    herdr("plugin", "link", str(PLUGIN))
    try:
        sh(*TMUX, "new-session", "-d", "-s", "demo", "-x", str(W), "-y", str(H), "-c", str(work),
           "env -i " + " ".join(f"{k}={v}" for k, v in ENV.items()) + f" {HERDR}")
        time.sleep(4)
        # 新装的 herdr 先弹欢迎框（只认回车，Esc 关不掉，开着时鼠标全被它吞了），回车后再弹 integrations 设置（Esc 关）
        for key in ("Enter", "Escape"):
            sh(*TMUX, "send-keys", "-t", "demo", key)
            time.sleep(1)
        herdr("pane", "run", "w1:p1", f"clear; exec python3 {HERE / 'fake_claude.py'} {transcript} {sid}")
        for _ in range(40):                     # 等轨迹自己打开（pane.agent_detected → trail.py auto）
            time.sleep(0.5)
            if "turns" in screen():
                break
        time.sleep(2)
        if STAGE:
            print("搭好了：tmux -L herdrdemo attach -t demo；收掉：再跑一次不带 --stage 的，或 tmux -L herdrdemo kill-server")
            return
        tx = trail_x()

        shot(2.6)                                                   # 1. 打开
        x, y = find("#3 ", right_of=tx)
        click(x + 6, y)
        time.sleep(2)
        shot(2.8)                                                   # 2. 跳到第 3 轮
        click(*find("»", right_of=tx))
        time.sleep(2.5)
        shot(2.2)                                                   # 3. 窄版
        click(*find("«", right_of=trail_x()))
        time.sleep(2.5)
        shot(2.2)                                                   # 4. 拉回宽版
        tx = trail_x()
        git = find("±", right_of=tx)                                # 底部的 Git 按钮（unicode 档的图标）
        click(*git)
        time.sleep(2.5)
        shot(2.8)                                                   # 5. lazygit 浮窗
    finally:
        if not STAGE:
            sh(*TMUX, "kill-server")
            herdr("server", "stop")
            time.sleep(1)
    if STAGE:
        return

    # 帧补齐到同一尺寸再拼：palettegen 让 GIF 的颜色不糊
    lst = WORK / "frames.txt"
    lst.write_text("".join(f"file '{p}'\nduration {d}\n" for p, d in frames) + f"file '{frames[-1][0]}'\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse",
                    "-loop", "0", str(OUT)], check=True)
    print(OUT, f"{sum(d for _, d in frames):.1f}s", f"{OUT.stat().st_size // 1024} KB")
    shutil.rmtree(HOME, ignore_errors=True)


main()
