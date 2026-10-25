#!/usr/bin/env python3
"""claude-trail: list every time Claude stopped in a session, and scroll Claude's screen to it.

Reads the transcripts Claude Code already writes to ~/.claude/projects/*/*.jsonl.
The tmux sidebar (trail-sidebar.sh) drives it through the subcommands at the bottom;
each takes the tmux pane that runs Claude and finds that pane's current session.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
# Written by hooks/tmux_status_update.py: {"%pane": {"session_id": ..., ...}}
STATUS_FILE = Path.home() / ".claude" / "tmux-claude-status.json"

# 翻一次之后先等多久再截屏（秒）。每趟 tmux 调用要 ~8ms，截早了就得重截。
REDRAW_WAIT = float(os.environ.get("CLAUDE_TRAIL_WAIT", "0.015"))

# Plain-string user entries that are harness noise, not something that makes Claude reply.
NOISE_PREFIXES = ("<local-command", "<command-name>", "<command-message>",
                  "This session is being continued from a previous conversation")
SLASH_CMD = re.compile(r"^/[\w:-]+(\s|$)")

def _read_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue  # a line still being written


def _text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _trigger(entry):
    """If this entry starts a new stretch of Claude work, return (kind, text)."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    kind = (entry.get("origin") or {}).get("kind")
    text = _text_of(content).strip()
    if kind == "task-notification":
        return ("notify", "后台任务完成")
    if kind == "peer":
        return ("peer", "收到其他会话的消息")
    if entry.get("isMeta") or not text:
        return None
    if kind == "human":
        return ("human", text)
    if text.startswith(NOISE_PREFIXES) or text.startswith("<") or SLASH_CMD.match(text):
        return None
    return ("human", text)


def parse_session(path):
    """Split a transcript into stops: each trigger plus the texts Claude wrote after it."""
    stops, title, cur = [], None, None
    for e in _read_jsonl(path):
        t = e.get("type")
        if t == "ai-title":
            title = e.get("aiTitle") or title
            continue
        if t == "system" and e.get("subtype") == "compact_boundary":
            # Claude's screen restarts here: earlier turns are no longer shown in it.
            for st in stops:
                st["compacted"] = True
            continue
        trig = _trigger(e)
        if trig:
            cur = {"kind": trig[0], "prompt": trig[1], "ts": e.get("timestamp"),
                   "id": e.get("uuid"), "texts": [], "end": None, "compacted": False}
            stops.append(cur)
            continue
        if t == "assistant" and not e.get("isSidechain") and cur is not None:
            txt = _text_of((e.get("message") or {}).get("content")).strip()
            if txt:
                cur["texts"].append(txt)
                cur["end"] = e.get("timestamp")
    # A notification/peer stretch where Claude said nothing is noise; a human prompt
    # with no reply yet (still working, or interrupted) stays so you can see it.
    stops = [s for s in stops if s["texts"] or s["kind"] == "human"]
    for i, s in enumerate(stops, 1):
        s["n"] = i
        s["final"] = s["texts"][-1] if s["texts"] else ""
        s["earlier"] = s["texts"][:-1]
        del s["texts"]
    return {"title": title, "stops": stops}


def get_session(path):
    return parse_session(path)


# ---- CLI for the tmux sidebar -------------------------------------------------

# 表头第 2 行：一排图标按钮 (图标, 动作)。以前是「[主界面到最底]  [刷新额度]  [关闭]」加一行
# 「[git]  [文件]」，方括号加文字又乱又不直观（2026-10-22 用户：「能不能用 icon 的形式来表示」）。
# 图标是 Nerd Font 的（iTerm 用 HackNFM），都在 BMP 私用区，终端和 fzf 都按 1 格算。
# 每个按钮是「空格 + 图标 + 空格」再加上它自己的一小段状态（git 后面是没提交的文件数，刷新后面是
# 额度数据多久前的）。状态段按最长的情况留**固定宽度**，数字变长变短后面的按钮都不挪位，
# cmd_click 按固定格子认按钮；按钮之间隔 1 格。一格宽的图标太难点中，点偏一格也还落在按钮里，
# 点在后面的状态文字上也算点了这个按钮。（2026-10-22 用户：「刷新的那个按钮……这个数字要写到这个位置」）
BUTTONS = [                  # (图标, 动作, 状态段宽度)
    ("\uf103", "bottom", 0),   # 两个向下箭头：主界面到最底
    ("\ue702", "git", 4),      # git 标志：在这个 Claude 的目录开 lazygit；后面「 12」= 12 个文件没提交
    ("\uf07c", "files", 0),    # 打开的文件夹：在这个 Claude 的目录开 yazi
    ("\uf021", "usage", 10),   # 刷新：重新查订阅额度；后面「 15 分钟前」= 额度数据多新
    ("\uf0ec", "account", 5),  # ⇄：把这个 Claude 换到另一个账号；后面「 l2」= 现在登的是谁
    ("\uf00d", "close", 0),    # 叉：关掉侧栏
]
# 切账号用 bin/switch_account.py（安全闸、带队友先收队都在那边），这里只是个入口。
REPO = Path(__file__).resolve().parent.parent    # 这个仓库的根：sidebar/ 的上一层


def _first(*paths):
    """第一个存在的路径；都不存在就返回第一个（报错信息里写它）。"""
    return next((p for p in paths if p.exists()), paths[0])


# 仓库里的那份优先；旧的单独仓库（usage-monitor）是搬进来之前的位置，还没搬完的机器上用它
SWITCHER = _first(REPO / "bin" / "switch_account.py", Path.home() / "projects" / "usage-monitor" / "switch_account.py")
SWITCH_DIR = Path.home() / ".local" / "state" / "claude-trail" / "run"   # <Claude 窗格>.switch：切换进度
SLOT, GAP = 3, 1
TOOLS = {"git", "files"}          # 交给 bin/float-tool.sh
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"

