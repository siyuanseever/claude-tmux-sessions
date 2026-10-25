#!/usr/bin/env python3
"""statetrail 的 herdr 适配层：停在 Claude 窗格右边的会话轨迹。

    trail.py toggle   插件动作：焦点所在的 Claude 还没有轨迹就开一个，有了就在宽版 / 窄版宽度之间拉
    trail.py auto     事件钩子：tab 里有 Claude 时自动打开
    trail.py pane     轨迹窗格本体（herdr 按 manifest 的 [[panes]] 起它）：这个进程 + 一个 fzf

每个轨迹自己带一个小进程，订阅 herdr 的事件（Claude 状态变了、窗格关了），没事件时阻塞在 select 上。
会话 id 直接问 herdr（`pane.get` 的 agent_session），不需要钩子和状态文件。读屏 / 发键走 herdr 的 socket
（一次 0.3ms）。读对话记录、切轮次、画柱子、跳转算法都在 core/ 里，这里只管和 herdr 打交道。
"""
import http.client
import json
import os
import re
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import claude, jump, render as R  # noqa: E402
import theme                                 # noqa: E402
from core.i18n import set_lang, t as _t     # noqa: E402
from core.transcript import ClaudeTranscript, reader_for  # noqa: E402,F401  ClaudeTranscript 给测试脚本用

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID") or "statetrail"
SOCK = os.environ.get("HERDR_SOCKET_PATH") or str(Path.home() / ".config" / "herdr" / "herdr.sock")
STATE = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR")
             or Path.home() / ".local" / "state" / "herdr" / "plugins" / PLUGIN_ID)
RUN = STATE / "run"                # <轨迹窗格>.json / .fifo / .sock / .rows
JUMP_LOG = STATE / "jump.log"      # 每次跳转一行：用了多久、找到没有
EVENT_LOG = STATE / "events.log"   # 自动打开收到的事件（文档没写 HERDR_PLUGIN_EVENT_JSON 的结构，先记下来）
TAB_NAMES = STATE / "tab-names.json"   # {tab_id: 我们上次给它起的名字}：label 还是这个，才算「没被你手动改过」
TAB_NAME_MAX = 24                  # tab 名最多多少列（中文算两格），太长 tab 栏放不下
claude.USAGE_DIR = STATE           # 额度读数存在插件自己的状态目录
MIN_WIDTH = 120                    # Claude 那一格至少这么宽才自动开
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
                  or Path.home() / ".config" / "herdr" / "plugins" / "config" / PLUGIN_ID)
# 配置（<配置目录>/config.toml，`herdr plugin config-dir statetrail` 打印这个目录）。文件不存在就全用默认值。
# 这一阶段只开放确有需要的几项：按钮列表、自动打开、默认宽版还是窄版、宽版宽度、图标字体、界面语言。
DEFAULTS = {
    "auto_open": True,             # 切到有 Claude 的 tab 时自动打开
    "default_mode": "wide",        # 打开时多宽：宽版（"wide"，width 那么宽）还是窄版（"narrow"，herdr 允许的最窄）
    "width": 0.34,                 # 宽版占 Claude 那一格的比例
    # 图标："auto"（默认）：本机装了 Nerd Font 就用 "nerd"，没装用 "unicode"；也可以直接写 "nerd" / "unicode" /
    # "text"（全部写成字）。装了不代表终端在用它，显示成方块就手动设 "unicode"
    "icons": "auto",
    # 额度：默认只读 Claude Code 自己缓存在 .claude.json 里的读数，不碰任何凭据（只在你跑 /usage 之类时更新，可能是几天前的）。
    # 设 true 才自己去查真实额度：读 Claude Code 存在钥匙串里的令牌，调 /usage 用的那个接口，最多 30 分钟一次
    "live_usage": False,
    "lang": "",                    # "zh" / "en"；不写就按系统 locale
    "tab_names": True,             # 没手动起过名的 tab，用 Claude 的会话名命名（会话名变了跟着改）
    # 自定义按钮，宽版、窄版都放在最底下一行（和上面 Claude 相关的按钮分开）。两种：
    #   command = "lazygit"                       在 Claude 那一格的目录里，用本插件的浮窗跑它（经 sh -c）
    #   action = "herdr-sidebar.open-sidebar"     调别的 herdr 插件的动作（默认配置里不放这种）
    # icon 可选：写 ICONS 里的名字（"git"、"files"…）就按 icons 那一档取字形，也可以直接写一个字形
    "buttons": [{"label": "Git", "command": "lazygit", "icon": "git"},
                {"label": "文件", "command": "yazi", "icon": "files"}],
}


