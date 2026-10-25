#!/usr/bin/env python3
"""claude-trail 的 herdr 版：停在 Claude 窗格右边的轨迹侧栏（第一阶段：可行性原型）。

    trail.py toggle   插件动作：焦点所在的 Claude 还没有轨迹就开一个，有了就在宽版 / 窄版之间切换
    trail.py auto     事件钩子：tab 里有 Claude 时自动打开
    trail.py pane     侧栏窗格本体（herdr 按 manifest 的 [[panes]] 起它）：这个进程 + 一个 fzf

和 tmux 版的分工不一样。tmux 版是一个常驻进程每秒 `tmux list-panes` 轮询所有窗格；这里每个侧栏
自己带一个小进程，订阅 herdr 的事件（Claude 状态变了、窗格关了），没事件时阻塞在 select 上。
会话 id 直接问 herdr（`pane.get` 的 agent_session），不要 tmux 版的钩子和状态文件。

解析对话记录、列表和表头的额度柱子直接 import tmux 版 sidebar/ 下的代码，一行不改它；
跳转的算法照搬 claude_trail.cmd_jump，只是读屏 / 发键换成 herdr 的 socket（一次 0.3ms，tmux 一次 ~8ms）。
"""
import http.client
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "sidebar"))
sys.path.insert(0, str(REPO / "hooks"))
import claude_trail as ct  # noqa: E402
import trail_daemon as td  # noqa: E402  只用它的 Transcript / rows_of（增量解析、两行一项的列表）
import ctx_to_tmux as cx   # noqa: E402  只用它的 last_usage / short_model（模型和上下文大小）
from i18n import t as _t   # noqa: E402

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID") or "claude-trail"
SOCK = os.environ.get("HERDR_SOCKET_PATH") or str(Path.home() / ".config" / "herdr" / "herdr.sock")
STATE = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR")
             or Path.home() / ".local" / "state" / "herdr" / "plugins" / PLUGIN_ID)
RUN = STATE / "run"                # <侧栏窗格>.json / .fifo / .sock / .rows
JUMP_LOG = STATE / "jump.log"      # 每次跳转一行：用了多久、找到没有（验收要的数字）
EVENT_LOG = STATE / "events.log"   # 自动打开收到的事件（文档没写 HERDR_PLUGIN_EVENT_JSON 的结构，先记下来）
MIN_WIDTH = 120                    # Claude 那一格至少这么宽才自动开，和 tmux 版一样
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
                  or Path.home() / ".config" / "herdr" / "plugins" / "config" / PLUGIN_ID)
# 配置（<配置目录>/config.toml，`herdr plugin config-dir claude-trail` 打印这个目录）。文件不存在就全用默认值。
# 这一阶段只开放确有需要的几项：按钮列表、自动打开、默认宽版还是窄版、宽版宽度、图标字体。
DEFAULTS = {
    "auto_open": True,             # 切到有 Claude 的 tab 时自动打开
    "default_mode": "wide",        # 打开时多宽：宽版（"wide"，width 那么宽）还是窄版（"narrow"，herdr 允许的最窄）
    "width": 0.34,                 # 宽版占 Claude 那一格的比例，和 tmux 版的 -l 34% 一样
    # 图标："nerd" 用 Nerd Font 字形（MesloLGS NF 之类自带）；别人没装 Nerd Font 时用 "unicode" 普通符号，
    # 或 "text" 全部写成字
    "icons": "nerd",
    # 宽版表头第 1 行、窄版底部的按钮：在 Claude 那一格的目录里，用本插件的浮窗跑 command（经 sh -c）。
    # icon 可选：写 ICONS 里的名字（"git"、"files"…）就按 icons 那一档取字形，也可以直接写一个字形
    "buttons": [{"label": "Git", "command": "lazygit", "icon": "git"},
                {"label": "文件", "command": "yazi", "icon": "files"}],
}


def config():
    import tomllib
    try:
        with open(CONFIG_DIR / "config.toml", "rb") as f:
            user = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        user = {}
    cfg = {**DEFAULTS, **{k: v for k, v in user.items() if k in DEFAULTS}}
    cfg["buttons"] = [b for b in cfg["buttons"] if isinstance(b, dict) and b.get("label") and b.get("command")]
    try:
        cfg["width"] = min(0.9, max(0.1, float(cfg["width"])))
    except (TypeError, ValueError):
        cfg["width"] = DEFAULTS["width"]
    for key, allowed in (("default_mode", ("wide", "narrow")), ("icons", ("nerd", "unicode", "text"))):
        if cfg[key] not in allowed:
            cfg[key] = DEFAULTS[key]
    return cfg


CFG = config()
WIDTH = CFG["width"]
TICK = 60                          # 没有事件时多久醒一次：额度的倒计时按分钟变
WIDE_MIN = 30                      # 轨迹窗格（tty）至少这么宽才画宽版，窄了画窄版（拖分隔线时跟着变）
FRAME = 2                          # herdr 给窗格画的边框：tty 比 pane.layout 报的矩形窄 2 列、矮 2 行（实测 51 → 49）
GONE_GRACE = 15                    # Claude 退出后等多少秒才关侧栏：切账号时 Claude 会退出再重启
LATER = (0.5, 1.5)                 # Claude 状态变了之后再补刷几次：Stop 时最后那条回复还没写进对话记录
VERSION = re.compile(r"^\d+\.\d+\.\d+$")   # Claude Code 的进程名是版本号
AT_BOTTOM = "Jump to bottom"       # Claude 往上翻了才在底部显示这句；没有就是在最底


# ---- herdr socket --------------------------------------------------------------

class HerdrError(Exception):
    pass


def call(method, **params):
    """一次请求一条连接：newline-delimited JSON。比起 `herdr` 命令行省掉每次起进程。"""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5)
        s.connect(SOCK)
        s.sendall((json.dumps({"id": method, "method": method, "params": params}) + "\n").encode())
        line = s.makefile("rb").readline()
    r = json.loads(line)
    if "error" in r:
        raise HerdrError(r["error"].get("code"), r["error"].get("message"))
    return r["result"]


def pane_info(pane):
    try:
        return call("pane.get", pane_id=pane)["pane"]
    except (OSError, ValueError, HerdrError):
        return None


def session_id(info):
    if not info or info.get("agent") != "claude":
        return None
    return (info.get("agent_session") or {}).get("value")