# git / 文件 原来只在 tmux 底栏上，可它们开的是「这个 Claude 所在的目录」，跟别的窗口无关；
# 底栏的位置也不够了，所以搬到侧栏上（2026-10-22 用户：「只跟当前相关的内容放侧边栏上」）。
# 弹窗本身是 bin/float-tool.sh，传 Claude 窗格过去，目录就取它的。
# 弹窗一律浮在右边、点外面就关，盖住侧栏也没关系：点左边的 Claude 就回来了。
FLOAT_TOOL = _first(REPO / "bin" / "float-tool.sh", Path.home() / "dotfiles" / "bin" / "float-tool.sh")

# ---- 表头第 3~5 行：这个 Claude 的模型/上下文 + 订阅额度 ----------------------
# 这三行原来在 tmux 底栏右边。底栏一行只有几十列，额度只能画成两个字形（用量一格、
# 时间一格），数字得点开才看得到；侧栏有 60 多列，画得下整条，所以搬过来，底栏那边
# 在有侧栏时就不画了（tmux.conf.local 的 status-right 按 #{P:#{@trail_for}} 判断）。
DEFAULT_CFG = Path.home() / ".claude"


def cfg_files(cfg=""):
    """这个窗格的 Claude 用哪个配置目录 → (额度文件, .claude.json)。

    两个账号可以同时在线（claude 和 claude2，CLAUDE_CONFIG_DIR 各指一个目录），额度和
    登录账号都是各自一份，所以这两个文件得按窗格来——ctx_to_tmux.py 把那个目录写在窗格的
    @claude_config 上。空的就是默认那套；默认那套的 .claude.json 在家目录下，不在 ~/.claude/ 里。"""
    d = Path(cfg) if cfg else DEFAULT_CFG
    conf = Path.home() / ".claude.json" if d == DEFAULT_CFG else d / ".claude.json"
    return d / "tmux-usage-live.json", conf        # 前者 usage-refresh.py 写，后者 Claude Code 自己写
WINDOW_SECS = {"5h": 5 * 3600, "7d": 7 * 86400}
# 没有要提醒的时候就淡灰，别晃眼睛（2026-10-08 用户：「默认不要黄色了，黄色有点晃
# 眼睛，默认没有提醒的时候就淡灰色好了」）；要提醒才上颜色，这样颜色一出现就是信号。
USED_C = "\033[38;2;158;158;158m"  # 淡灰：已经用掉的，一切正常
WARN_C = "\033[38;2;215;135;0m"    # 橙：该留意了
ALERT_C = "\033[38;2;215;95;95m"   # 红：真的要注意了
TIME_C = "\033[38;2;0;135;215m"    # 蓝：窗口已经走过的时间，和底栏那两格同一套颜色
GREY = "\033[38;2;108;108;108m"
DIMC = "\033[38;2;88;88;88m"


def read_usage(cfg=""):
    try:
        return json.loads(cfg_files(cfg)[0].read_text())
    except (OSError, ValueError):
        return None