def config():
    try:
        import tomllib                 # Python 3.11+；更老的版本由 missing_deps() 报出来，这里先用默认值
        with open(CONFIG_DIR / "config.toml", "rb") as f:
            user = tomllib.load(f)
    except (ImportError, OSError, ValueError):
        user = {}
    cfg = {**DEFAULTS, **{k: v for k, v in user.items() if k in DEFAULTS}}
    cfg["buttons"] = [b for b in cfg["buttons"] if isinstance(b, dict) and b.get("label")
                      and (b.get("command") or re.fullmatch(r"[\w.:-]+\.[\w:-]+", str(b.get("action") or "")))]
    try:
        cfg["width"] = min(0.9, max(0.1, float(cfg["width"])))
    except (TypeError, ValueError):
        cfg["width"] = DEFAULTS["width"]
    for key, allowed in (("default_mode", ("wide", "narrow")), ("icons", ("auto", "nerd", "unicode", "text"))):
        if cfg[key] not in allowed:
            cfg[key] = DEFAULTS[key]
    return cfg


def nerd_font_installed():
    """本机装没装 Nerd Font：macOS 看 ~/Library/Fonts 和 /Library/Fonts 的文件名，Linux 问 fc-list。
    只说明装了，不说明终端在用它。"""
    import glob
    pat = re.compile(r"nerd ?font|(^|[ _-])nf([ _.-]|$)", re.I)
    names = [os.path.basename(f) for d in (Path.home() / "Library" / "Fonts", Path("/Library/Fonts"))
             for f in glob.glob(str(d / "*"))]
    if not names and shutil.which("fc-list"):
        try:
            names = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=3).stdout.splitlines()
        except (OSError, subprocess.TimeoutExpired):
            names = []
    return any(pat.search(n) for n in names)


CFG = config()
set_lang(CFG["lang"])
if CFG["icons"] == "auto":
    CFG["icons"] = "nerd" if nerd_font_installed() else "unicode"
claude.LIVE = bool(CFG["live_usage"])
FZF_COLORS = theme.apply(R)        # 颜色跟着 herdr 的主题（和左侧 Agents 栏、选中行、边框一致）
WIDTH = CFG["width"]
TICK = 60                          # 没有事件时多久醒一次：额度的倒计时按分钟变
WIDE_MIN = 30                      # 轨迹窗格（tty）至少这么宽才画宽版，窄了画窄版（拖分隔线时跟着变）
FRAME = 2                          # herdr 给窗格画的边框：tty 比 pane.layout 报的矩形窄 2 列、矮 2 行（实测 51 → 49）
# 宽版最多挤到 Claude 剩多少列：Claude 自己的 /diff 面板要终端至少 110 列才开得出来
# （code.claude.com/docs/en/interactive-mode.md「Diff panel」）。放不下宽版就用窄版
CLAUDE_MIN = 110
TOOL_SHARE = 0.6                   # 按钮开的工具（lazygit / yazi）占 Claude 那一块的比例，左边的 Claude 还看得见
GONE_GRACE = 15                    # Claude 退出后等多少秒才关轨迹：切账号时 Claude 会退出再重启
LATER = (0.5, 1.5)                 # Claude 状态变了之后再补刷几次：Stop 时最后那条回复还没写进对话记录
VERSION = re.compile(r"^\d+\.\d+\.\d+$")   # Claude Code 的进程名是版本号


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
    if not info or not reader_for(info.get("agent")):
        return None
    return (info.get("agent_session") or {}).get("value")


def notify(title, body=None):
    try:
        call("notification.show", title=title, body=body, sound="none")
    except (OSError, ValueError, HerdrError):
        pass


def claude_pid(pane):
    try:
        procs = call("pane.process_info", pane_id=pane)["process_info"]["foreground_processes"]
    except (OSError, ValueError, KeyError, HerdrError):
        return None
    for p in procs:
        if p.get("argv0") == "claude" or VERSION.match(p.get("name") or ""):
            return p.get("pid")
    return None


# ---- 依赖 ------------------------------------------------------------------------

FZF_MIN = (0, 71)                  # --id-nth 是 fzf 0.71 加的（click-footer 0.65、Unix socket 的 --listen 0.66）
DEPS_WARNED = STATE / "deps-warned"


def missing_deps():
    """缺了什么：[(一句话, 怎么装)]。python3 本身没有的话 herdr 起不来这个脚本，只能靠 README 说。"""
    out = []
    if sys.version_info < (3, 11):
        out.append((_t("要 Python 3.11 以上，现在是 {v}").format(v=sys.version.split()[0]),
                    "brew install python / apt install python3"))
    path = shutil.which("fzf")
    if not path:
        out.append((_t("没找到 fzf"), "brew install fzf / https://github.com/junegunn/fzf#installation"))
    else:
        try:
            v = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3).stdout
            ver = tuple(int(x) for x in re.match(r"(\d+)\.(\d+)", v).groups())
        except (OSError, AttributeError, ValueError, subprocess.TimeoutExpired):
            ver = (0, 0)
        if ver < FZF_MIN:
            out.append((_t("fzf 要 {need} 以上，现在是 {have}").format(
                need=".".join(map(str, FZF_MIN)), have=".".join(map(str, ver))), "brew upgrade fzf"))
    return out


def warn_deps(quiet=False):
    """缺依赖就用 herdr 的通知说一声。quiet（自动打开时）一天最多说一次，别每切一次 tab 就弹。返回缺没缺。"""
    miss = missing_deps()
    if not miss:
        return False
    if quiet:
        try:
            if time.time() - DEPS_WARNED.stat().st_mtime < 86400:
                return True
        except OSError:
            pass
        STATE.mkdir(parents=True, exist_ok=True)
        DEPS_WARNED.touch()
    notify(_t("statetrail 缺依赖"), "; ".join(f"{m}（{how}）" for m, how in miss))
    return True


