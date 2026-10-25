#!/usr/bin/env python3
"""小红书组图：8 张 1080×1440 竖图 → docs/xhs/01.png … 08.png。

    python3 docs/promo/xhs/build.py

每张图是一页 HTML（图用这个目录里的裁剪图和 docs/img/ 下的截图，全是演示数据），无界面 Chrome 按 2 倍截图再缩回 1080×1440。
Chrome 用一个固定的配置目录，只杀自己起的那个；起之前确认它不会下载本地 AI 模型。
"""
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent
IMG = OUT.parents[1] / "img"
TMP = Path(os.environ.get("TMPDIR", "/tmp")) / "claude-tmux-xhs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = Path(os.environ.get("TMPDIR", "/tmp")) / "claude-tmux-render-chrome"
W, H = 1080, 1440

CSS = """
:root { --bg:#121417; --card:#1b1d22; --fg:#ecebe8; --dim:#9aa0a6; --accent:#d97757; --line:#2c2f36;
        --wait:#e5534b; --done:#8fd15b; --run:#e8c547; --read:#6ea8fe; }
* { box-sizing:border-box; margin:0; }
html, body { width:1080px; height:1440px; background:var(--bg); color:var(--fg);
  font-family:"PingFang SC","Hiragino Sans GB",-apple-system,sans-serif; }
.page { width:1080px; height:1440px; padding:84px 72px 64px; display:flex; flex-direction:column; position:relative; }
.kicker { color:var(--accent); font-size:30px; font-weight:600; letter-spacing:2px; }
h1 { font-size:66px; line-height:1.22; font-weight:800; margin-top:18px; }
h1 em { color:var(--accent); font-style:normal; }
.sub { color:var(--dim); font-size:34px; line-height:1.5; margin-top:22px; }
.shot { margin-top:40px; border:1px solid var(--line); border-radius:22px; overflow:hidden; background:#1b1d1e; }
.shot img { display:block; width:100%; }
ul { list-style:none; padding:0; margin-top:40px; }
li { font-size:34px; line-height:1.5; padding:16px 0 16px 46px; position:relative; border-bottom:1px solid var(--line); }
li:last-child { border-bottom:none; }
li::before { content:""; position:absolute; left:6px; top:32px; width:16px; height:16px; border-radius:50%; background:var(--accent); }
li b { color:var(--fg); } li span { color:var(--dim); }
.chips { display:flex; gap:16px; flex-wrap:wrap; margin-top:36px; }
.chip { font-size:30px; padding:10px 26px; border-radius:999px; background:var(--card); border:1px solid var(--line); }
.foot { position:absolute; left:72px; right:72px; bottom:52px; display:flex; justify-content:space-between;
  color:var(--dim); font-size:26px; }
.pain { margin-top:44px; display:flex; flex-direction:column; gap:28px; }
.pain div { background:var(--card); border:1px solid var(--line); border-radius:22px; padding:34px 38px; }
.pain .q { font-size:42px; font-weight:700; } .pain .a { font-size:32px; color:var(--dim); margin-top:12px; }
.pain .a b { color:var(--accent); }
.legend { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:36px; }
.legend div { background:var(--card); border:1px solid var(--line); border-radius:18px; padding:22px 26px; font-size:32px; }
.legend i { font-style:normal; font-weight:800; margin-right:14px; }
.arrow { text-align:center; color:var(--accent); font-size:44px; margin:18px 0; }
pre { margin-top:36px; background:var(--card); border:1px solid var(--line); border-radius:18px; padding:28px 30px;
  font:28px/1.6 Menlo,monospace; color:var(--fg); white-space:pre-wrap; word-break:break-all; }
.url { margin-top:30px; font:34px Menlo,monospace; color:var(--accent); }
"""


def img(name):   # 先找这个目录里的裁剪图，再找 docs/img/
    path = OUT / name if (OUT / name).exists() else IMG / name
    return f'<div class="shot"><img src="{path.as_uri()}"></div>'


def page(n, body):
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=1080">
<style>{CSS}</style><div class="page">{body}
<div class="foot"><span>claude-tmux-sessions · 开源</span><span>{n} / 8</span></div></div>"""


def lis(items):
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


SLIDES = [
    # 1 封面
    """<div class="kicker">CLAUDE CODE × TMUX</div>
<h1>一个人，<br>带<em>两支 Claude 团队</em></h1>
<div class="sub">我给 Claude Code 写了一套 tmux 控制台，已开源</div>
<div class="chips"><span class="chip">底栏</span><span class="chip">选择器</span><span class="chip">侧栏</span><span class="chip">一键换账号</span></div>
""" + img("cover-crop.png"),
    # 2 用法
    """<div class="kicker">我的用法</div>