def _left_str(secs):
    """还有多久重置，窄到塞得进一行：1d14h / 2h01m / 37m。"""
    if secs <= 0:
        return "  已过"
    d, r = divmod(int(secs), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m".rjust(6)


# 一行的固定开销：`ctx ` 4 列 + ` 100%` 5 列 + 尾巴前的那个空格 1 列
BAR_FIXED = 10


def _bar_w(width, tails=()):
    """三条柱子（ctx / 5h / 7d）共用一个宽度，列才对得齐；按**最长的那条尾巴**反推，
    尾巴就不会被挤出右边界。以前柱子宽度写死，窄侧栏里 `626k Opus 5.5/medium` 被
    截成 `Opus 5.5…`，推理力度看不见了（2026-10-08 用户报：「你现在的写法我看不到
    模型/effort 的完成信息了」）。"""
    longest = max((_cols(t) for t in tails), default=6)
    room = width - BAR_FIXED - longest
    # 不设上限，剩多少用多少 —— 搬进侧栏本来就是图这儿地方大。
    # 窄到连 4 格都放不下就三条都不画柱子（只剩 `ctx  62% 628k`）：三条要一起有
    # 或一起没有，才还是对齐的。
    return 0 if room < 4 else room


def _bar_line(label, frac, mark, tail, bar_w, used_c=USED_C):
    """`5h ████▌░░░░│░░░░  24% 2h01m`

    实心 = 已经用掉的比例；蓝色竖线 = 那条参照线（额度是「窗口已经走过的时间」，
    上下文是「自动压缩的那条线」）。线在实心右边就是还有富余，被实心盖过去就是过了。
    底栏那两个字形是同一个意思，只是这里一格一格画得出来，不用靠比高度。"""
    lab = f"{GREY}{label.ljust(3)} {RESET}"
    if frac is None:
        return f"{DIMC}{label.ljust(3)} {'·' * bar_w}    ? {tail}{RESET}"
    fill = max(0, min(bar_w, round(max(0.0, min(1.0, frac)) * bar_w)))
    if bar_w == 0:
        return f"{lab}{frac * 100:3.0f}% {GREY}{tail}{RESET}"
    if mark is None or not 0.0 <= mark <= 1.0:
        body = f"{used_c}{'█' * fill}{RESET}{DIMC}{'░' * (bar_w - fill)}{RESET}"
    else:
        i = max(0, min(bar_w - 1, int(mark * bar_w)))
        m = f"{TIME_C}│{RESET}"
        body = (f"{used_c}{'█' * min(fill, i)}{RESET}" + (m if i < fill else "")
                + f"{used_c}{'█' * max(0, fill - i - 1)}{RESET}"
                + f"{DIMC}{'░' * max(0, i - fill)}{RESET}" + (m if i >= fill else "")
                + f"{DIMC}{'░' * max(0, bar_w - max(i + 1, fill))}{RESET}")
    return f"{lab}{body} {frac * 100:3.0f}% {GREY}{tail}{RESET}"


def quota_colour(frac, mark):
    """额度柱子的颜色：按「**比匀速线超前多少**」，不是按用了多少。

    5h 用掉 38%，在窗口第 3 小时是正常，在第 10 分钟是要命 —— 单看绝对百分比
    说明不了任何事，柱子上那条蓝线画的就是这个差。

    门槛压得比较高：平时不在意这个，颜色一出现就该是「快有问题了」
    （2026-10-08 用户：「平时不是很在意这个，等快有问题了才颜色提醒」）。
    超出匀速线 20 个百分点起橙、35 个起红；绝对值到 80%/93% 也分别橙、红
    （窗口快走完了也还是要知道自己快撞顶）。"""
    if frac is None:
        return USED_C
    over = frac - mark if mark is not None else 0.0
    if frac >= 0.93 or over >= 0.35:
        return ALERT_C
    if frac >= 0.80 or over >= 0.20:
        return WARN_C
    return USED_C


def _quota_tail(w):
    try:
        dt = datetime.fromisoformat((w or {}).get("resets_at") or "")
        return _left_str((dt - datetime.now().astimezone()).total_seconds())
    except (TypeError, ValueError):
        return "     ?"


def _quota_line(label, w, bar_w):
    pct = (w or {}).get("utilization")
    left = None
    try:
        dt = datetime.fromisoformat((w or {}).get("resets_at") or "")
        left = (dt - datetime.now().astimezone()).total_seconds()
    except (TypeError, ValueError):
        pass
    mark = None if left is None else 1.0 - max(0.0, min(1.0, left / WINDOW_SECS.get(label, 5 * 3600)))
    tail = _left_str(left) if left is not None else "     ?"
    frac = None if pct is None else float(pct) / 100
    return _bar_line(label, frac, mark, tail, bar_w, quota_colour(frac, mark))


# 上下文的目标线。settings.json 里 autoCompactWindow 改成 auto 之后这边就只剩这个值，
# 原来跟着 ctx_to_tmux.py 底栏那一格用 30 万，太早就上色了
# （2026-10-08 用户：「还是有点深，特别是 ctx 的，我们的目标线定高一些吧，500k 吧」）。
CTX_HARD = 500_000
CTX_RE = re.compile(r"(\d+)%\s*\((\d+)k\)")


def _auto_compact_at():
    """自动压缩的那条线。settings.json 里写了 autoCompactWindow 就用它；没写就退回
    CTX_HARD —— ctx_to_tmux.py 底栏那一格一直把 30 万当「该马上压缩」的满格线。
    （2026-10-08 实测：settings.json 里的 autoCompactWindow 会被 Claude Code 自己
    改没，不能当成一定在。）关掉自动压缩就不画这条参照线。"""
    try:
        d = json.loads((Path.home() / ".claude" / "settings.json").read_text())
    except (OSError, ValueError):
        return CTX_HARD
    if not d.get("autoCompactEnabled", True):
        return None
    return d.get("autoCompactWindow") or CTX_HARD


def _ctx_tail(ctx):
    """`626k`。模型和推理力度单独占一行，不跟柱子挤在一起
    （2026-10-08 用户：「Sonnet 5.5/medium 这个单独放一行怎么样？不要跟 ctx 挤在一起了」）。"""
    m = CTX_RE.search(ctx or "")
    return f"{m.group(2)}k" if m else None


def _ctx_line(model, ctx, bar_w, tail):
    """`ctx ██████│░░░░░░░░  21% 216k Sonnet 5.5/medium`

    @claude_ctx 是 "21% (216k)"：百分比已经是相对这个模型的上下文窗口算好的，
    直接拿来当柱子的填充比例。参照线是自动压缩那条（400k 之类），按同一个窗口折算——
    窗口大小用和 ctx_to_tmux.py 一样的规矩：haiku 20 万，其余 100 万。"""
    m = CTX_RE.search(ctx or "")
    if not m:
        return None
    pct, k = int(m.group(1)), int(m.group(2))
    tokens = k * 1000
    window = 200_000 if "haiku" in (model or "").lower() else 1_000_000
    at = _auto_compact_at()
    # 上下文没有「匀速」可言，按离自动压缩那条线多远：过八成橙、过线红。
    # 阈值从压缩线推，压缩线是可配的（settings.json 的 autoCompactWindow）。
    line = at or CTX_HARD
    colour = ALERT_C if tokens >= line else WARN_C if tokens >= line * 0.8 else USED_C
    return _bar_line("ctx", pct / 100, None if not at else at / window, tail, bar_w, colour)


def _fresh_mark(u):
    """只在有话说的时候出声：正在查 ⟳，刚查失败 ✗。和底栏 refresh_state 一个意思。"""
    if not u:
        return ""
    att, got, err = u.get("attempted_at_ms") or 0, u.get("fetched_at_ms") or 0, u.get("error_at_ms") or 0
    if att > got and att > err:
        return f" {GREY}⟳{RESET}"
    if err > got:
        return f" {ALERT_C}✗{RESET}"
    return ""


GAUGE_GLYPHS = "▁▂▃▄▅▆▇█"


_ACCT = {}


def current_account(cfg=""):
    """这个窗格里的 Claude 登的是哪个账号，取邮箱 @ 前面那段。

    两个账号（比如 you.a / you.b）可以同时在线，额度各算各的，所以 5h / 7d 两条说的
    是谁的额度，得看这一行。按文件和 mtime 缓存——这文件几十 MB，每次渲染都读太浪费。
    """
    conf = cfg_files(cfg)[1]
    try:
        mt = conf.stat().st_mtime
    except OSError:
        return ""
    hit = _ACCT.get(conf)
    if not hit or hit[0] != mt:
        try:
            a = json.loads(conf.read_text()).get("oauthAccount") or {}
            name = (a.get("emailAddress") or "").split("@")[0]
        except (OSError, ValueError):
            name = ""
        hit = _ACCT[conf] = (mt, name)
    return hit[1]


def usage_state(u):
    """刷新按钮后面那一小段：点了「刷新额度」到底有没有动静，额度数据多新。

    原来只有额度行尾一个 ⟳，查一次两三秒就过去了，数字还常常原样（半小时内本来就不会变），
    看起来就像没反应（2026-10-10 用户：「刷新额度点击没有反馈信息」「我不知道有没有刷新好」）。
    所以这里把三件事说出来：在查、查完了多久、查失败了。要塞进按钮后面的固定宽度，所以写短。"""
    if not u:
        return f"{DIMC}无读数{RESET}"
    att, got, err = u.get("attempted_at_ms") or 0, u.get("fetched_at_ms") or 0, u.get("error_at_ms") or 0
    if att > got and att > err:
        return f"{WARN_C}查询中…{RESET}"
    if err > got:
        return f"{ALERT_C}查询失败{RESET}"
    age = int(time.time() - got / 1000)
    if age < 90:
        return f"{USED_C}刚刚更新{RESET}"
    if age < 3600:
        return f"{DIMC}{age // 60} 分钟前{RESET}"
    return f"{DIMC}{min(age // 3600, 99)} 小时前{RESET}"


_SA = None


def _switcher():
    """bin/switch_account.py（账号配置在它那里读：~/.config/claude-tmux/accounts.json）。"""
    global _SA
    if _SA is None:
        sys.path.insert(0, str(SWITCHER.parent))
        import switch_account
        _SA = switch_account
    return _SA


def other_cfg(cfg=""):
    """另一个账号的配置目录（@claude_config 的写法：默认账号是空串）；只配了一个账号是 None。"""
    try:
        sa = _switcher()
    except Exception:
        return None
    cur = Path(os.path.expanduser(cfg)) if cfg else DEFAULT_CFG
    d = next((cd for _, cd in sa.ACCTS.values() if Path(cd) != cur), None)
    return None if d is None else ("" if Path(d) == DEFAULT_CFG else str(d))


def switch_status(pane):
    """这个 Claude 窗格的切换进度 {state, msg, at}；没在切 / 没切过是 {}。"""
    try:
        return json.loads((SWITCH_DIR / f"{pane}.switch").read_text())
    except (OSError, ValueError):
        return {}


def account_state(cfg="", sw=None):
    """账号按钮后面那一小段：**现在**登的是谁（只写两个账号名不一样的那截：you.a / you.b → a / b），
    前面是 ⇄，读起来就是「现在 l2，点一下换」。以前写「→l」（点了切到谁），跟开关的习惯反着，
    看着就不像个切换按钮（2026-10-23 用户：「应该显示他现在登录的……现在它并不是一个切换按钮」）。
    正在切是「…」，刚切失败 / 被跳过（30 秒内）是「✗」，原因在 tmux 底部那行。"""
    sw = sw or {}
    if sw.get("state") == "running":
        return f"{WARN_C}…{RESET}"
    if sw.get("state") in ("fail", "skip") and time.time() - (sw.get("at") or 0) < 30:
        return f"{ALERT_C}✗{RESET}"
    oc = other_cfg(cfg)
    if oc is None:                     # 只配了一个账号：没得切
        return f"{DIMC}—{RESET}"
    a, b = current_account(cfg), current_account(oc)
    if not a:
        return f"{DIMC}?{RESET}"
    pre = os.path.commonprefix([a, b]) if b else ""
    cut = pre.rfind(".") + 1 if "." in pre else 0
    return f"{USED_C}{a[cut:]}{RESET}"


def git_state(n):
    """git 按钮后面那一小段：这个 Claude 的目录里有几个文件没提交。不是仓库 / 都提交了就空着。"""
    if not n:
        return ""
    return f"{WARN_C}{n if n < 100 else '99+'}{RESET}"


def _fit(text, width):
    """把一段带颜色的状态塞进固定宽度：短了补空格，长了截掉（按显示宽度，中文算两格）。"""
    if not width:
        return ""
    body = " " + text
    w = _cols(body)
    if w <= width:
        return body + " " * (width - w)
    out, used = "", 0
    for ch in ANSI.sub("", body):
        if used + _cols(ch) > width:
            break
        out, used = out + ch, used + _cols(ch)
    return out + " " * (width - used)


def buttons_line(u=None, git_n=None, cfg="", sw=None):
    """表头第 2 行：一排图标按钮，git / 刷新 / 账号后面各跟一小段固定宽度的状态。"""
    pad = (SLOT - 1) // 2
    state = {"git": git_state(git_n), "usage": usage_state(u), "account": account_state(cfg, sw)}
    cells = [" " * pad + icon + " " * (SLOT - 1 - pad) + _fit(state.get(action, ""), extra)
             for icon, action, extra in BUTTONS]
    return (" " * GAP).join(cells)


def button_at(col):
    """表头第 2 行第 col 列（从 1 数）底下的按钮动作；点在按钮之间的空隙上是 None。"""
    x = 1
    for _, action, extra in BUTTONS:
        if x <= col < x + SLOT + extra:
            return action
        x += SLOT + extra + GAP
    return None


def model_of(model, bar=""):
    """模型名。@claude_model 是 ctx_to_tmux.py 新加的纯文本字段；这台机器上已经开着的
    Claude 要等它下一轮才会写上，所以回退到老字段 @claude_bar——去掉 tmux 的 #[...]
    标记、切到柱子那个字形为止，剩下的就是 `Opus 5.5/high`。"""
    if model:
        return model
    plain = re.sub(r"#\[[^\]]*\]", "", bar or "")
    for g in GAUGE_GLYPHS:
        plain = plain.split(g)[0]
    return plain.strip()


def status_lines(width, model="", ctx="", usage=None, bar="", cfg=""):
    """表头第 3 行起（前面是轮数、按钮）。model/ctx 是 ctx_to_tmux.py 写在 Claude 窗格上的
    @claude_model（`Opus 5.5/high`）和 @claude_ctx（`11% (110k)`）；cfg 是同一处写的
    @claude_config，决定额度和账号读谁的。"""
    model = model_of(model, bar)
    # fzf 的表头左边留 2 列（和列表项的 ▌ 对齐），按整格宽排会被挤出右边界截成「…」
    width = max(24, width - 2)
    u = usage if usage is not None else read_usage(cfg)
    # 先把三条尾巴都算出来，再按最长的那条定柱子宽度：三条对齐，谁都不被截
    ctx_tail = _ctx_tail(ctx)
    tails = [t for t in (ctx_tail, _quota_tail((u or {}).get("five_hour")),
                         _quota_tail((u or {}).get("seven_day"))) if t]
    bar_w = _bar_w(width, tails)
    lines = []
    acct = current_account(cfg)
    if model or acct:
        head = model
        if acct and len(f"{model} · {acct}") <= width:   # 窄的时候宁可不显示账号，也别把模型截了
            head = f"{model} · {acct}" if model else acct
        lines.append(f"{GREY}{head}{RESET}")
    line = _ctx_line(model, ctx, bar_w, ctx_tail) if ctx_tail else None
    if line:
        lines.append(line)
    elif ctx and not model:
        lines.append(f"{GREY}{ctx}{RESET}")
    if u:
        lines.append(_quota_line("5h", u.get("five_hour"), bar_w) + _fresh_mark(u))
        lines.append(_quota_line("7d", u.get("seven_day"), bar_w))
    return lines


def _pane_entry(pane):
    try:
        return json.loads(STATUS_FILE.read_text()).get(pane) or {}
    except (OSError, ValueError):
        return {}


def session_path(pane):
    """The transcript of whatever Claude session is running in this tmux pane now."""
    if os.environ.get("CLAUDE_TRAIL_TRANSCRIPT"):  # for testing against a given session
        return Path(os.environ["CLAUDE_TRAIL_TRANSCRIPT"])
    sid = _pane_entry(pane).get("session_id")
    if not sid:
        return None
    hits = sorted(PROJECTS.glob(f"*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def _stops(pane):
    p = session_path(pane)
    return (p, get_session(p)) if p else (None, None)


def _busy(pane, s):
    """Id of the newest stop if Claude is still working on it (or waiting on a permission).

    Its text keeps changing until the turn ends, and every change would reload the list.
    So it stays a placeholder until Claude stops — exactly the moment worth a refresh anyway.
    """
    if s and s["stops"] and _pane_entry(pane).get("status") in ("running", "blocked"):
        return s["stops"][-1]["id"]
    return None


def _when(ts):
    if not ts:
        return ""
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%m/%d %H:%M")


def _oneline(s):
    return " ".join(s.split())


def _first_line(s):
    for line in s.splitlines():
        line = re.sub(r"[#*`>|]", "", line)
        # 下划线只剥 markdown 的强调（_重点_、__粗体__），不剥标识符里的：created_at、my_var 原样显示
        line = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", line).strip(" -")
        if line:
            return line
    return ""


def _rows(pane):
    """Chronological, oldest at the top like the conversation itself."""
    _, s = _stops(pane)
    if not s:
        return ""
    busy = _busy(pane, s)
    out = []
    for st in s["stops"]:
        ask = _oneline(st["prompt"]) if st["kind"] == "human" else f"（{st['prompt']}）"
        if st["id"] == busy:
            reply = "（Claude 还在干活…）"
        else:
            reply = _first_line(st["final"]) if st["final"] else "（没有文字回复）"
        gone = " · 已压缩" if st["compacted"] else ""
        meta = f"{DIM}#{st['n']} {_when(st['ts'])}{gone}{RESET}"
        # Two-line item: what you said, then how Claude's reply starts.
        out.append(f"{st['id']}\t{meta} {ask}\n  {DIM}{reply}{RESET}")
    return "\0".join(out)


def cmd_rows(pane):
    sys.stdout.write(_rows(pane))


def cmd_header(pane):
    p, s = _stops(pane)
    if not s:
        print("这个窗格里没找到 Claude 会话")
        return
    cfg = _tmux("show", "-pqv", "-t", pane, "@claude_config").strip()
    print(f"{len(s['stops'])} 轮")
    print(buttons_line(read_usage(cfg)))
    for ln in status_lines(int(os.environ.get("COLUMNS") or 66),
                           _tmux("show", "-pqv", "-t", pane, "@claude_model").strip(),
                           _tmux("show", "-pqv", "-t", pane, "@claude_ctx").strip(),
                           bar=_tmux("show", "-pqv", "-t", pane, "@claude_bar").strip(),
                           cfg=_tmux("show", "-pqv", "-t", pane, "@claude_config").strip()):
        print(ln)


# 底栏那块额度点一下会去 Anthropic 查一次真实用量；搬进侧栏以后那个入口就没了，
# 所以表头的两行额度也认点击，走同一个脚本（install.sh 装的那条软链）。
USAGE_REFRESH = Path.home() / ".claude" / "hooks" / "usage-refresh.py"


def refresh_usage(pane):
    """去 Anthropic 查一次这个窗格那个账号的真实额度（不是默认那套的）。~2 秒，不等它，
    下一次表头重画就是新数了。"""
    try:
        cfg = _tmux("show", "-pqv", "-t", pane, "@claude_config").strip() or str(DEFAULT_CFG)
        subprocess.Popen([sys.executable, str(USAGE_REFRESH), "--force", "--config", cfg],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def cmd_click(pane, line, col, current=""):
    """fzf action for a click on the header: whichever button sits under that column."""
    # 1 轮数 2 按钮，第 3 行起是 status_lines（模型 / ctx / 5h / 7d，有几行看有没有数据，
    # 不一定齐）：行号对不准哪一行是额度，干脆第 3 行往下点哪都刷新额度，多刷一次没坏处。
    if (line or "").isdigit() and int(line) >= 3:
        refresh_usage(pane)
        return
    if line != "2":
        return
    action = button_at(int(col or 0))
    if action == "usage":
        refresh_usage(pane)
    elif action == "close":
        # 你自己关的：记在 Claude 窗格上，常驻进程就不会再自动把它打开
        _tmux("set", "-p", "-t", pane, "@trail_off", "1")
        print("abort")
    elif action == "bottom":
        cmd_bottom(pane)
        print("last")   # 侧栏也跟着落到最后一轮，和主界面对上
    elif action in TOOLS:
        _open_tool(pane, action)
    elif action == "account":
        if switch_status(pane).get("state") == "running":
            return                                  # 正在切，再点一次不叠
        # 后台跑：要退出、重启、等新进程起来，最长（带队友先收队）十几分钟
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "switch", pane],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def cmd_switch(pane):
    """把这个 Claude 换到另一个账号（后台进程里跑）。进度写在 SWITCH_DIR/<pane>.switch，常驻进程
    据此在按钮后面显示「…」，并且切换期间 Claude 退出、重启那几秒不把侧栏当成没人了关掉。"""
    f = SWITCH_DIR / f"{pane}.switch"
    SWITCH_DIR.mkdir(parents=True, exist_ok=True)

    def put(state, msg):
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": state, "msg": msg, "at": time.time()}, ensure_ascii=False))
        tmp.replace(f)

    put("running", "准备切换")
    try:
        if not SWITCHER.exists():
            raise RuntimeError(f"没找到 {SWITCHER}")
        sa = _switcher()
        target = other_cfg(_tmux("show", "-pqv", "-t", pane, "@claude_config").strip())
        if target is None:
            raise RuntimeError(f"只配了一个账号，在 {sa.ACCOUNTS_FILE} 里加上另一个")
        to = next(k for k, (_, d) in sa.ACCTS.items() if Path(d) == (Path(target) if target else DEFAULT_CFG))
        r = sa.switch_one(pane, to, log=lambda m: put("running", m))
    except Exception as e:                      # 出什么错都要把「…」收掉，别一直挂着
        r = {"state": "fail", "msg": f"{type(e).__name__}: {e}"}
    put(r.get("state", "fail"), r.get("msg", ""))
    _tmux("display-message", "-t", pane, f"切账号：{r.get('msg', '')}")


def _open_tool(pane, action):
    """git / 文件：交给 float-tool.sh，传的是 Claude 窗格（不是侧栏自己）。
    放后台跑：fzf 的 transform 要等这个进程退出，弹窗慢一点侧栏就会卡住。"""
    if not FLOAT_TOOL.exists():
        _tmux("display-message", "-t", pane, f"侧栏：没找到 {FLOAT_TOOL}")
        return
    subprocess.Popen([str(FLOAT_TOOL), pane, action], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _cols(text):
    """Display width, CJK counting double — what fzf uses to decide where a line wraps."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in ANSI.sub("", text))



JUMP_FILE = Path.home() / ".local" / "state" / "claude-trail" / "jump"
JUMP_BUDGET = 6.0  # 秒：一次跳转最多翻这么久
MAX_ROLLS = 400  # screenfuls before giving up: the turn may be gone after /compact or /clear


def _tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


_DROP = re.compile(r"[\s*`_~#>|\\]")


def _norm(text):
    """屏幕上的一行 / 针，两边都过一遍：去掉空白和 markdown 记号字符再比。

    以前只在针这边剥记号，屏幕那边原样：回复开头是「~/work/…」「my_var」时，针里的 ~ _
    被剥掉了，屏幕上却还在，永远对不上，于是点哪一轮都提示「找不到那一轮」（2026-10-13 用户反馈）。
    """
    return _DROP.sub("", text)


def _needle(st):
    """A short run of plain text from the start of the final reply, normalised (see _norm).

    A long needle may wrap onto the next row, so take the first few words of the first line.
    """
    for line in (st["final"] or "").splitlines():     # 没有最终回复的轮次没有针：屏幕上只有 ❯ 开头的提问行
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)        # [text](url) -> text
        line = _norm(line)
        if line:
            return line[:12]
    return ""


MISSING_DIR = JUMP_FILE.parent / "missing"


def missing_ids(pane):
    """这个窗格的主界面里已经翻不到的那些轮（续接的会话 Claude 只显示最近一段、/compact 清掉的）。"""
    try:
        return set(json.loads((MISSING_DIR / f"{pane}.json").read_text()))
    except (OSError, ValueError):
        return set()


def _save_missing(pane, ids):
    MISSING_DIR.mkdir(parents=True, exist_ok=True)
    (MISSING_DIR / f"{pane}.json").write_text(json.dumps(sorted(ids)))


def needles_of(stops):
    """每轮一根针；整个会话里不唯一的置空（认不了位置），见 cmd_jump 里的说明。"""
    needles = [_needle(st) for st in stops]
    dup = {n for n in needles if needles.count(n) > 1}
    return ["" if n in dup else n for n in needles]


def locate(screen_text, needles):
    """屏幕上现在有哪几轮：返回认出来的轮次下标（升序）。你自己的话（❯ 开头）不算。"""
    rows = [_norm(r) for r in screen_text.splitlines() if not r.lstrip().startswith("❯")]
    return [i for i, n in enumerate(needles) if n and any(n in r for r in rows)]


def cmd_jump(pane, stop_id):
    """Scroll Claude's own screen so that this turn's final reply sits near the top.

    Claude Code has no "go to turn" command, so do what you would do by hand: start from
    whichever end is closer (Ctrl+Home / Ctrl+End) and page towards the turn (PageUp/PageDown:
    exactly half a viewport, unlike the wheel, which accelerates when rolled fast), reading the
    screen after each move, until the reply's first words show up; then nudge it to the top.
    Your own messages (rows starting with "❯", including the sticky one at the top) never count,
    since you often quote Claude.

    翻多远不是一屏一屏试出来的：每看一屏都认一下现在停在第几轮（`where`），用刚走过的
    那一段算出这个会话里「一轮大概几个半屏」，再照着把剩下的路一次翻掉。翻过头就掉一次
    头、锁死方向，改成一屏一屏挪。
    实测（假的主界面 + 368 轮的真实会话记录，12 个目标 × 3 种起点）：一次跳转的中位耗时
    从 2.34 秒降到 0.91 秒、最慢从 4.97 秒降到 2.65 秒，36 次都跳到了。
    """
    import time
    if stop_id in ("fold", "unfold"):  # 侧栏顶上「已压缩」那一行：只在侧栏里展开/收起，主界面不动
        return
    _, s = _stops(pane)
    stops = (s or {}).get("stops", [])
    idx = next((i for i, x in enumerate(stops) if x["id"] == stop_id), None)
    # Claude 还在干的那一轮没有最终回复：拿它半截的文字去找，屏幕又一直在滚新内容，永远对不上，
    # 跳转会翻几百屏、几十秒都不停，期间你往上滚一下就被它拽回去（2026-10-02 用户原话：
    # 「我往上滚，它滚不了，它会一直 reset 到最底部」）。最新的东西本来就在最底下，直接到底。
    if idx is not None and stop_id == _busy(pane, s):
        cmd_bottom(pane)
        return
    needle = _needle(stops[idx]) if idx is not None else ""
    if not needle:
        if idx is not None:
            _tmux("display-message", "-t", pane, "轨迹：这一轮 Claude 没写文字回复，没法定位")
        return
    if stop_id in missing_ids(pane):
        _tmux("display-message", "-t", pane, "轨迹：这一轮在主界面里已经看不到了（续接的会话只显示最近一段，或被 /compact 清掉）")
        return
    # A newer click wins: each jump checks this token and stops once it changes.
    token = str(os.getpid())
    JUMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    JUMP_FILE.write_text(token)
    # 认位置用的针：只留在整个会话里唯一的那些。16 个字太短，"好的，我来看一下" 这种开头
    # 好几轮都一样，拿它认位置会把差着一百多轮的两块屏幕认成同一处，跳转就在两屏之间来回弹。
    needles = needles_of(stops)
    height = int(_tmux("display", "-p", "-t", pane, "#{pane_height}") or 40)
    # Two half-pages per look while that still keeps a reply in view between looks.
    pages = 2 if height >= 30 else 1
    from_top = idx < len(stops) / 2

    def screen():
        return _tmux("capture-pane", "-p", "-t", pane)

    def needle_row(text):
        for i, row in enumerate(text.splitlines()):
            if needle in _norm(row) and not row.lstrip().startswith("❯"):
                return i
        return None

    def press(before, *keys):
        """Send keys, wait for the screen to settle, return it. None if nothing moved (an end).

        All the keys go out in ONE tmux invocation — spawning tmux costs ~8ms and a jump does
        hundreds of these, so the round trips, not Claude's redraw, were most of the wait.

        ⚠️ 等的是「不再变」，不是「变了」。一次发几十上百个翻页时，Claude 会先画一次、
        再接着消化剩下的按键；看到第一次变化就返回，拿到的是半路的画面，随后按那个画面
        算出来的行号全是错的——抬升那一步会把目标滚出屏幕，整次跳转就落空了。
        """
        args = []
        for k in keys:
            args += ["send-keys", "-t", pane, *k, ";"]
        _tmux(*args[:-1])
        time.sleep(REDRAW_WAIT)
        now, last = screen(), None
        for _ in range(60):
            if now != before and now == last:
                return now
            last = now
            time.sleep(0.01)
            now = screen()
        return None if now == before else now

    def where(text):
        """Which turn the screen is showing: the last one whose opening words are on it.

        Every turn has a needle, so a screenful almost always carries one. Knowing where we
        are turns the search from "one screen at a time" into "measure how many half-screens
        a turn costs here, then page the rest of the way in one go".
        """
        rows = [_norm(row) for row in text.splitlines() if not row.lstrip().startswith("❯")]
        seen = None
        for i, n in enumerate(needles):
            if n and any(n in nrows for nrows in rows):
                seen = i
        return seen

    def lift(now):
        """Nudge the found turn up near the top — measure, scroll a little, measure again.

        滚轮每格滚几行不固定（连着滚会加速，第一格有时还不动），一次发十几格必然滚过头，
        目标就跑出屏幕外或钻进顶上那条粘住的「❯」提问行底下，看起来就是「找不到」
        （2026-10-13 实测：目标在第 19 行，发 17 格滚轮后屏幕上再也认不出它）。
        所以一次最多两格、每次重新数；一旦认不出就放弃抬升——目标本来就在屏幕上，
        后面 search 会再找一遍，不抬了。
        """
        down = f"\033[<65;10;{height // 2}M"
        for _ in range(10):
            r = needle_row(now)
            if r is None or r <= 4:
                return
            if r - 4 >= height // 2:           # 还差半屏以上，先翻页
                now = press(now, ["PageDown"])
            else:
                now = press(now, ["-l"] + [down] * min(2, r - 4))
            if now is None:
                return

    def search(nudge=True):
        now = press(screen(), ["C-Home" if from_top else "C-End"]) or screen()
        last_turn, since = None, 0        # 上次认出停在哪一轮；从那以后翻了几个半屏
        prev_down, fine = from_top, False  # 翻过头一次之后就改成一屏一屏地找
        for _ in range(MAX_ROLLS):
            r = needle_row(now)
            if r is not None:
                if nudge:
                    lift(now)
                return True
            if JUMP_FILE.read_text() != token:
                return True
            if time.monotonic() > deadline:
                return None                   # 找太久了：停手，别再动主界面
            # 这一步翻多远：认出现在是第几轮，就能用刚走过的那一段算出「一轮几个半屏」，
            # 按它把剩下的路一次翻掉（只走 60%，留出余量，宁可多来一轮也别翻过头）。
            # 认不出来（屏幕上一句熟面孔都没有）就退回老办法：一次一屏。
            # 认出来的轮次在目标后面，说明翻过了，下一步自动掉头。
            # 这一步翻多远、往哪翻。认出现在停在第几轮，就能用刚走过的那一段算出
            # 「一轮几个半屏」，按它把剩下的路一次翻掉（只走 60%，宁可多来一轮也别翻过头）。
            #
            # ⚠️ 方向不能一直听 where 的。where 偶尔会认错，一认错方向就反，反完又反，
            # 在相邻两屏之间来回弹到 MAX_ROLLS 为止（实测：一次 38 秒，最后还是没找到）。
            # 所以掉头只准一次——那一次是「确实翻过头了」——之后方向锁死，一次两个半屏
            # 老老实实挪过去：两个半屏正好首尾相接，不会漏掉任何一行。
            cur = where(now)
            steps, down = pages, prev_down
            if cur is not None and not fine:
                down = cur < idx
                if down != prev_down:
                    fine = True
                elif last_turn is not None and cur != last_turn and since:
                    rate = since / abs(cur - last_turn)          # 半屏 / 轮
                    steps = int(rate * abs(idx - cur) * 0.6)
                    steps = max(pages, min(steps, MAX_ROLLS))
                last_turn, since = cur, 0
            prev_down = down
            # 一次 send-keys 带上全部按键。`-N` 在这里没用：那是给 `-X` 的复制模式命令
            # 计重复次数的，普通按键不认（实测 `-N 10 PageDown` 和一次 PageDown 走得一样远）。
            now = press(now, ["PageDown" if down else "PageUp"] * steps)
            since += steps
            if now is None:
                return False
        return False

    # 正常一次跳转中位 0.9 秒、最慢 2.65 秒。超过这个预算还在翻，多半是找不到了；再翻下去
    # 就是在跟你的滚轮抢屏幕，所以到点就停在原处，也不再拽回最底下。
    deadline = time.monotonic() + JUMP_BUDGET
    # Check the result once more: a redraw read half-done can send the search past it.
    for attempt in range(2):
        found = search(nudge=attempt == 0)
        if found is None:
            _tmux("display-message", "-t", pane, "轨迹：这一轮没找着，先停在这儿了")
            return
        if found and (JUMP_FILE.read_text() != token or needle_row(screen()) is not None):
            if stop_id in missing_ids(pane):
                _save_missing(pane, missing_ids(pane) - {stop_id})
            return
    # 翻到头也没有：看一眼主界面最上面是哪一轮，比它更早的都翻不到，一起记下来
    # （侧栏灰显、再点立刻提示，不用再白翻几秒）。认不出就只记这一轮。
    _tmux("send-keys", "-t", pane, "C-Home")
    time.sleep(0.4)
    top = locate(screen(), needles)
    gone = [x["id"] for x in stops[:top[0]]] if top and top[0] > idx else [stop_id]
    _save_missing(pane, missing_ids(pane) | set(gone))
    _tmux("send-keys", "-t", pane, "C-End")
    _tmux("display-message", "-t", pane, "轨迹：主界面里找不到这一轮（续接的会话只显示最近一段，或被 /compact 清掉）")


def cmd_bottom(pane):
    JUMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    JUMP_FILE.write_text("bottom")  # cancel a jump still scrolling
    _tmux("send-keys", "-t", pane, "C-End")


def cmd_sig(pane):
    """Changes exactly when the sidebar would look different, so it reloads only then."""
    import hashlib
    print(hashlib.sha1(_rows(pane).encode()).hexdigest() if session_path(pane) else "")


def cmd_has(pane):
    """有没有找到这个窗格的 Claude 会话：只看文件在不在，不解析（开侧栏前用，要快）。"""
    sys.exit(0 if session_path(pane) else 1)


COMMANDS = {"rows": cmd_rows, "header": cmd_header, "sig": cmd_sig, "has": cmd_has,
            "click": cmd_click, "jump": cmd_jump, "bottom": cmd_bottom, "switch": cmd_switch}


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}} <tmux-pane> [stop-id]")
    COMMANDS[sys.argv[1]](*sys.argv[2:])


if __name__ == "__main__":
    main()