# ---- 图标、按钮 ------------------------------------------------------------------

# 能用图标说清楚的都用图标（Nerd Font 的字形在私用区，终端按 1 格宽画）。
# 别人不一定装了 Nerd Font，所以配置 icons 可以换成普通 Unicode 符号，或者全写成字。
ICONS = {
    #          nerd       unicode  text
    "narrow":  ("", "»",     ">>"),       # 宽版左上角：拉到窄版
    "wide":    ("", "«",     "<<"),       # 窄版左上角：拉回宽版
    "bottom":  ("", "⇊",     "End"),      # 主界面到最底
    "usage":   ("", "⟳",     "Ref"),      # 马上查一次额度
    "model":   ("", "✦",     ""),         # 模型前面的标记
    "account": ("", "@",     ""),         # 账号前面的标记
    "ctx":     ("", "◧",     "ctx"),
    "5h":      ("", "◷",     "5h"),
    "7d":      ("", "▦",     "7d"),
    "git":     ("", "±",     ""),         # 文字档不另加图标，按钮本身就有字
    "files":   ("", "▤",     ""),
}
_TIER = ("nerd", "unicode", "text").index(CFG["icons"])


def icon(name):
    v = ICONS[name][_TIER]
    return _t(v) if _TIER == 2 else v          # 文字档的字要跟着界面语言


def button_icon(b):
    """配置里的按钮：icon 是 ICONS 里的名字就按档位查表，否则当成字形原样用（文字档都不用）。"""
    ic = str(b.get("icon") or "")
    if CFG["icons"] == "text" or not ic:
        return ""
    return icon(ic) if ic in ICONS else ic


SLOT, GAP = 3, 1


def tool_buttons():
    """[(文字, 动作)]：图标和字只画一个，宽版窄版一样（用户定的）。有图标就只画图标；
    icons = "text" 或者这个按钮没配图标，就只写字。"""
    return [(button_icon(b) or _t(b["label"]), f"tool:{i}") for i, b in enumerate(CFG["buttons"])]


# 宽版表头第 1 行只放和 Claude 相关的按钮，格子的宽度、点偏一格也算的规矩和 tmux 版一样。
# 最左上角是「拉到窄版」（»），窄版的「拉回宽版」（«）也在同一个位置（第 1 行第 4 列）。没有「关闭」：
# 关掉之后鼠标就没有地方再把它点出来了（herdr 的右键菜单是写死的，插件动作进不去，
# 见 herdr v0.9.5 src/client/shell/context_menu.rs:59-78）。
# 配置里的自定义按钮（默认 Git = lazygit、文件 = yazi）只是打开别的工具，性质不同：宽版、窄版都放到
# 最底下一行（fzf 的 footer），用分隔线和上面隔开（用户定的）。
def wide_buttons():
    return [(icon("narrow"), "narrow", 0), (icon("bottom"), "bottom", 0), (icon("usage"), "usage", 10)]


def footer_buttons():
    return [(t, a, 0) for t, a in tool_buttons()]


def _cell_w(text):
    """图标按钮占 3 格（空格 + 图标 + 空格）；带字的按钮按字宽，两边各留一格。"""
    return max(SLOT, R.cols(text) + 2)


def buttons_line(buttons, u=None):
    cells = []
    for text, act, extra in buttons:
        w = _cell_w(text)
        pad = (w - R.cols(text)) // 2
        cells.append(" " * pad + text + " " * (w - R.cols(text) - pad)
                     + R.fit(R.usage_state(u, time.time()) if act == "usage" else "", extra))
    return (" " * GAP).join(cells)


def button_at(buttons, col):
    x = 1
    for text, act, extra in buttons:
        w = _cell_w(text) + extra
        if x <= col < x + w:
            return act
        x += w + GAP
    return None


def short_model(model):
    """`Opus 5.5/high` → `Opus5.5/hi`：窄版一行只有十来列。"""
    name, _, effort = (model or "").partition("/")
    return name.replace(" ", "") + (f"/{effort[:2]}" if effort else "")