def notify(title, body=None):
    try:
        call("notification.show", title=title, body=body, sound="none")
    except (OSError, ValueError, HerdrError):
        pass


# ---- 哪个账号、哪份对话记录 ------------------------------------------------------

def config_dirs():
    """可能的 CLAUDE_CONFIG_DIR：默认的 ~/.claude，加上 ~/.claude-*（一个账号一个目录，比如 ~/.claude-l2）。
    CLAUDE_TRAIL_CONFIG_DIRS（冒号分隔）可以改。"""
    env = os.environ.get("CLAUDE_TRAIL_CONFIG_DIRS")
    if env:
        return [Path(os.path.expanduser(d)) for d in env.split(":") if d]
    extra = sorted(d for d in Path.home().glob(".claude-*") if (d / "projects").is_dir())
    return [ct.DEFAULT_CFG, *extra]


def claude_pid(pane):
    try:
        procs = call("pane.process_info", pane_id=pane)["process_info"]["foreground_processes"]
    except (OSError, ValueError, KeyError, HerdrError):
        return None
    for p in procs:
        if p.get("argv0") == "claude" or VERSION.match(p.get("name") or ""):
            return p.get("pid")
    return None


def config_dir(pane):
    """这个窗格里的 Claude 用的是哪个配置目录（决定账号和额度读谁的）。

    tmux 版靠钩子把 CLAUDE_CONFIG_DIR 写到窗格选项上；这里不装钩子：Claude Code 自己会给每个
    进程写 <配置目录>/sessions/<pid>.json，看这个文件落在哪个目录就知道了。"""
    pid = claude_pid(pane)
    if pid:
        for d in config_dirs():
            if (d / "sessions" / f"{pid}.json").exists():
                return d
    return ct.DEFAULT_CFG