<h1>一个账号管一个项目，<br>一个会话担一个职位</h1>
""" + img("framework.png") + lis([
        "<b>账号分项目</b><span>：额度各算各的，互不卡</span>",
        "<b>会话分职位</b><span>：上下文只装自己那摊事</span>",
        "<b>我只做判断</b><span>：会话需要我时才过去</span>"]),
    # 3 痛点
    """<div class="kicker">问题</div>
<h1>会话一多，<br>三件事最费神</h1>
<div class="pain">
<div><div class="q">① 不知道谁在等我</div><div class="a">权限框弹着，它干等了二十分钟 → <b>底栏</b></div></div>
<div><div class="q">② 不知道大家在干什么</div><div class="a">只能一个窗口一个窗口切过去看 → <b>选择器、侧栏</b></div></div>
<div><div class="q">③ 一个号的额度用完了</div><div class="a">整条线停下，等几个小时 → <b>换账号</b></div></div>
</div>""",
    # 4 底栏
    """<div class="kicker">底栏</div>
<h1>底栏一直告诉你：<br><em>谁在等你</em></h1>
""" + img("bar-left.png") + """
<div class="legend">
<div><i style="color:var(--wait)">⏸</i>在等你确认</div><div><i style="color:var(--done)">✔</i>做完了，你还没看</div>
<div><i style="color:var(--run)">▶</i>在跑，不用管</div><div><i style="color:var(--read)">✓</i>做完了，你看过了</div>
</div>
<div class="sub" style="margin-top:34px">有会话卡在确认上，⏸ 变红，那个窗口在窗口列表里也是红的，点 ⏸ 直接跳过去：</div>
""" + img("statusbar.png") + """
<div class="sub" style="margin-top:34px">右边是整机 CPU、内存、交换、磁盘。每一段都能点：</div>
""" + img("bar-right.png"),
    # 5 选择器
    """<div class="kicker">选择器</div>
<h1>一个键，<br>一眼看完所有会话</h1>
""" + img("picker-list.png") + lis([
        "<b>按项目分组</b><span>：一个 tmux session 一个项目</span>",
        "<b>右边是实时画面</b><span>：光标停在哪个就看哪个</span>",
        "<b>按编号直接跳</b><span>：看过的自动变 ✓，列表只留要你处理的</span>"]),
    # 6 侧栏
    """<div class="kicker">侧栏</div>
<h1>侧栏只管<br><em>眼前这一个会话</em></h1>
""" + img("sidebar-short.png") + lis([
        "<b>每一轮一项</b><span>：点一下，主界面滚到那一轮</span>",
        "<b>额度三条</b><span>：上下文、5 小时、7 天，快超了才变色</span>",
        "<b>一排按钮</b><span>：git、文件、刷新额度、换账号</span>"]),
    # 7 换账号
    """<div class="kicker">换账号</div>
<h1>额度用完，<br>点一下 ⇄ 接着干</h1>
""" + img("hdr-before.png") + """<div class="arrow">↓ 点 ⇄，同一个会话换到另一个账号</div>""" + img("hdr-after.png") + lis([
        "<b>对话接着聊</b><span>：用另一个账号恢复同一个会话</span>",
        "<b>拿不准就不动</b><span>：在干活、输入框里有字，一律跳过</span>"]),
    # 8 开源
    """<div class="kicker">开源</div>
<h1>三步装好</h1>
<pre>git clone https://github.com/threedaycat/claude-tmux-sessions.git
cd claude-tmux-sessions && ./install.sh
# tmux 配置里加一行：
source-file ~/…/claude-tmux-sessions/tmux/claude-tmux.conf</pre>
<div class="url">github.com/threedaycat/claude-tmux-sessions</div>
""" + lis([
        "<b>macOS 上日常在用</b><span>：Linux 还没测</span>",
        "<b>界面是中文</b><span>：要装 Nerd Font 显示图标</span>",
        "<b>欢迎提 issue</b><span>：用得上就点个 star</span>"]),
]


def main():
    if subprocess.run(["defaults", "read", "com.google.Chrome", "GenAILocalFoundationalModelSettings"],
                      capture_output=True, text=True).stdout.strip() != "1":
        sys.exit("先设 Chrome 策略：defaults write com.google.Chrome GenAILocalFoundationalModelSettings -int 1")
    TMP.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    for i, body in enumerate(SLIDES, 1):
        h, raw = TMP / f"s{i}.html", TMP / f"s{i}@2x.png"
        h.write_text(page(i, body), encoding="utf-8")
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={PROFILE}"], capture_output=True)
        raw.unlink(missing_ok=True)
        subprocess.run([CHROME, "--headless=old", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={PROFILE}", "--force-device-scale-factor=2",
                        f"--window-size={W},{H}", f"--screenshot={raw}", h.as_uri()],
                       capture_output=True, timeout=60)
        Image.open(raw).convert("RGB").resize((W, H), Image.Resampling.LANCZOS).save(OUT / f"{i:02d}.png", optimize=True)
        print(OUT / f"{i:02d}.png")


if __name__ == "__main__":
    main()