def sh_out(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


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


# ---- 读屏 / 发键（给 core.jump 用）-------------------------------------------------

class HerdrTerm:
    # herdr 的 send-keys 不认 Home / End / PageUp / PageDown（报 invalid_key），直接发终端的转义序列
    KEYS = {"C-Home": "\x1b[1;5H", "C-End": "\x1b[1;5F", "PageUp": "\x1b[5~", "PageDown": "\x1b[6~"}

    def __init__(self, pane):
        self.pane = pane
        info = pane_info(pane) or {}
        self.height = (info.get("scroll") or {}).get("viewport_rows") or 40

    def screen(self):
        return call("pane.read", pane_id=self.pane, source="visible")["read"]["text"]

    def send(self, *keys):
        """所有按键拼成一次 send_text。keys 里不在 KEYS 表里的原样发（滚轮的鼠标序列）。"""
        call("pane.send_text", pane_id=self.pane, text="".join(self.KEYS.get(k, k) for k in keys))


# ---- 轨迹本体 --------------------------------------------------------------------

class Sidebar:
    def __init__(self, target, me):
        self.target, self.me = target, me
        RUN.mkdir(parents=True, exist_ok=True)
        base = RUN / me.replace(":", "_")
        self.reg, self.fifo = base.with_suffix(".json"), base.with_suffix(".fifo")
        self.sock, self.rows_file = base.with_suffix(".sock"), base.with_suffix(".rows")
        self.sid = self.path = self.tr = None
        self.cfg_dir, self.status = claude.DEFAULT_CFG, None
        self.opened, self.expanded = None, False
        self.missing = set()
        self.pushed = (None, None)            # 上次推给 fzf 的 (列表, 表头)
        self.flash = None                     # (一句提示, 到什么时候为止)
        self.mc = (None, ("", ""))            # (对话记录大小, model_ctx)：没变就不重读尾巴
        self.due = []                         # 预约的补刷时间
        self.usage_kick = 0.0
        self.jump_token = 0
        self.proc, self.wide = None, None     # self.wide：宽版上次的列数，从窄版切回去时还原
        self.mode = None                      # "wide" / "narrow"：按窗格实际宽度定（mode_for），不是按钮切的
        self.no_claude_since = None           # 这一格里从什么时候起不是 Claude 了
        self.tool = None                      # (工具窗格, 第几个按钮)：开着的 lazygit / yazi 之类
        self.tool_check = None                # 焦点离开工具窗格的时刻：0.4 秒后还不在它身上就关
        self.wake_r, self.wake_w = os.pipe()  # 跳转线程 / 额度线程 / 信号 叫醒主循环
        os.set_blocking(self.wake_w, False)

    # -- 状态 --

    def refresh(self):
        """问 herdr 这个 Claude 现在什么状态、跑的哪个会话，读对话记录新追加的部分。
        返回 False = Claude 那一格已经没了。"""
        info = pane_info(self.target)
        if info is None:
            return False
        self.status = info.get("agent_status")
        if reader_for(info.get("agent")):
            self.no_claude_since = None
        elif self.no_claude_since is None:    # Claude 退出了（切账号重启那几秒也会这样，所以等一会儿再关）
            self.no_claude_since = time.monotonic()
            self.later(GONE_GRACE + 0.5)
        self.name_tab(info)
        sid = session_id(info)
        if sid != self.sid:                   # /clear、/resume 换了会话
            self.sid, self.path, self.tr = sid, None, None
            self.opened, self.expanded, self.mc = None, False, (None, ("", ""))
            self.missing = set()
            if sid:
                self.cfg_dir = claude.config_dir_of_pid(claude_pid(self.target))
        if self.sid and self.path is None:    # 新会话第一条消息之前还没有文件，下次再找
            self.path = claude.transcript_path(self.sid, self.cfg_dir)
            if self.path:
                self.tr = reader_for(info.get("agent"))(self.path)
        if self.tr:
            self.tr.update()
        return True

    def busy(self):
        return self.status in ("working", "blocked")

    def name_tab(self, info):
        """用 Claude 的会话名（终端标题，Claude 会按话题自己改）给 tab 命名，分不清哪个 tab 是哪个 Claude 时有用。

        只动「没被你手动起过名」的 tab：label 还是 herdr 默认的编号，或者还是我们上次起的那个名字。
        你手动改过之后两样都不是，就再也不碰它。标题没变就不问 herdr，refresh 里每次调也不多花一个请求。"""
        title = (info.get("terminal_title_stripped") or "").strip()
        tab = info.get("tab_id")
        if not CFG["tab_names"] or not title or not tab or title == getattr(self, "_titled", None):
            return
        self._titled = title
        name = R.clip(title, TAB_NAME_MAX)
        if name != title:
            name = R.clip(title, TAB_NAME_MAX - 1) + "…"
        try:
            t = call("tab.get", tab_id=tab)["tab"]
            ours = json.loads(TAB_NAMES.read_text()) if TAB_NAMES.exists() else {}
            label = t.get("label") or ""
            if label == name or (label != str(t.get("number")) and label != ours.get(tab)):
                return
            call("tab.rename", tab_id=tab, label=name)
            ours[tab] = name
            TAB_NAMES.write_text(json.dumps(ours, ensure_ascii=False))
        except (OSError, ValueError, KeyError, HerdrError):
            pass

    def stops(self):
        return self.tr.visible() if self.tr else []

    def kick_usage(self, force=False):
        """（live_usage 打开时）额度旧了（默认 30 分钟）就在后台线程里去查一次；core.claude.refresh_usage 自己还会再节流一道
        （同一个账号的几个轨迹用文件锁串起来，失败的也算），这里多叫一次不会多发请求。"""
        if not claude.LIVE:
            if force:                         # 点了刷新：读一下最新的缓存，顺便说怎么打开实时查询
                self.say(_t("额度来自 Claude Code 的缓存；要实时查询，在配置里设 live_usage = true"), 8)
            return
        if not force and (not claude.usage_stale(self.cfg_dir) or time.monotonic() - self.usage_kick < 60):
            return
        self.usage_kick = time.monotonic()
        cfg = self.cfg_dir

        def work():
            try:
                claude.refresh_usage(cfg, force)
            except OSError:
                pass
            os.write(self.wake_w, b"u")       # 查完了：表头跟上
        threading.Thread(target=work, daemon=True).start()
        self.later(0.3)                       # 先画出「查询中…」

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
                self.mc = (size, claude.model_ctx(self.path))
        return self.mc[1]

    def header(self, width):
        return self.narrow_header(width) if self.mode == "narrow" else self.wide_header(width)

    def narrow_header(self, width):
        """窄版表头（自上而下）：拉回宽版的 « / 模型 / 账号 / ctx、5h、7d 百分比，下面那条边框就是分隔线。
        平时灰，快到上限才上色，规则和宽版的柱子一样。放不下的按 账号 → 模型 → 7d 的顺序丢。
        on_command 按行号认：第 1 行切换，带百分比的那几行点了查一次额度。"""
        room = max(4, width - 2)                       # fzf 表头左边留 2 列
        flash = f" {R.WARN_C}!{R.RESET}" if self.flash and time.monotonic() < self.flash[1] else ""
        # 不画 Claude 的状态（工作中 / 等你 / 完成）：herdr 左侧的 Agents 栏已经有了，只显示 herdr 没有的（用户定的）
        lines = [f" {R.BOLD}{icon('wide')}{R.RESET}{flash}"]    # « 在第 4 列，和宽版的 » 同一个位置（fzf 表头左边留 2 列）
        model, ctx = self.model_ctx()
        acct = claude.short_account(self.cfg_dir)
        u = claude.read_usage(self.cfg_dir)
        fields = []                                    # (优先级：数字小的先丢, 行)

        def tagged(name, text):                        # 文字档没有标记，前面就别留空格
            return f"{R.GREY}{icon(name) + ' ' if icon(name) else ''}{text}{R.RESET}"
        if model:
            fields.append((2, tagged("model", short_model(model))))
        if acct:
            fields.append((1, tagged("account", acct)))
        lab_w = max(R.cols(icon(x)) for x in ("ctx", "5h", "7d"))   # 文字档 ctx 比 5h 宽一格，对齐百分比
        c = R.ctx_pct(ctx, claude.auto_compact_at() or claude.CTX_HARD)
        if c:
            fields.append((9, f"{R.GREY}{icon('ctx').ljust(lab_w)}{R.RESET} {c[1]}{c[0]:3d}%{R.RESET}"))
        for label, key, prio in (("5h", "five_hour", 9), ("7d", "seven_day", 3)):
            q = R.quota_pct(u, label, key)
            if q:
                fields.append((prio, f"{R.GREY}{icon(label).ljust(lab_w)}{R.RESET} {q[1]}{q[0]:3.0f}%{R.RESET}"))
        # 宽度不够整行放下的字段，按优先级丢（账号 → 模型 → 7d），剩下的照原顺序排
        for prio in (1, 2, 3):
            fields = [(p, ln) for p, ln in fields if p != prio or R.cols(ln) <= room]
        lines += [ln for _, ln in fields]
        return "\n".join(R.clip(ln, room) for ln in lines)

    def wide_header(self, width):
        if self.tr is None:
            head = _t("这个窗格里没找到 Claude 会话")
        else:
            stops = self.stops()
            gone_n = sum(1 for st in stops if st["compacted"])
            head = (_t("{gone} 轮已压缩 · 当前 {n} 轮").format(gone=gone_n, n=len(stops) - gone_n) if gone_n
                    else _t("{n} 轮").format(n=len(stops)))
        if self.flash and time.monotonic() < self.flash[1]:
            head += f" · {R.WARN_C}{self.flash[0]}{R.RESET}"
        u = claude.read_usage(self.cfg_dir)
        model, ctx = self.model_ctx()
        lines = R.status_lines(width, model, ctx, u, claude.current_account(self.cfg_dir),
                               claude.auto_compact_at(), claude.CTX_HARD)
        return "\n".join([buttons_line(wide_buttons(), u), head, *lines])

    def rows(self, width):
        if not self.tr:
            return ""
        missing = frozenset(self.missing)
        if self.mode == "narrow":
            return R.narrow_rows_of(self.stops(), self.busy(), width, self.expanded, missing)
        return R.rows_of(self.stops(), self.busy(), self.opened, width, self.expanded, (), missing)

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
            if sid in (R.FOLD, R.UNFOLD):      # 「已压缩」那一行：只在轨迹里展开 / 收起
                self.expanded = sid == R.FOLD
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
                elif int(ln) >= 2:             # 模型、账号、百分比那几行：查一次额度
                    self.kick_usage(force=True)
                return
            if int(ln) >= 3:                   # 宽版的模型 / ctx / 5h / 7d 那几行：去查一次真实额度
                self.kick_usage(force=True)
                return
            act = button_at(wide_buttons(), int(col)) if ln == "1" else None
            if act:
                self.act(act)
        elif parts[0] == "foot" and len(parts) > 2 and parts[2].isdigit():   # 底部的自定义按钮
            act = button_at(footer_buttons(), int(parts[2]))
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
            HerdrTerm(self.target).send("C-End")
            fzf(self.sock, "last")
        elif act in ("wide", "narrow"):
            self.set_mode(act)
        elif act.startswith("tool:"):
            self.run_tool(CFG["buttons"][int(act[5:])])

    def run_tool(self, button):
        """配置里的按钮。command：在 Claude 那一格的目录里开本插件的浮窗（manifest 的 tool 窗格），经 sh -c 跑它；
        action：调别的 herdr 插件的动作，先把焦点交回 Claude（点轨迹时焦点到了轨迹上，对方插件按焦点干活）。"""
        if button.get("action") and not button.get("command"):
            action = button["action"]
            try:
                call("pane.focus", pane_id=self.target)
                call("plugin.action.invoke", action_id=action,
                     context={"focused_pane_id": self.target, "invocation_source": PLUGIN_ID})
            except (OSError, ValueError, HerdrError) as e:
                self.say(f"{action}: {e}")
            return
        index = CFG["buttons"].index(button)
        if self.tool:                         # 再点同一个按钮 = 关；点另一个 = 换过去
            same = self.tool[1] == index
            self.close_tool()
            if same:
                return
        cmd = button["command"]
        try:
            prog = shlex.split(cmd)[0]
        except (ValueError, IndexError):
            prog = cmd
        if not shutil.which(prog):
            self.say(_t("找不到 {p}").format(p=prog))
            return
        cwd = (pane_info(self.target) or {}).get("foreground_cwd") or ""
        env = {"STATETRAIL_CWD": cwd, "STATETRAIL_CMD": cmd, "PATH": os.environ.get("PATH", "")}
        if prog == "lazygit":                 # 在最外层按 Esc 也退出，和 yazi 一样（叠加在你自己的配置后面）
            mine = Path(sh_out("lazygit", "--print-config-dir").strip() or "/nonexistent") / "config.yml"
            env["LG_CONFIG_FILE"] = ",".join([*([str(mine)] if mine.exists() else []), str(HERE / "tools" / "lazygit.yml")])
        # 开在 Claude 右边、占 Claude 那一块的 TOOL_SHARE，左边的 Claude 还看得见；点外面（焦点挪走）就关。
        # 不用 herdr 的 popup：它只能居中、是整个会话的模态窗口，盖住 Claude、点外面也不关（用户 2026-10-25 报的）。
        # herdr 0.9.5 没有 tmux 那种不挤别人的浮动窗格，所以 Claude 会暂时变窄、重新换行。
        try:
            r = call("plugin.pane.open", plugin_id=PLUGIN_ID, entrypoint="tool", placement="split",
                     target_pane_id=self.target, direction="right", focus=True, env=env)
            pane = r["plugin_pane"]["pane"]["pane_id"]
            call("pane.resize", pane_id=pane, direction="left", amount=TOOL_SHARE - 0.5)
            self.tool, self.tool_check = (pane, index), None
        except (OSError, ValueError, KeyError, HerdrError) as e:
            self.say(f"{prog}: {e}")

    def close_tool(self):
        if self.tool:
            try:
                call("pane.close", pane_id=self.tool[0])
            except (OSError, ValueError, HerdrError):
                pass
        self.tool, self.tool_check = None, None

    # -- 跳转 --

    def jump(self, stop_id, token):
        t0 = time.monotonic()
        stops = self.stops()
        n = next((i + 1 for i, s in enumerate(stops) if s["id"] == stop_id), None)
        try:
            found = self._jump(stops, n, token)
        except (OSError, ValueError, KeyError, HerdrError) as e:
            found = f"error {e!r}"
        try:
            with open(JUMP_LOG, "a") as f:
                f.write(json.dumps({"at": time.strftime("%F %T"), "pane": self.target, "sid": self.sid,
                                    "turn": n, "of": len(stops), "result": found,
                                    "secs": round(time.monotonic() - t0, 3)}) + "\n")
        except OSError:
            pass

    def _jump(self, stops, n, token):
        if n is None:
            return "unknown"
        idx, stop_id = n - 1, stops[n - 1]["id"]
        term = HerdrTerm(self.target)
        # agent 还在干的那一轮没有最终回复，拿半截的文字去找会一直对不上：最新的东西本来就在最底下
        if idx == len(stops) - 1 and self.busy():
            term.send("C-End")
            return "busy-bottom"
        if not jump.needle(stops[idx]["reply"]):
            self.say(_t("轨迹：这一轮 Claude 没写文字回复，没法定位"))
            return "no-text"
        if stop_id in self.missing:
            self.say(_t("轨迹：这一轮在主界面里已经看不到了（续接的会话只显示最近一段，或被 /compact 清掉）"))
            return "missing"
        result = jump.jump(term, [s["reply"] for s in stops], idx, lambda: self.jump_token != token)
        if result == "found":
            self.missing.discard(stop_id)
        elif result == "timeout":
            self.say(_t("轨迹：这一轮没找着，先停在这儿了"))
        elif isinstance(result, tuple):       # ("gone", k)：比主界面最上面那一轮更早的都翻不到，一起记下来
            top = result[1]
            self.missing |= {x["id"] for x in stops[:top]} if top is not None else {stop_id}
            self.say(_t("轨迹：主界面里找不到这一轮（续接的会话只显示最近一段，或被 /compact 清掉）"))
            result = "not-found"
        return result

    # -- herdr 事件 --

    def subscribe(self):
        """订阅这个 Claude 的状态变化、窗格关闭。`pane.updated` 不订：它不能按窗格过滤，
        别的插件（比如 herdr-sidebar 每几秒更新一次自己的 token）会一直把这里叫醒。"""
        s = socket.socket(socket.AF_UNIX)
        s.connect(SOCK)
        # pane.focused：工具窗格开着时，点了别处（焦点挪走）就关掉它，和 tmux 版「点浮窗外面就关」一样
        subs = [{"type": "pane.agent_status_changed", "pane_id": self.target},
                {"type": "pane.closed"}, {"type": "pane.exited"}, {"type": "pane.focused"}]
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
        extra = [] if narrow else ["--gap"]   # 窄版：每轮一行，不要项与项之间的空行
        if footer_buttons():                  # 自定义按钮固定在底部，分隔线和上面隔开
            extra += ["--footer", buttons_line(footer_buttons()), "--footer-border=top",
                      "--bind", ("click-footer:execute-silent(printf 'foot %s %s\\n' \"$FZF_CLICK_FOOTER_LINE\" "
                                 f"\"$FZF_CLICK_FOOTER_COLUMN\" >> {fifo})")]
        return [
            "fzf", "--read0", "--ansi", "--no-input", "--no-sort", "--layout=reverse", "--sync",
            "--track", "--id-nth=1", "--delimiter=\t", "--with-nth=2..",
            "--highlight-line", "--info=hidden", "--pointer=▌", "--no-scrollbar", "--ellipsis=…", *extra,
            f"--color={FZF_COLORS}",
            f"--listen={self.sock}", "--header", header, "--header-border=bottom",
            # 打开时落在最后一轮；只这一次，之后刷新列表不把你拽回去
            "--bind", "load:last+unbind(load)",
            # 点一项 / 点表头：写进 FIFO，这个进程马上收到（不用每次起一个 python，也不轮询文件）
            "--bind", f"left-click:execute-silent(printf 'open %s\\n' {{1}} >> {fifo})",
            "--bind", ("click-header:execute-silent(printf 'click %s %s\\n' \"$FZF_CLICK_HEADER_LINE\" "
                       f"\"$FZF_CLICK_HEADER_COLUMN\" >> {fifo})"),
            "--bind", "scroll-up:offset-up+offset-up+offset-up,scroll-down:offset-down+offset-down+offset-down",
            "--bind", "enter:ignore,double-click:ignore,esc:ignore",
        ]

    # -- 宽版 / 窄版 --

    def split_widths(self):
        """(Claude 那一格的宽, 轨迹的宽)；两格就是 open_sidebar 切出来的那一对，宽度加起来是那条分割的宽。"""
        rects = {p["pane_id"]: p["rect"] for p in call("pane.layout", pane_id=self.me)["layout"]["panes"]}
        return rects[self.target]["width"], rects[self.me]["width"]

    def set_width(self, cols):
        """把轨迹调到 cols 列（挪 Claude 和轨迹之间那条线，见 open_sidebar）。herdr 把分割比例限制在
        0.1–0.9（src/layout.rs:295），所以最窄是这条分割的 10%，要更窄它会自己夹住。"""
        # resize 挪的是窗格「那个方向上」的那条边：Claude 的右边、轨迹的左边都是中间这条线。
        # 反过来（Claude 往左、轨迹往右）挪的是外侧的线，会去挤旁边的 herdr-sidebar（实测 32 列挤成 18 列）
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
                # 上次的宽度、width 配置里大的那个，但给 Claude 留够 CLAUDE_MIN；实在放不下就停在窄版不动
                cols = max((self.wide or 0) + FRAME, round((tw + sw) * WIDTH))
                cols = min(cols, tw + sw - CLAUDE_MIN - FRAME)
                if cols >= WIDE_MIN + FRAME:
                    self.set_width(cols)
                else:
                    self.say(_t("放不下宽版：要给 Claude 留 {n} 列").format(n=CLAUDE_MIN))
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
        signal.signal(signal.SIGWINCH, lambda *_: None)   # 窗格宽度变了：按新宽度重画
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
                        if self.tool and name in ("pane_closed", "pane_exited") and data.get("pane_id") == self.tool[0]:
                            self.tool, self.tool_check = None, None   # 在工具里按 q / Esc 退出了，窗格自己关了
                            try:                                      # herdr 会把焦点给隔壁（轨迹），还给 Claude
                                call("pane.focus", pane_id=self.target)
                            except (OSError, ValueError, HerdrError):
                                pass
                        if self.tool and name == "pane_focused" and data.get("pane_id") != self.tool[0]:
                            # 等 0.4 秒再看：焦点还不在工具上才关（照 tmux 版，免得一闪而过的焦点变化把它关了）
                            self.tool_check = time.monotonic() + 0.4
                            self.later(0.45)
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
                if self.tool_check and time.monotonic() >= self.tool_check:
                    self.tool_check = None
                    focused = next((p["pane_id"] for p in (call("pane.list").get("panes") or [])
                                    if p.get("focused") and p.get("tab_id") == (pane_info(self.target) or {}).get("tab_id")), None)
                    if self.tool and focused != self.tool[0]:
                        self.close_tool()
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
            self.close_tool()
            for p in (self.reg, self.fifo, self.sock, self.rows_file, self.rows_file.with_suffix(".tmp")):
                p.unlink(missing_ok=True)
            self.stop_fzf()
            sub.close()
            for fd in (fifo_r, fifo_w):
                os.close(fd)


def cmd_pane():
    target, me = os.environ.get("STATETRAIL_TARGET"), os.environ.get("HERDR_PANE_ID")
    if not target or not me:
        sys.exit("trail.py pane: STATETRAIL_TARGET / HERDR_PANE_ID not set")
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
    miss = missing_deps()
    if miss:                                   # 窗格里直接写出来，停住等你看，别一闪就没了
        print(_t("statetrail 缺依赖：") + "\n")
        for m, how in miss:
            print(f"  • {m}\n    {how}\n")
        print(_t("装好以后关掉这个窗格，再打开一次。"))
        signal.pause()
        return
    Sidebar(target, me).run()


# ---- 开 / 切 ---------------------------------------------------------------------

def sidebars():
    """{轨迹窗格: Claude 窗格}，只留还活着的。"""
    try:
        alive = {p["pane_id"] for p in call("pane.list")["panes"]}
    except (OSError, ValueError, KeyError, HerdrError):
        return {}
    out = {}
    RUN.mkdir(parents=True, exist_ok=True)    # 新装的还没有这个目录：以前在这里直接崩，自动打开永远不生效
    for f in RUN.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            out[d["sidebar"]] = d["target"]
        except (OSError, ValueError, KeyError):
            continue
    out = {sb: tg for sb, tg in out.items() if sb in alive}
    # 轨迹被强杀、没来得及收拾：窗格已经不在、放了 10 秒以上的文件都删掉
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
             target_pane_id=target, direction="right", focus=False, env={"STATETRAIL_TARGET": target})
    pane = r["plugin_pane"]["pane"]["pane_id"]
    # 轨迹进程起来要零点几秒，它写登记之前紧跟着来的第二个事件会以为还没开、再开一个：这里先写上
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / f"{pane.replace(':', '_')}.json").write_text(json.dumps({"sidebar": pane, "target": target}))
    # plugin.pane.open 没有比例参数，开出来是对半分；把中间那条线往右推到 66 / 34（默认窄版就推到底，
    # herdr 会夹在 0.9）。要对 Claude 那一格做：对轨迹做 resize right 挪的是轨迹**右边**那条线，
    # 右边还有别的窗格（比如 herdr-sidebar 停在右边）时会去挤它（实测把它从 32 列挤到 18 列）
    ratio = 0.1 if CFG["default_mode"] == "narrow" else wide_ratio(target, pane)
    call("pane.resize", pane_id=target, direction="right", amount=0.5 - ratio)
    return pane