def transcript_path(sid, cfg_dir):
    """先在它自己的配置目录里找，再找别的（两个目录的 projects 可能是同一个软链）。"""
    for d in [cfg_dir, *(x for x in config_dirs() if x != cfg_dir)]:
        hits = sorted((d / "projects").glob(f"*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime)
        if hits:
            return hits[-1]
    return None


def cfg_key(cfg_dir):
    """claude_trail 里配置目录的写法：默认那套是空串。"""
    return "" if Path(cfg_dir) == ct.DEFAULT_CFG else str(cfg_dir)


def model_ctx(path):
    """(`Opus 5.5/high`, `11% (111k)`)：tmux 版由 ctx_to_tmux.py 写到窗格选项上，这里直接从对话记录读。"""
    tokens, mid, effort = cx.last_usage(str(path))
    name = cx.short_model(mid)
    if name and effort:
        name += "/" + effort
    if tokens is None:
        return name, ""
    window = 200_000 if mid and "haiku" in mid else 1_000_000
    return name, f"{tokens * 100 // window}% ({tokens // 1000}k)"


# ---- 表头 ------------------------------------------------------------------------

# 图标：宽版、窄版里能用图标说清楚的都用图标（Nerd Font 的字形在私用区，终端按 1 格宽画）。
# 别人不一定装了 Nerd Font，所以配置 icons 可以换成普通 Unicode 符号，或者全写成字。
ICONS = {
    #          nerd       unicode  text
    "narrow":  ("\uf101", "»",     ">>"),       # 宽版左上角：切到窄版
    "wide":    ("\uf100", "«",     "<<"),       # 窄版左上角：切到宽版
    "bottom":  ("\uf103", "⇊",     "End"),      # 主界面到最底
    "usage":   ("\uf021", "⟳",     "Ref"),      # 马上查一次额度
    "working": ("\uf110", "◐",     "工作中"),
    "blocked": ("\uf059", "◆",     "等你"),
    "done":    ("\uf00c", "✓",     "完成"),
    "unknown": ("\uf10c", "○",     "—"),
    "model":   ("\uf0e7", "✦",     ""),         # 模型前面的标记
    "account": ("\uf007", "@",     ""),         # 账号前面的标记
    "ctx":     ("\uf2db", "◧",     "ctx"),
    "5h":      ("\uf017", "◷",     "5h"),
    "7d":      ("\uf073", "▦",     "7d"),
    "git":     ("\ue702", "±",     ""),         # 文字档不另加图标，按钮本身就有字
    "files":   ("\uf07c", "▤",     ""),
}
_TIER = ("nerd", "unicode", "text").index(CFG["icons"])


def icon(name):
    return ICONS[name][_TIER]


def button_icon(b):
    """配置里的按钮：icon 是 ICONS 里的名字就按档位查表，否则当成字形原样用（文字档都不用）。"""
    ic = str(b.get("icon") or "")
    if CFG["icons"] == "text" or not ic:
        return ""
    return icon(ic) if ic in ICONS else ic


STATUS = {"working": ("working", "WARN_C"), "blocked": ("blocked", "ALERT_C"),
          "done": ("done", "DIMC"), "idle": ("done", "DIMC")}


def tool_buttons(with_label=True):
    """[(文字, 动作)]：宽版图标加文字，窄版只要图标（没有图标的按钮还是写字）。"""
    out = []
    for i, b in enumerate(CFG["buttons"]):
        ic = button_icon(b)
        text = f"{ic} {b['label']}" if ic and with_label else (ic or b["label"])
        out.append((text, f"tool:{i}"))
    return out


# 宽版表头第 1 行的按钮，格子的宽度、点偏一格也算的规矩和 tmux 版一样（tmux 版按钮在第 2 行）。
# 最左上角是「切到窄版」（»），窄版的「切回宽版」（«）也在同一个位置（第 1 行第 4 列）。没有「关闭」：
# 关掉之后鼠标就没有地方再把它点出来了（herdr 的右键菜单是写死的，插件动作进不去，
# 见 herdr v0.9.5 src/client/shell/context_menu.rs:59-78）。
# 配置里的按钮（默认 Git = lazygit、文件 = yazi）跟在后面：tmux 版的 git / 文件走 float-tool.sh（tmux 专用），
# 这里是本插件自己的浮窗，不依赖别的 herdr 插件。切账号（tmux 版的 ⇄）第一阶段不做。
def wide_buttons():
    return [(icon("narrow"), "narrow", 0), (icon("bottom"), "bottom", 0),
            *((t, a, 0) for t, a in tool_buttons()), (icon("usage"), "usage", 10)]


def _cell_w(text):
    """图标按钮占 3 格（空格 + 图标 + 空格）；带字的按钮按字宽，两边各留一格。"""
    return max(ct.SLOT, ct._cols(text) + 2)


def buttons_line(buttons, u=None):
    cells = []
    for text, act, extra in buttons:
        w = _cell_w(text)
        pad = (w - ct._cols(text)) // 2
        cells.append(" " * pad + text + " " * (w - ct._cols(text) - pad)
                     + ct._fit(ct.usage_state(u) if act == "usage" else "", extra))
    return (" " * ct.GAP).join(cells)


def button_at(buttons, col):
    x = 1
    for text, act, extra in buttons:
        w = _cell_w(text) + extra
        if x <= col < x + w:
            return act
        x += w + ct.GAP
    return None


def narrow_footer():
    return [(t, a, 0) for t, a in tool_buttons(with_label=False)]


def short_model(model):
    """`Opus 5.5/high` → `Opus5.5/hi`：窄版一行只有十来列。"""
    name, _, effort = (model or "").partition("/")
    return name.replace(" ", "") + (f"/{effort[:2]}" if effort else "")


def short_account(cfg_dir):
    """账号缩写：和别的账号比，只留不一样的那截（you.a / you.b → a / b），规矩和 tmux 版的账号按钮一样。"""
    me = ct.current_account(cfg_key(cfg_dir))
    others = [ct.current_account(cfg_key(d)) for d in config_dirs() if d != Path(cfg_dir)]
    others = [o for o in others if o and o != me]
    if not me or not others:
        return me
    pre = os.path.commonprefix([me, *others])
    return me[pre.rfind(".") + 1:] if "." in pre else me


def _clip(text, width):
    """按显示宽度截（中文算两格），保住颜色收尾。"""
    plain = ct.ANSI.sub("", text)
    if ct._cols(plain) <= width:
        return text
    out, w = "", 0
    for ch in plain:
        if w + ct._cols(ch) > width:
            break
        out, w = out + ch, w + ct._cols(ch)
    return out


def quota_pct(u, label, key):
    """(百分比, 颜色)：颜色和表头柱子同一套规则（claude_trail.quota_colour，按超前匀速线多少）。"""
    w = (u or {}).get(key) or {}
    pct = w.get("utilization")
    if pct is None:
        return None
    frac, mark = float(pct) / 100, None
    try:
        left = (datetime.fromisoformat(w.get("resets_at") or "") - datetime.now().astimezone()).total_seconds()
        mark = 1.0 - max(0.0, min(1.0, left / ct.WINDOW_SECS[label]))
    except (TypeError, ValueError):
        pass
    return float(pct), ct.quota_colour(frac, mark)


def ctx_pct(ctx):
    """(百分比, 颜色)：和 claude_trail._ctx_line 一样，按离自动压缩那条线多远：过八成橙、过线红。"""
    m = ct.CTX_RE.search(ctx or "")
    if not m:
        return None
    tokens, line = int(m.group(2)) * 1000, ct._auto_compact_at() or ct.CTX_HARD
    return int(m.group(1)), ct.ALERT_C if tokens >= line else ct.WARN_C if tokens >= line * 0.8 else ct.USED_C


def fzf(sock, action):
    """往 fzf 的 --listen（Unix socket）发一个动作。"""
    class Conn(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.settimeout(2)
            self.sock.connect(str(sock))
    try:
        c = Conn("localhost", timeout=2)
        c.request("POST", "/", body=action.encode())
        c.getresponse().read()
        c.close()
        return True
    except OSError:
        return False


def _q(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


# ---- 侧栏本体 --------------------------------------------------------------------

class Sidebar:
    def __init__(self, target, me):
        self.target, self.me = target, me
        RUN.mkdir(parents=True, exist_ok=True)
        base = RUN / me.replace(":", "_")
        self.reg, self.fifo = base.with_suffix(".json"), base.with_suffix(".fifo")
        self.sock, self.rows_file = base.with_suffix(".sock"), base.with_suffix(".rows")
        self.sid = self.path = self.tr = None
        self.cfg_dir, self.status = ct.DEFAULT_CFG, None
        self.opened, self.expanded = None, False
        self.missing = set()
        self.pushed = (None, None)            # 上次推给 fzf 的 (列表, 表头)
        self.flash = None                     # (一句提示, 到什么时候为止)：tmux 版的 display-message
        self.mc = (None, ("", ""))            # (对话记录大小, model_ctx)：没变就不重读尾巴
        self.due = []                         # 预约的补刷时间
        self.usage_kick = 0.0
        self.jump_token = 0
        self.proc, self.wide = None, None      # self.wide：宽版上次的列数，从窄版切回去时还原
        self.mode = None                      # "wide" / "narrow"：按窗格实际宽度定（mode_for），不是按钮切的
        self.no_claude_since = None           # 这一格里从什么时候起不是 Claude 了
        self.wake_r, self.wake_w = os.pipe()  # 跳转线程 / 信号 叫醒主循环
        os.set_blocking(self.wake_w, False)

    # -- 状态 --

    def refresh(self):
        """问 herdr 这个 Claude 现在什么状态、跑的哪个会话，读对话记录新追加的部分，推给 fzf。
        返回 False = Claude 那一格已经没了。"""
        info = pane_info(self.target)
        if info is None:
            return False
        self.status = info.get("agent_status")
        if info.get("agent") == "claude":
            self.no_claude_since = None
        elif self.no_claude_since is None:    # Claude 退出了（切账号重启那几秒也会这样，所以等一会儿再关）
            self.no_claude_since = time.monotonic()
            self.later(GONE_GRACE + 0.5)
        sid = session_id(info)
        if sid != self.sid:                   # /clear、/resume 换了会话
            self.sid, self.path, self.tr = sid, None, None
            self.opened, self.expanded, self.mc = None, False, (None, ("", ""))
            self.missing = set()
            if sid:
                self.cfg_dir = config_dir(self.target)
        if self.sid and self.path is None:    # 新会话第一条消息之前还没有文件，下次再找
            self.path = transcript_path(self.sid, self.cfg_dir)
            if self.path:
                self.tr = td.Transcript(self.path)
        if self.tr:
            self.tr.update()
        return True

    def busy(self):
        return self.status in ("working", "blocked")

    def kick_usage(self, force=False):
        """额度旧了（默认 30 分钟）就让 usage-refresh.py 去查一次，判据和 trail_daemon.read_usage 一样；
        它自己还会再节流一道（在飞的请求也算），这里多叫一次不会多发请求。"""
        if not ct.USAGE_REFRESH.exists():
            return
        u = ct.read_usage(cfg_key(self.cfg_dir)) or {}
        interval = 60 * int(os.environ.get("CLAUDE_TMUX_USAGE_REFRESH_MIN") or 30)
        if not force and time.time() - u.get("fetched_at_ms", 0) / 1000 <= interval:
            return
        if not force and time.monotonic() - self.usage_kick < 60:
            return
        self.usage_kick = time.monotonic()
        cmd = [sys.executable, str(ct.USAGE_REFRESH), "--config", str(self.cfg_dir)]
        if force:
            cmd.insert(2, "--force")
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
        self.later(3, 6)                      # 查一次两三秒，查完了表头跟上

    def later(self, *secs):
        now = time.monotonic()
        self.due = sorted(self.due + [now + s for s in secs])

    # -- 渲染 --

    def width(self):
        try:
            return os.get_terminal_size(sys.stdout.fileno()).columns
        except OSError:
            return 60

    def model_ctx(self):
        if self.path:
            try:
                size = self.path.stat().st_size
            except OSError:
                size = None
            if size != self.mc[0]:
                self.mc = (size, model_ctx(self.path))
        return self.mc[1]

    def header(self, width):
        return self.narrow_header(width) if self.mode == "narrow" else self.wide_header(width)

    def narrow_header(self, width):
        """窄版表头（自上而下）：切回宽版的 « / 状态 / 模型 / 账号 / ctx、5h、7d 百分比，下面那条边框就是分隔线。
        平时灰，快到上限才上色，规则和宽版的柱子一样。放不下的按 账号 → 模型 → 7d 的顺序丢。
        on_command 按行号认：第 1 行切换，带百分比的那几行点了查一次额度。"""
        room = max(4, width - 2)                       # fzf 表头左边留 2 列
        st, colour = STATUS.get(self.status, ("unknown", "DIMC"))
        flash = f" {ct.WARN_C}!{ct.RESET}" if self.flash and time.monotonic() < self.flash[1] else ""
        lines = [f" {ct.BOLD}{icon('wide')}{ct.RESET}{flash}",    # « 在第 4 列，和宽版的 » 同一个位置（fzf 表头左边留 2 列）
                 f"{getattr(ct, colour)}{icon(st)}{ct.RESET}"]
        model, ctx = self.model_ctx()
        acct = short_account(self.cfg_dir)
        u = ct.read_usage(cfg_key(self.cfg_dir))
        fields = []                                    # (优先级：数字小的先丢, 行)
        def tagged(name, text):                        # 文字档没有标记，前面就别留空格
            return f"{ct.GREY}{icon(name) + ' ' if icon(name) else ''}{text}{ct.RESET}"
        if model:
            fields.append((2, tagged("model", short_model(model))))
        if acct:
            fields.append((1, tagged("account", acct)))
        lab_w = max(ct._cols(icon(x)) for x in ("ctx", "5h", "7d"))   # 文字档 ctx 比 5h 宽一格，对齐百分比
        c = ctx_pct(ctx)
        if c:
            fields.append((9, f"{ct.GREY}{icon('ctx').ljust(lab_w)}{ct.RESET} {c[1]}{c[0]:3d}%{ct.RESET}"))
        for label, key, prio in (("5h", "five_hour", 9), ("7d", "seven_day", 3)):
            q = quota_pct(u, label, key)
            if q:
                fields.append((prio, f"{ct.GREY}{icon(label).ljust(lab_w)}{ct.RESET} {q[1]}{q[0]:3.0f}%{ct.RESET}"))
        # 宽度不够整行放下的字段，按优先级丢（账号 → 模型 → 7d），剩下的照原顺序排
        for prio in (1, 2, 3):
            fields = [(p, ln) for p, ln in fields if p != prio or ct._cols(ct.ANSI.sub("", ln)) <= room]
        lines += [ln for _, ln in fields]
        return "\n".join(_clip(ln, room) for ln in lines)

    def narrow_rows(self, width):
        """窄版列表：每轮一行，轮号加你那句话的前几个字（`#41 那我们`），不放 Claude 回复的开头。"""
        stops = self.tr.visible()
        room = max(4, width - 3)                       # 左边 2 列 + 指针
        busy = self.busy()
        out = []
        gone_n = sum(1 for st in stops if st["compacted"])
        if gone_n:
            out.append(f"{td.UNFOLD if self.expanded else td.FOLD}\t"
                       + _clip(f"{ct.DIMC}#1–#{gone_n} {'−' if self.expanded else '+'}{ct.RESET}", room))
        for n, st in enumerate(stops, 1):
            if st["compacted"] and not self.expanded:
                continue
            ask = ct._oneline(st["prompt"]) if st["kind"] == "human" else _t("（{p}）").format(p=st["prompt"])
            colour = (ct.WARN_C if busy and n == len(stops) else ct.DIMC if st["id"] in self.missing else "")
            out.append(f"{st['id']}\t" + _clip(f"{ct.DIMC}#{n}{ct.RESET} {colour}{ask}", room) + ct.RESET)
        return "\0".join(out)

    def wide_header(self, width):
        if self.tr is None:
            head = _t("这个窗格里没找到 Claude 会话")
        else:
            stops = self.tr.visible()
            gone_n = sum(1 for st in stops if st["compacted"])
            head = (_t("{gone} 轮已压缩 · 当前 {n} 轮").format(gone=gone_n, n=len(stops) - gone_n) if gone_n
                    else _t("{n} 轮").format(n=len(stops)))
        if self.flash and time.monotonic() < self.flash[1]:
            head += f" · {ct.WARN_C}{self.flash[0]}{ct.RESET}"
        cfg = cfg_key(self.cfg_dir)
        u = ct.read_usage(cfg)
        model, ctx = self.model_ctx()
        return "\n".join([buttons_line(wide_buttons(), u), head, *ct.status_lines(width, model, ctx, u, "", cfg)])

    def rows(self, width):
        if not self.tr:
            return ""
        if self.mode == "narrow":
            return self.narrow_rows(width)
        return td.rows_of(self.tr, self.busy(), self.opened, width, self.expanded, (), frozenset(self.missing))

    def push(self):
        width = self.width()
        rows, header = self.rows(width), self.header(width)
        last_rows, last_header = self.pushed
        # 表头和列表分两次发（tmux 版踩过：写在同一条里 fzf 会把后面的 change-header 吞掉）
        if header != last_header and not fzf(self.sock, "change-header:" + header):
            return
        if rows != last_rows:
            tmp = self.rows_file.with_suffix(".tmp")
            tmp.write_text(rows)
            tmp.replace(self.rows_file)
            if not fzf(self.sock, f"reload-sync(cat {_q(self.rows_file)})"):
                self.pushed = (None, header)
                return
        self.pushed = (rows, header)

    def say(self, msg, secs=6):
        """跳转线程里也会叫：只记下来、叫醒主循环，推送都在主线程里做。"""
        self.flash = (msg, time.monotonic() + secs)
        self.later(secs + 0.1)
        os.write(self.wake_w, b"x")

    # -- 点击 --

    def on_command(self, line):
        if (STATE / "debug").exists():        # touch 一下这个文件就记下每条点击命令（排查点不中的问题）
            print(time.strftime("%T"), "cmd", repr(line), file=sys.stderr, flush=True)
        parts = line.split()
        if not parts:
            return
        if parts[0] == "open" and len(parts) > 1:
            sid = parts[1].strip("'")
            if sid in (td.FOLD, td.UNFOLD):    # 「已压缩」那一行：只在侧栏里展开 / 收起
                self.expanded = sid == td.FOLD
                self.push()
                return
            self.opened = sid
            self.push()
            self.jump_token += 1
            threading.Thread(target=self.jump, args=(sid, self.jump_token), daemon=True).start()
        elif parts[0] == "click" and len(parts) > 2:          # 表头
            ln, col = parts[1], parts[2]
            if not (ln.isdigit() and col.isdigit()):
                return
            if self.mode == "narrow":
                if ln == "1":
                    self.act("wide")
                elif int(ln) >= 3:             # 模型、账号、百分比那几行：查一次额度
                    self.kick_usage(force=True)
                return
            if int(ln) >= 3:                   # 宽版的模型 / ctx / 5h / 7d 那几行：去查一次真实额度
                self.kick_usage(force=True)
                return
            act = button_at(wide_buttons(), int(col)) if ln == "1" else None
            if act:
                self.act(act)
        elif parts[0] == "foot" and len(parts) > 2 and parts[2].isdigit():   # 窄版底部的按钮
            act = button_at(narrow_footer(), int(parts[2]))
            if act:
                self.act(act)
        elif parts[0] == "toggle":            # 插件动作（快捷键）：拉到窄版 / 拉回宽版
            self.act("wide" if self.mode == "narrow" else "narrow")

    def act(self, act):
        """宽版、窄版的按钮共用。"""
        if act == "usage":
            self.kick_usage(force=True)
        elif act == "bottom":
            self.jump_token += 1
            self.send("C-End")
            fzf(self.sock, "last")
        elif act in ("wide", "narrow"):
            self.set_mode(act)
        elif act.startswith("tool:"):
            self.run_tool(CFG["buttons"][int(act[5:])])

    def run_tool(self, button):
        """配置里的按钮：在 Claude 那一格的目录里开本插件的浮窗（manifest 的 tool 窗格），经 sh -c 跑它的 command。"""
        import shlex
        cmd = button["command"]
        try:
            prog = shlex.split(cmd)[0]
        except (ValueError, IndexError):
            prog = cmd
        if not shutil.which(prog):
            self.say(_t("找不到 {p}").format(p=prog))
            return
        cwd = (pane_info(self.target) or {}).get("foreground_cwd") or ""
        try:
            # popup 不收 target_pane_id（herdr 回 invalid_params：popup panes target the active pane），目录用环境变量传
            call("plugin.pane.open", plugin_id=PLUGIN_ID, entrypoint="tool", placement="popup",
                 env={"CLAUDE_TRAIL_CWD": cwd, "CLAUDE_TRAIL_CMD": cmd, "PATH": os.environ.get("PATH", "")})
        except (OSError, ValueError, HerdrError) as e:
            self.say(f"{prog}: {e}")                 # 比如设置页开着时 herdr 回 ui_busy

    # -- 跳转：照搬 claude_trail.cmd_jump，读屏 / 发键换成 herdr --

    # herdr 的 send-keys 不认 Home / End / PageUp / PageDown（报 invalid_key），直接发终端的转义序列
    KEYS = {"C-Home": "\x1b[1;5H", "C-End": "\x1b[1;5F", "PageUp": "\x1b[5~", "PageDown": "\x1b[6~"}

    def send(self, *keys):
        """所有按键拼成一次 send_text。keys 里不在 KEYS 表里的原样发（滚轮的鼠标序列）。"""
        call("pane.send_text", pane_id=self.target, text="".join(self.KEYS.get(k, k) for k in keys))

    def screen(self):
        return call("pane.read", pane_id=self.target, source="visible")["read"]["text"]

    def jump(self, stop_id, token):
        t0 = time.monotonic()
        try:
            found = self._jump(stop_id, token)
        except (OSError, ValueError, KeyError, HerdrError) as e:
            found = f"error {e!r}"
        stops = self.tr.visible() if self.tr else []
        n = next((i + 1 for i, s in enumerate(stops) if s["id"] == stop_id), None)
        try:
            with open(JUMP_LOG, "a") as f:
                f.write(json.dumps({"at": time.strftime("%F %T"), "pane": self.target, "sid": self.sid,
                                    "turn": n, "of": len(stops), "result": found,
                                    "secs": round(time.monotonic() - t0, 3)}) + "\n")
        except OSError:
            pass

    def _jump(self, stop_id, token):
        stops = self.tr.visible() if self.tr else []
        idx = next((i for i, x in enumerate(stops) if x["id"] == stop_id), None)
        if idx is None:
            return "unknown"
        # Claude 还在干的那一轮没有最终回复，拿半截的文字去找会一直对不上：最新的东西本来就在最底下
        if idx == len(stops) - 1 and self.busy():
            self.send("C-End")
            return "busy-bottom"
        needle = ct._needle({"final": stops[idx]["reply"]})
        if not needle:
            self.say(_t("轨迹：这一轮 Claude 没写文字回复，没法定位"))
            return "no-text"
        if stop_id in self.missing:
            self.say(_t("轨迹：这一轮在主界面里已经看不到了（续接的会话只显示最近一段，或被 /compact 清掉）"))
            return "missing"
        needles = ct.needles_of([{"final": s["reply"]} for s in stops])
        info = pane_info(self.target) or {}
        height = (info.get("scroll") or {}).get("viewport_rows") or 40
        pages = 2 if height >= 30 else 1
        from_top = idx < len(stops) / 2

        def cancelled():
            return self.jump_token != token

        def needle_row(text):
            for i, row in enumerate(text.splitlines()):
                if needle in ct._norm(row) and not row.lstrip().startswith("❯"):
                    return i
            return None

        def press(before, *keys):
            """发键、等屏幕**不再变**再返回（见 cmd_jump 里的说明）；什么都没动（到头了）返回 None。"""
            self.send(*keys)
            time.sleep(ct.REDRAW_WAIT)
            now, last = self.screen(), None
            for _ in range(60):
                if now != before and now == last:
                    return now
                last = now
                time.sleep(0.01)
                now = self.screen()
            return None if now == before else now

        def where(text):
            rows = [ct._norm(r) for r in text.splitlines() if not r.lstrip().startswith("❯")]
            seen = None
            for i, n in enumerate(needles):
                if n and any(n in r for r in rows):
                    seen = i
            return seen

        def lift(now):
            down = f"\x1b[<65;10;{height // 2}M"
            for _ in range(10):
                r = needle_row(now)
                if r is None or r <= 4:
                    return
                if AT_BOTTOM not in now:        # 已经在最底：往下推不动，别白等 press 那 0.6 秒
                    return
                if r - 4 >= height // 2:
                    now = press(now, "PageDown")
                else:
                    now = press(now, *[down] * min(2, r - 4))
                if now is None:
                    return

        def search(nudge=True):
            now = self.screen()
            # 已经在最底时 Ctrl+End 什么都不会动，press 要干等 0.6 秒才认定「到头了」（tmux 版也是）
            if from_top or AT_BOTTOM in now:
                now = press(now, "C-Home" if from_top else "C-End") or self.screen()
            last_turn, since = None, 0
            prev_down, fine = from_top, False
            for _ in range(ct.MAX_ROLLS):
                if needle_row(now) is not None:
                    if nudge:
                        lift(now)
                    return True
                if cancelled():
                    return True
                if time.monotonic() > deadline:
                    return None
                cur = where(now)
                steps, down = pages, prev_down
                if cur is not None and not fine:
                    down = cur < idx
                    if down != prev_down:
                        fine = True
                    elif last_turn is not None and cur != last_turn and since:
                        rate = since / abs(cur - last_turn)
                        steps = max(pages, min(int(rate * abs(idx - cur) * 0.6), ct.MAX_ROLLS))
                    last_turn, since = cur, 0
                prev_down = down
                now = press(now, *["PageDown" if down else "PageUp"] * steps)
                since += steps
                if now is None:
                    return False
            return False

        deadline = time.monotonic() + ct.JUMP_BUDGET
        for attempt in range(2):
            found = search(nudge=attempt == 0)
            if found is None:
                self.say(_t("轨迹：这一轮没找着，先停在这儿了"))
                return "timeout"
            if cancelled():
                return "cancelled"
            if found and needle_row(self.screen()) is not None:
                self.missing.discard(stop_id)
                return "found"
        # 翻到头也没有：比主界面最上面那一轮更早的都翻不到，一起记下来（侧栏灰显，再点立刻提示）
        self.send("C-Home")
        time.sleep(0.4)
        top = ct.locate(self.screen(), needles)
        gone = [x["id"] for x in stops[:top[0]]] if top and top[0] > idx else [stop_id]
        self.missing |= set(gone)
        self.send("C-End")
        self.say(_t("轨迹：主界面里找不到这一轮（续接的会话只显示最近一段，或被 /compact 清掉）"))
        return "not-found"

    # -- 主循环 --

    def subscribe(self):
        """订阅这个 Claude 的状态变化、窗格关闭。`pane.updated` 不订：它不能按窗格过滤，
        别的插件（比如 herdr-sidebar 每几秒更新一次自己的 token）会一直把这里叫醒。"""
        s = socket.socket(socket.AF_UNIX)
        s.connect(SOCK)
        subs = [{"type": "pane.agent_status_changed", "pane_id": self.target},
                {"type": "pane.closed"}, {"type": "pane.exited"}]
        s.sendall((json.dumps({"id": "trail", "method": "events.subscribe",
                               "params": {"subscriptions": subs}}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise HerdrError("closed", "subscription closed")
            buf += chunk
        ack, buf = buf.split(b"\n", 1)
        ack = json.loads(ack)
        if "error" in ack:
            raise HerdrError(ack["error"].get("code"), ack["error"].get("message"))
        return s, buf

    def fzf_cmd(self, header):
        fifo = _q(self.fifo)
        narrow = self.mode == "narrow"
        # 窄版：每轮一行，不要项与项之间的空行；按钮放 footer，固定在底部
        extra = (["--footer", buttons_line(narrow_footer()), "--footer-border=top",
                  "--bind", ("click-footer:execute-silent(printf 'foot %s %s\\n' \"$FZF_CLICK_FOOTER_LINE\" "
                             f"\"$FZF_CLICK_FOOTER_COLUMN\" >> {fifo})")] if narrow else ["--gap"])
        return [
            "fzf", "--read0", "--ansi", "--no-input", "--no-sort", "--layout=reverse", "--sync",
            "--track", "--id-nth=1", "--delimiter=\t", "--with-nth=2..",
            "--highlight-line", "--info=hidden", "--pointer=▌", "--no-scrollbar", "--ellipsis=…", *extra,
            f"--listen={self.sock}", "--header", header, "--header-border=bottom",
            # 打开时落在最后一轮；只这一次，之后刷新列表不把你拽回去
            "--bind", "load:last+unbind(load)",
            # 点一项 / 点表头：写进 FIFO，这个进程马上收到（不用 tmux 版那样起一个 python，也不轮询文件）
            "--bind", f"left-click:execute-silent(printf 'open %s\\n' {{1}} >> {fifo})",
            "--bind", ("click-header:execute-silent(printf 'click %s %s\\n' \"$FZF_CLICK_HEADER_LINE\" "
                       f"\"$FZF_CLICK_HEADER_COLUMN\" >> {fifo})"),
            "--bind", "scroll-up:offset-up+offset-up+offset-up,scroll-down:offset-down+offset-down+offset-down",
            "--bind", "enter:ignore,double-click:ignore,esc:ignore",
        ]

    # -- 宽版 / 窄版 --

    def split_widths(self):
        """(Claude 那一格的宽, 侧栏的宽)；两格就是 open_sidebar 切出来的那一对，宽度加起来是那条分割的宽。"""
        rects = {p["pane_id"]: p["rect"] for p in call("pane.layout", pane_id=self.me)["layout"]["panes"]}
        return rects[self.target]["width"], rects[self.me]["width"]

    def set_width(self, cols):
        """把侧栏调到 cols 列（挪 Claude 和侧栏之间那条线，见 open_sidebar）。herdr 把分割比例限制在
        0.1–0.9（src/layout.rs:295），所以最窄是这条分割的 10%，要更窄它会自己夹住。"""
        # resize 挪的是窗格「那个方向上」的那条边：Claude 的右边、侧栏的左边都是中间这条线。
        # 反过来（Claude 往左、侧栏往右）挪的是外侧的线，会去挤旁边的 herdr-sidebar（实测 32 列挤成 18 列）
        tw, sw = self.split_widths()
        if sw > cols:
            call("pane.resize", pane_id=self.target, direction="right", amount=(sw - cols) / (tw + sw))
        elif sw < cols:
            call("pane.resize", pane_id=self.me, direction="left", amount=(cols - sw) / (tw + sw))

    def set_mode(self, mode):
        """左上角的 » / «：一键把宽度拉到窄版（herdr 允许的最窄，约 15 列）或拉回宽版（上次的宽度，至少 width）。
        画宽版还是窄版不在这里定：宽度一变，窗格收到 SIGWINCH，主循环按新宽度换（见 mode_for）。"""
        try:
            if mode == "narrow":
                self.wide = self.width()
                self.set_width(1)
            else:
                tw, sw = self.split_widths()
                self.set_width(max((self.wide or 0) + FRAME, WIDE_MIN + FRAME, round((tw + sw) * WIDTH)))
        except (OSError, ValueError, KeyError, HerdrError):
            pass

    def mode_for(self, width):
        return "wide" if width >= WIDE_MIN else "narrow"

    def follow_width(self):
        """宽度跨过 WIDE_MIN 就换成另一版的 fzf（列表、表头、底部都不一样，换不了就重起一个）。
        你拖分隔线时窗格收到一串 SIGWINCH，只有跨过阈值那一下才重起，别的时候只按新宽度重画表头。"""
        mode = self.mode_for(self.width())
        if mode == self.mode:
            return False
        self.stop_fzf()
        self.mode = mode
        self.start_fzf()
        return True

    def start_fzf(self):
        # 右键固定交给 herdr 菜单：轨迹自己不用右键，误点了菜单里的「Send right-clicks to pane」
        # 之后右键会被 fzf 吞掉，连 herdr 的菜单都弹不出来
        try:
            call("pane.input.set", pane_id=self.me, right_click="herdr")
        except (OSError, ValueError, HerdrError):
            pass
        width = self.width()
        rows, header = self.rows(width), self.header(width)
        self.rows_file.write_text(rows)
        self.pushed = (rows, header)
        with open(self.rows_file, "rb") as stdin:
            self.proc = subprocess.Popen(self.fzf_cmd(header), stdin=stdin, stdout=subprocess.PIPE)

    def stop_fzf(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    # -- 主循环 --

    def run(self):
        self.reg.write_text(json.dumps({"sidebar": self.me, "target": self.target, "pid": os.getpid()}))
        for p in (self.fifo, self.sock):
            p.unlink(missing_ok=True)
        os.mkfifo(self.fifo)
        # 读端非阻塞；自己再拿一个写端占着，fzf 那边每次写完关掉也不会让读端一直读到 EOF 空转
        fifo_r = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK)
        fifo_w = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        signal.set_wakeup_fd(self.wake_w, warn_on_full_buffer=False)
        signal.signal(signal.SIGWINCH, lambda *_: None)   # 窗格宽度变了：柱子按新宽度重画
        sub, evbuf = self.subscribe()
        self.refresh()
        self.kick_usage()
        self.mode = self.mode_for(self.width())
        self.start_fzf()
        buf = b""
        try:
            while True:
                if self.proc is None or self.proc.poll() is not None:
                    break                                     # fzf 自己退出了
                now = time.monotonic()
                timeout = min(TICK, max(0.0, self.due[0] - now)) if self.due else TICK
                r, _, _ = select.select([sub, fifo_r, self.wake_r, self.proc.stdout], [], [], timeout)
                if self.proc and self.proc.stdout in r:
                    break
                stale = not r
                if self.due and self.due[0] <= time.monotonic():
                    self.due = [t for t in self.due if t > time.monotonic()]
                    stale = True
                if self.wake_r in r:
                    os.read(self.wake_r, 4096)
                    stale = True
                if sub in r:
                    # 一次可能来好几条：自己切行，别用带缓冲的 readline（select 看不见缓冲里剩下的）
                    chunk = sub.recv(65536)
                    if not chunk:
                        break                                 # herdr server 没了
                    *events, evbuf = (evbuf + chunk).split(b"\n")
                    gone = False
                    for line in events:
                        ev = json.loads(line)
                        if "error" in ev:                    # events_lost：重订，按现状重读
                            sub.close()
                            sub, evbuf = self.subscribe()
                            stale = True
                            break
                        # 事件名两种写法都有：状态是 pane.agent_status_changed，关闭是 pane_exited（实测）
                        name, data = (ev.get("event") or "").replace(".", "_"), ev.get("data") or {}
                        if name in ("pane_closed", "pane_exited") and data.get("pane_id") == self.target:
                            gone = True
                        if name == "pane_agent_status_changed":
                            stale = True
                            self.later(*LATER)
                    if gone:
                        break
                if fifo_r in r:
                    try:
                        buf += os.read(fifo_r, 65536)
                    except BlockingIOError:
                        pass
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        self.on_command(ln.decode("utf-8", "replace"))
                if stale:
                    if not self.refresh():
                        break
                    if self.no_claude_since and time.monotonic() - self.no_claude_since > GONE_GRACE:
                        break                                 # 这一格里已经没有 Claude 了：真正关掉
                    if not r:
                        self.kick_usage()
                    if not self.follow_width():
                        self.push()
        finally:
            self.jump_token += 1
            for p in (self.reg, self.fifo, self.sock, self.rows_file, self.rows_file.with_suffix(".tmp")):
                p.unlink(missing_ok=True)
            self.stop_fzf()
            sub.close()
            for fd in (fifo_r, fifo_w):
                os.close(fd)


def cmd_pane():
    target, me = os.environ.get("CLAUDE_TRAIL_TARGET"), os.environ.get("HERDR_PANE_ID")
    if not target or not me:
        sys.exit("trail.py pane: CLAUDE_TRAIL_TARGET / HERDR_PANE_ID not set")
    # 窗格一退出就关，报错来不及看：stderr 记到状态目录里
    STATE.mkdir(parents=True, exist_ok=True)
    err = os.open(STATE / "pane.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(err, 2)
    # herdr 关窗格时发 SIGHUP：转成正常退出，好走 finally 把 fzf 和临时文件收掉。
    # 只认第一次：紧跟着还会再来一次（实测只删掉了一半文件），第二次会把 finally 打断
    def bye(*_):
        for sig in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        sys.exit(0)
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, bye)
    Sidebar(target, me).run()


# ---- 开 / 关 ---------------------------------------------------------------------

def sidebars():
    """{侧栏窗格: Claude 窗格}，只留还活着的。"""
    try:
        alive = {p["pane_id"] for p in call("pane.list")["panes"]}
    except (OSError, ValueError, KeyError, HerdrError):
        return {}
    out = {}
    for f in RUN.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            out[d["sidebar"]] = d["target"]
        except (OSError, ValueError, KeyError):
            continue
    out = {sb: tg for sb, tg in out.items() if sb in alive}
    # 侧栏被强杀、没来得及收拾：窗格已经不在、放了 10 秒以上的文件都删掉
    keep = {sb.replace(":", "_") for sb in out}
    for f in RUN.iterdir():
        try:
            if f.name.split(".")[0] not in keep and time.time() - f.lstat().st_mtime > 10:
                f.unlink(missing_ok=True)
        except OSError:
            pass
    return out


def open_sidebar(target):
    r = call("plugin.pane.open", plugin_id=PLUGIN_ID, entrypoint="trail", placement="split",
             target_pane_id=target, direction="right", focus=False, env={"CLAUDE_TRAIL_TARGET": target})
    pane = r["plugin_pane"]["pane"]["pane_id"]
    # 侧栏进程起来要零点几秒，它写登记之前紧跟着来的第二个事件会以为还没开、再开一个：这里先写上
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / f"{pane.replace(':', '_')}.json").write_text(json.dumps({"sidebar": pane, "target": target}))
    # plugin.pane.open 没有比例参数，开出来是对半分；把中间那条线往右推到 66 / 34（默认窄版就推到底，
    # herdr 会夹在 0.9）。
    # 要对 Claude 那一格做：对侧栏做 resize right 挪的是侧栏**右边**那条线，右边还有别的窗格
    # （比如 herdr-sidebar 停在右边）时会去挤它（实测把它从 32 列挤到 18 列）
    call("pane.resize", pane_id=target, direction="right",
         amount=0.5 - (0.1 if CFG["default_mode"] == "narrow" else WIDTH))
    return pane


def cmd_toggle():
    ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    focused = ctx.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID")
    if len(sys.argv) > 2:
        focused = sys.argv[2]
    if not focused:
        return
    with locked():
        bars = sidebars()
        for sb, tg in bars.items():
            if focused in (sb, tg):                   # 焦点在侧栏上、或在开着侧栏的 Claude 上：收起 / 展开
                with open(RUN / f"{sb.replace(':', '_')}.fifo", "w") as f:
                    f.write("toggle\n")
                return
        info = pane_info(focused)
        if not session_id(info):
            notify(_t("轨迹：这个窗格里没找到 Claude 会话"))
            return
        open_sidebar(focused)


def cmd_auto():
    """事件钩子（tab.focused / pane.agent_detected）：默认打开。

    只在这个 tab 里**正好有一个** Claude 时开，停在它右边：没有 Claude 不开；好几个 Claude
    （agent teams 的队友）时再切一块谁都看不清，也不开。另外要够宽、还没开着。
    侧栏没有「关闭」只有「收起」，所以不用记谁被手动关过：被 herdr 的 Close pane 关掉的，下次切过来照样开。"""
    if not CFG["auto_open"]:
        return
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}"
    try:
        with open(EVENT_LOG, "a") as f:
            f.write(raw[:400] + "\n")
        if EVENT_LOG.stat().st_size > 200_000:
            EVENT_LOG.write_text("")
    except OSError:
        pass
    ev = json.loads(raw)
    data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
    pane = data.get("pane_id") or (data.get("pane") or {}).get("pane_id")
    tab = data.get("tab_id") or (data.get("pane") or {}).get("tab_id")
    if not tab and pane:
        tab = (pane_info(pane) or {}).get("tab_id")
    tab = tab or os.environ.get("HERDR_TAB_ID")
    if not tab:
        return
    with locked():
        try:
            panes = [p for p in call("pane.list")["panes"] if p.get("tab_id") == tab]
        except (OSError, ValueError, KeyError, HerdrError):
            return
        # 只看 agent：Claude 刚被认出来（pane.agent_detected）时会话 id 往往还没报上来，要求有 id 就会漏开
        # （实测第二次起测试 Claude 就没开）。轨迹自己会等会话 id 出现
        claudes = [p for p in panes if p.get("agent") == "claude"]
        if len(claudes) != 1:
            return
        c = claudes[0]["pane_id"]
        if c in sidebars().values():
            return
        try:
            rects = {p["pane_id"]: p["rect"] for p in call("pane.layout", pane_id=c)["layout"]["panes"]}
        except (OSError, ValueError, KeyError, HerdrError):
            return
        if (rects.get(c) or {}).get("width", 0) < MIN_WIDTH:
            return
        open_sidebar(c)


def locked():
    """开 / 关的判断和动作要串行：焦点一变会连着来好几个事件，各起一个进程。"""
    import fcntl
    from contextlib import contextmanager

    @contextmanager
    def lock():
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "open.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
    return lock()


COMMANDS = {"toggle": cmd_toggle, "pane": cmd_pane, "auto": cmd_auto}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}}")
    COMMANDS[sys.argv[1]]()