def wide_cols(total):
    """宽版的列数（窗格矩形）：width 那么宽，但给 Claude 留够 CLAUDE_MIN 列；剩下的放不下宽版就是 0（用窄版）。"""
    cols = min(round(total * WIDTH), total - CLAUDE_MIN - FRAME)
    return cols if cols >= WIDE_MIN + FRAME else 0


def wide_ratio(target, pane):
    """刚切出来对半分的那一对窗格：宽版占这条分割的比例；放不下宽版就 0.1（夹到最窄，也就是窄版）。"""
    try:
        rects = {p["pane_id"]: p["rect"] for p in call("pane.layout", pane_id=target)["layout"]["panes"]}
        total = rects[target]["width"] + rects[pane]["width"]
    except (OSError, ValueError, KeyError, HerdrError):
        return WIDTH
    cols = wide_cols(total)
    return cols / total if cols else 0.1


def cmd_toggle():
    ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    focused = ctx.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID")
    if len(sys.argv) > 2:
        focused = sys.argv[2]
    if not focused or warn_deps():
        return
    with locked():
        bars = sidebars()
        for sb, tg in bars.items():
            if focused in (sb, tg):                   # 焦点在轨迹上、或在开着轨迹的 Claude 上：拉窄 / 拉宽
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
    轨迹没有「关闭」只有窄版，所以不用记谁被手动关过：被 herdr 的 Close pane 关掉的，下次切过来照样开。"""
    if not CFG["auto_open"] or warn_deps(quiet=True):
        return
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
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
        claudes = [p for p in panes if reader_for(p.get("agent"))]
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
    """开 / 切的判断和动作要串行：焦点一变会连着来好几个事件，各起一个进程。"""
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
