#!/usr/bin/env python3
"""trail_daemon.py — 所有轨迹侧栏共用的一个常驻进程（launchd 拉起，崩了自动重启）。

以前每个侧栏自己带一个 bash 循环，每 2 秒起一次 python 把整份对话记录从头读一遍算指纹：
最大的对话记录 150MB，一次 0.28 秒，开十个侧栏就是十份这样的循环。现在：

- 一个进程管所有侧栏，每秒一轮：一次 `tmux list-panes -a`，对每份对话记录只 stat 一下，
  变长了才读**新追加的那几行**（记住读到哪了），列表看起来真的不一样才推给 fzf。
- 侧栏窗格里只剩 fzf 本身（exec，没有 bash 外壳）；刷新由这里通过 fzf 的 --listen 端口推过去。
- 侧栏不会因为一次 tmux 调用失败就被关掉：只有 list-panes 成功、而且 Claude 窗格确实没了，才关。
  侧栏自己意外退出了，下一轮会重新开（除非是你点了关闭）。
- 默认打开：Claude 独占一个窗口、窗口够宽、你正看着这个窗口时，自动在右边开侧栏。
  你点了关闭（状态栏「轨迹」或表头 [关闭]）会在那个 Claude 窗格上记 @trail_off，就不再自动开；
  再点「轨迹」打开，标记清掉。`tmux set -g @trail_auto 0` 整体关掉自动打开。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claude_trail as ct  # noqa: E402
from i18n import t as _t  # noqa: E402  claude_trail 已经把 lib/ 放进 sys.path；常驻进程，改语言后要重启（sidebar/install.sh）

RUN = Path.home() / ".local" / "state" / "claude-trail" / "run"   # <侧栏窗格>.rows / .port
TICK = 1.0               # 整轮检查的间隔
POLL = 0.1               # 两轮之间每 0.1 秒看一眼有没有新的点击（只 stat 几个小文件）
PROMPT_MAX = 4000        # 展开时最多显示你那句话的多少个字
MIN_WIDTH = 120          # Claude 窗格至少这么宽才自动开侧栏
WIDTH = "34%"
CLAUDE_CMD = re.compile(r"^\d+\.\d+\.\d+$")  # Claude Code 的进程名是版本号
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
TMUX = shutil.which("tmux") or "tmux"
SIDEBAR = HERE / "trail-sidebar.sh"
CYAN = "\x1b[36m"
FMT = "\t".join(["#{pane_id}", "#{@trail_for}", "#{window_id}", "#{window_panes}", "#{pane_width}",
                 "#{pane_current_command}", "#{@trail_off}", "#{window_active}", "#{session_attached}",
                 "#{@trail_auto}", "#{pane_floating_flag}", "#{@picker_float}", "#{@trail_shell}",
                 "#{@trail_shell_height}", "#{@claude_model}", "#{@claude_ctx}", "#{@claude_bar}",
                 "#{@claude_config}", "#{@resurrect_skip}", "#{pane_current_path}"])  # 用户选项在格式里会一路往上找到全局的，省一次 tmux show
# 目录放最后一格：按前面的制表符切，目录名里就算有制表符也不会错位
GIT_TTL = 10             # 秒：每个目录多久查一次 git status（侧栏 git 图标后面的数字）
SHELL_H = "8"            # 侧栏上面那条命令行的行数，@trail_shell_height 可改，0 = 不开


def tmux(*args):
    try:
        r = subprocess.run([TMUX, *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


class Transcript:
    """一份对话记录的增量解析：和 claude_trail.parse_session 同样的切轮规则，但只读新追加的行，
    每轮只留侧栏要显示的东西（不留全文），十几份大对话记录常驻也占不了多少内存。"""

    def __init__(self, path):
        self.path, self.pos, self.size = path, 0, -1
        self.stops, self.title, self.cur = [], None, None

    def update(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size == self.size:
            return False
        if size < self.pos:                       # 被截断/重写了：从头来
            self.__init__(self.path)
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        end = data.rfind(b"\n") + 1               # 最后一行可能还没写完，留到下次
        self.pos += end
        self.size = size
        for line in data[:end].decode("utf-8", "replace").splitlines():
            try:
                self.feed(json.loads(line))
            except ValueError:
                continue
        return end > 0

    def feed(self, e):
        t = e.get("type")
        if t == "ai-title":
            self.title = e.get("aiTitle") or self.title
            return
        if t == "system" and e.get("subtype") == "compact_boundary":
            for st in self.stops:
                st["compacted"] = True
            return
        trig = ct._trigger(e)
        if trig:
            self.cur = {"kind": trig[0], "prompt": trig[1][:PROMPT_MAX], "ts": e.get("timestamp"),
                        "id": e.get("uuid"), "reply": "", "has_text": False, "compacted": False}
            self.stops.append(self.cur)
            return
        if t == "assistant" and not e.get("isSidechain") and self.cur is not None:
            txt = ct._text_of((e.get("message") or {}).get("content")).strip()
            if txt:
                self.cur["has_text"] = True
                self.cur["reply"] = ct._first_line(txt)[:200]

    def visible(self):
        return [s for s in self.stops if s["has_text"] or s["kind"] == "human"]


def wrap(text, width):
    """按显示宽度（中文算两格）折行，给展开的那一项用；fzf 本身不折行。"""
    lines = []
    for para in text.splitlines() or [""]:
        cur, w = "", 0
        for ch in para:
            cw = ct._cols(ch)
            if w + cw > width:
                lines.append(cur)
                cur, w = "", 0
            cur += ch
            w += cw
        lines.append(cur)
    return lines


FOLD, UNFOLD = "fold", "unfold"   # 折叠行的 id：点「展开」写 fold，点「收起」写 unfold


def rows_of(tr, busy, opened=None, width=40, expanded=False, here=(), missing=frozenset()):
    """opened = 点开的那一轮：它的「你说的话」整段展开（主界面那边跳到 Claude 的最终回复）。

    /compact 之前的轮次收成最上面的一行：主界面已经跳不过去了，几百轮没压缩的会话
    压完以后，列表里几乎全是点了也没用的项。expanded 时照常列出来，那一行变成「收起」。
    """
    out = []
    stops = tr.visible()
    gone_n = sum(1 for st in stops if st["compacted"])
    if gone_n:
        span = f"#1–#{gone_n}" if gone_n > 1 else "#1"
        act = _t("点这里收起") if expanded else _t("点开展开")
        out.append(f"{UNFOLD if expanded else FOLD}\t{ct.DIM}{_t('{span} 已压缩（{n} 轮）· {act}').format(span=span, n=gone_n, act=act)}{ct.RESET}")
    for n, st in enumerate(stops, 1):
        if st["compacted"] and not expanded:
            continue
        ask = ct._oneline(st["prompt"])[:300] if st["kind"] == "human" else _t("（{p}）").format(p=st['prompt'])
        if busy and n == len(stops):
            reply = _t("（Claude 还在干活…）")
        else:
            reply = st["reply"] or _t("（没有文字回复）")
        gone = " · " + _t("已压缩") if st["compacted"] else ""
        if st["id"] in missing:
            gone += " · " + _t("主界面已无")
        # 最左一列：主界面屏幕上正显示着的轮次画一条亮边（相当于滚动条的滑块），其它留空
        mark = f"{CYAN}┃{ct.RESET}" if n - 1 in here else " "
        meta = f"{mark}{ct.DIM}#{n} {ct._when(st['ts'])}{gone}{ct.RESET}"
        if st["id"] in missing:
            ask = f"{ct.DIM}{ask}{ct.RESET}"
        if st["id"] == opened and st["kind"] == "human":
            body = "\n".join(" " + mark + " " + ln for ln in wrap(st["prompt"].strip(), max(10, width - 4)))
            out.append(f"{st['id']}\t{meta} {ct.BOLD}{_t('你说的：')}{ct.RESET}\n{body}\n {mark} {ct.DIM}{reply}{ct.RESET}")
            continue
        out.append(f"{st['id']}\t{meta} {ask}\n {mark} {ct.DIM}{reply}{ct.RESET}")
    return "\0".join(out)


def send(port, act):
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}", data=act.encode(),
                                                      method="POST"), timeout=1).read()
        return True
    except OSError:
        return False                              # fzf 还没起来或刚退出，下一轮再说


def header_of(tr, path, extra=(), usage=None, view=None, git_n=None, cfg="", sw=None):
    """第 1 行轮数，第 2 行图标按钮 + 刷新状态，后面跟 status_lines 的模型和额度几行。
    claude_trail.cmd_click 按行号认按钮，挪行要两边一起改。"""
    if tr is None:
        head = _t("这个窗格里没找到 Claude 会话")
    else:
        # 不写会话标题：侧栏就贴在那个 Claude 旁边，标题 tmux 的窗口名上已经有了
        # （2026-10-08 用户：「第一行的那个 claude name 也没有必要，因为就这一个 name，
        # 在 tmux 上已经显示了，没必要再显示一次」）。
        stops = tr.visible()
        gone_n = sum(1 for st in stops if st["compacted"])
        head = (_t("{gone} 轮已压缩 · 当前 {n} 轮").format(gone=gone_n, n=len(stops) - gone_n) if gone_n
                else _t("{n} 轮").format(n=len(stops)))
    if tr is not None and view:
        here, bottom = view
        # 在最底是常态，写出来没有信息量（2026-10-23 用户：「主界面在最底这句话没有任何意义」）；
        # 只在主界面往上翻了的时候说停在哪几轮
        if not bottom and here:
            head += " · " + _t("主界面在 {where}").format(
                where=f"#{here[0] + 1}" + (f"–#{here[-1] + 1}" if here[-1] != here[0] else ""))
    return "\n".join([head, ct.buttons_line(usage, git_n, cfg, sw), *extra])


class Daemon:
    def __init__(self):
        RUN.mkdir(parents=True, exist_ok=True)
        self.trs = {}          # 对话记录路径 → Transcript
        self.paths = {}        # session_id → 路径（glob 一次就记住）
        self.pushed = {}       # 侧栏窗格 → (端口, 列表内容, 表头)
        self.not_claude = {}   # Claude 窗格 → 连续几轮不是 Claude 了
        self.status, self.status_mtime = {}, None
        self.want_last = {}
        self.expanded = {}     # 侧栏窗格 → 已压缩的轮次展开着没有
        self.crowded = {}
        self.keys = {}         # 侧栏窗格 → 上次推送时的输入，没变就跳过
        self.needles, self.views = {}, {}   # Claude 窗格 → 认位置用的针 / 主界面上次停的位置
        self.bars = {}         # Claude 窗格 → (@claude_model, @claude_ctx, @claude_bar, @claude_config)
        self.cwds = {}         # 窗格 → 当前目录
        self.git = {}          # 目录 → (查的时间, 没提交的文件数；不是仓库 / 查不了是 None)
        self.usage, self.usage_mtime = {}, {}       # 配置目录 → 额度缓存 / 文件 mtime，没动就不重读

    def read_usage(self, cfgs):
        """额度是 usage-refresh.py 写的一个小 json，半小时才变一次：stat 一下，变了才读。

        一个配置目录一份：两个账号可以同时在线（claude / claude2），额度各算各的，所以按
        当前开着的那些 Claude 窗格的 @claude_config 分别读、分别刷。

        顺便负责「旧了就去刷一次」。这件事本来是底栏那段 #(status-badge.sh quota) 顺手做的
        （它每 10 秒渲染一次，顺便看一眼 fetched_at_ms 有多老），但现在有侧栏的窗口底栏
        不画那一段，#() 也就不跑了 —— 全部窗口都开着侧栏时就再没人去刷。判据和阈值跟
        status-badge.sh 的 maybe_refresh 一样（默认 30 分钟），usage-refresh.py 自己还会
        再节流一道（在飞的请求也算），所以这里多叫一次不会多发一个请求。
        """
        for cfg in cfgs:
            f = ct.cfg_files(cfg)[0]
            try:
                m = f.stat().st_mtime
                if m != self.usage_mtime.get(cfg):
                    self.usage[cfg], self.usage_mtime[cfg] = ct.read_usage(cfg), m
            except OSError:
                self.usage.pop(cfg, None)
                self.usage_mtime.pop(cfg, None)
        for gone in set(self.usage) - set(cfgs):   # 那个账号的 Claude 都关了，别留着
            self.usage.pop(gone, None)
            self.usage_mtime.pop(gone, None)
        interval = 60 * int(os.environ.get("CLAUDE_TMUX_USAGE_REFRESH_MIN") or 30)
        # 这里不自己再节流一层：usage-refresh.py 把「在飞的请求」也算进它自己的节流里，
        # status-badge.sh 就是靠这个每 10 秒喊一次也只发一个请求。多加一层只会在它那边
        # 刚好拒掉一次之后，把下一次正经的刷新也压住半小时。
        if not ct.USAGE_REFRESH.exists():
            return
        for cfg in cfgs:
            if time.time() - (self.usage.get(cfg) or {}).get("fetched_at_ms", 0) / 1000 <= interval:
                continue
            try:
                subprocess.Popen([sys.executable, str(ct.USAGE_REFRESH), "--config", cfg or str(ct.DEFAULT_CFG)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                pass

    def read_status(self):
        try:
            m = ct.STATUS_FILE.stat().st_mtime
            if m != self.status_mtime:
                self.status, self.status_mtime = json.loads(ct.STATUS_FILE.read_text()), m
        except (OSError, ValueError):
            pass

    def transcript(self, pane):
        sid = (self.status.get(pane) or {}).get("session_id")
        if not sid:
            return None, None
        p = self.paths.get(sid)
        if p is None or not p.exists():
            hits = sorted(ct.PROJECTS.glob(f"*/{sid}.jsonl"), key=lambda x: x.stat().st_mtime)
            if not hits:
                return None, None
            p = self.paths[sid] = hits[-1]
        tr = self.trs.get(p)
        if tr is None:
            tr = self.trs[p] = Transcript(p)
        tr.update()
        return tr, p

    def view(self, caller, tr):
        """主界面现在停在哪：(屏幕上认出来的轮次下标, 是不是在最底)。。

        认法和跳转一样：每轮开头那几个字（针）去屏幕上找。认不出任何一轮时沿用上一次的位置
        （屏幕上一屏全是长代码块、表格时会这样），不然滑块会一闪一闪地跳。
        """
        if tr is None:
            return (), False
        stops = tr.visible()
        key = (tr.size, len(stops))
        cached = self.needles.get(caller)
        if not cached or cached[0] != key:
            cached = self.needles[caller] = (key, ct.needles_of([{"final": st["reply"]} for st in stops]))
        screen = tmux("capture-pane", "-p", "-t", caller)
        if screen is None:
            return self.views.get(caller, ((), False))
        here = tuple(ct.locate(screen, cached[1]))
        bottom = "Jump to bottom" not in screen
        if not here:
            here = self.views.get(caller, ((), False))[0] if not bottom else ()
        self.views[caller] = (here, bottom)
        return here, bottom

    def git_dirty(self, cwd):
        """这个目录有几个文件没提交（git status --porcelain 的行数），GIT_TTL 秒内用上次的结果。
        大仓库一次也就几十毫秒（2026-10-22 实测一个几万文件的仓库 30ms），超过 2 秒算查不了。"""
        if not cwd:
            return None
        hit = self.git.get(cwd)
        if hit and time.monotonic() - hit[0] < GIT_TTL:
            return hit[1]
        try:
            r = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True,
                               text=True, timeout=2)
            n = len(r.stdout.splitlines()) if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            n = None
        self.git[cwd] = (time.monotonic(), n)
        return n

    def opened(self, sidebar):
        try:
            return (RUN / f"{sidebar}.open").read_text().strip()
        except OSError:
            return None

    def push(self, sidebar, caller, width=40):
        tr, path = self.transcript(caller)
        busy = (self.status.get(caller) or {}).get("status") in ("running", "blocked")
        try:
            port = (RUN / f"{sidebar}.port").read_text().strip()
        except OSError:
            port = ""
        opened = self.opened(sidebar)
        # 已压缩那一行展开着没有：只在点了它的时候变，点别的轮次（包括展开出来的那些）不收回去
        if opened in (FOLD, UNFOLD):
            self.expanded[sidebar] = opened == FOLD
        expanded = self.expanded.get(sidebar, False)
        model, ctx, bar, cfg = self.bars.get(caller, ("", "", "", ""))
        # 额度那两行里有「还剩多久重置」，每分钟会变；渲染好了放进 key，变了才重推。
        extra = ct.status_lines(width, model, ctx, self.usage.get(cfg), bar, cfg)
        view = self.view(caller, tr)
        missing = frozenset(ct.missing_ids(caller))
        header = header_of(tr, path, extra, self.usage.get(cfg), view, self.git_dirty(self.cwds.get(caller)),
                           cfg, ct.switch_status(caller))
        # header 要进 key：只有表头变了（改了表头的写法、轮数变了）而列表没变的时候，
        # key 不带它就会被当成「什么都没变」，改完半天不生效，得等下一分钟倒计时跳一下。
        key = (port, path, tr and tr.size, busy, opened, width, expanded, header, view[0], missing)
        if self.keys.get(sidebar) == key:
            return                                # 什么都没变，连列表都不用重新拼
        self.keys[sidebar] = key
        rows = rows_of(tr, busy, opened, width, expanded, view[0], missing) if tr else ""
        last = self.pushed.get(sidebar)
        if not port or last == (port, rows, header):
            return
        rf = RUN / f"{sidebar}.rows"
        if not last or last[1] != rows:
            tmp = rf.with_suffix(".tmp")
            tmp.write_text(rows)
            tmp.replace(rf)
        first = not last or last[0] != port      # fzf 刚起来：落到最后一轮
        # 表头和列表分两次发：`reload-sync(...)+change-header:...` 写在同一条里，fzf 会把后面的
        # change-header 吞掉（2026-10-13 实测：列表换了、表头纹丝不动）。表头先发，列表后发。
        ok = send(port, "change-header:" + header)
        if ok and (not last or last[1] != rows or first):
            ok = send(port, f"reload-sync(cat '{rf}')")
        if not ok:
            self.keys.pop(sidebar, None)          # 没送到，下一轮重来
        else:
            self.pushed[sidebar] = (port, rows, header)
            if first:
                # 落到最后一轮要等列表载完：和 reload 放在同一个请求里，fzf 会先跑 last、
                # 再载入新列表（--track 又把光标拉回第一项）。下一次 0.1 秒的轮询再单独发。
                self.want_last[sidebar] = port

    def open_sidebar(self, caller, shell_h=SHELL_H):
        sp = tmux("split-window", "-h", "-d", "-l", WIDTH, "-t", caller, "-P", "-F", "#{pane_id}",
                  f"exec '{SIDEBAR}' --run {caller}")
        if sp:
            sp = sp.strip()
            tmux("set", "-p", "-t", sp, "@trail_for", caller)
            tmux("set", "-p", "-t", sp, "@nofocus", "1")
            self.open_shell(sp, caller, shell_h)

    def open_shell(self, sidebar, caller, shell_h):
        """侧栏上面切一条命令行（-b，切在上方：侧栏列表点得多，放下面）：要敲 git / 看文件的时候不用离开这个窗口。
        打 @trail_shell（值是 Claude 窗格），这样它不会被当成侧栏，也不会被算进 tiled——
        算进去的话下一轮就当成「队友来了」，刚开的侧栏马上又被收走。不设 @nofocus：点它就是要打字。"""
        if not shell_h.isdigit() or int(shell_h) <= 0:
            return
        args = ["split-window", "-v", "-b", "-d", "-l", shell_h, "-t", sidebar, "-P", "-F", "#{pane_id}"]
        cwd = (tmux("display", "-p", "-t", caller, "#{pane_current_path}") or "").strip()
        if cwd:
            args += ["-c", cwd]
        sh = tmux(*args)
        if sh:
            tmux("set", "-p", "-t", sh.strip(), "@trail_shell", caller)

    def tick(self):
        out = tmux("list-panes", "-a", "-F", FMT)
        if out is None:
            return                                 # tmux 没在跑或一时没回：什么都不动
        self.read_status()
        panes, sidebars, shells, tiled, mates = {}, {}, {}, {}, {}
        self.bars = {}
        self.cwds = {}
        self.widths = {}
        shell_h = SHELL_H
        for line in out.splitlines():
            f = line.split("\t", 19)
            if len(f) < 20:
                continue
            (pid, trail_for, win, npanes, width, cmd, off, wactive, attached, auto, floating, pfloat,
             trail_shell, shell_h, cmodel, cctx, cbar, ccfg, rskip, cwd) = f
            self.cwds[pid] = cwd
            # 侧栏和它上面那条命令行都别让 tmux-resurrect 当普通窗格恢复：恢复出来只是两个空 shell，
            # 窗口里平铺着三格，这里就不会再开真正的侧栏（2026-10-22 用户：「它 restore 不出来轨迹，
            # 但是它打开了对应的 pane……那个 pane 是空的」）。bin/ 下的
            # resurrect_hooks.py 认这个标记：存档时记下坐标，恢复完把这两格关掉，下一轮这里照常重开。
            # 开侧栏的地方有好几处（这里、trail-sidebar.sh、浮窗），统一在这儿补，最多晚一秒。
            if (trail_for or trail_shell) and rskip != "1":
                tmux("set", "-p", "-t", pid, "@resurrect_skip", "1")
            if cmodel or cctx or cbar or ccfg:
                self.bars[pid] = (cmodel, cctx, cbar, ccfg)
            # 窗口里平铺着几个「正经」窗格：侧栏自己、它上面那条命令行、浮窗（picker 那种）不算
            if not trail_for and not trail_shell and floating != "1" and not pfloat:
                tiled[win] = tiled.get(win, 0) + 1
                # 其中有几个是 Claude：收起侧栏只认这个。「平铺窗格多了一个」会把你自己
                # 从命令行里再切一块也算进来，于是手一切侧栏就没了（2026-10-08 用户报：
                # 「右下角的命令行 pane 没有办法再切分，我一切分，轨迹 pane 就消失了」）。
                # 真正挤掉领队的是 agent teams，它开出来的每一格都是 Claude。
                if CLAUDE_CMD.match(cmd):
                    mates[win] = mates.get(win, 0) + 1
            panes[pid] = {"win": win, "n": int(npanes or 0), "w": int(width or 0), "claude": bool(CLAUDE_CMD.match(cmd)),
                          "off": off == "1", "float": floating == "1", "seen": wactive == "1" and attached not in ("", "0")}
            if trail_for:
                sidebars[pid] = trail_for
                self.widths[pid] = int(width or 40)
            if trail_shell:
                shells[pid] = trail_shell
        # 额度按配置目录读，所以要等上面扫完窗格、知道现在开着哪几个账号才能读
        self.read_usage({b[3] for b in self.bars.values()})
        # 侧栏：Claude 窗格没了，或者那里已经好几秒不是 Claude 了，就关掉
        kept = set()
        for sb, caller in sidebars.items():
            c = panes.get(caller)
            gone = c is None
            # 正在切账号：Claude 要退出、在 shell 里重启，中间那几秒不是 Claude，别把侧栏收了
            switching = ct.switch_status(caller).get("state") == "running"
            if c and not c["claude"] and not switching:
                self.not_claude[caller] = self.not_claude.get(caller, 0) + 1
                gone = self.not_claude[caller] >= 5
            elif c:
                self.not_claude.pop(caller, None)
            # Claude Code 的队友（agent teams）在同一个窗口里开了窗格：领队被挤到左边一条，
            # 侧栏再从这一条里切一块就没法看了。先收起来（不记 @trail_off），队友都走了自动回来。
            # 浮着的侧栏（开着队友时从状态栏按钮打开的那种）不挤领队，留着。
            if c and mates.get(c["win"], 0) > 1 and not panes.get(sb, {}).get("float"):
                self.crowded[caller] = self.crowded.get(caller, 0) + 1
                gone = gone or self.crowded[caller] >= 2
            else:
                self.crowded.pop(caller, None)
            if gone:
                tmux("kill-pane", "-t", sb)
                continue
            kept.add(caller)
            self.push(sb, caller, self.widths.get(sb, 40))
        # 命令行跟着侧栏走：侧栏这一轮被关掉、或自己退出去了，上面这条也收掉，别留一个孤窗格
        for sh, caller in shells.items():
            if caller not in kept:
                tmux("kill-pane", "-t", sh)
        self.sidebars = sidebars
        # 默认打开：Claude 独占窗口、够宽、正被看着、没被你关过、知道是哪个会话
        if auto != "0":
            have = set(sidebars.values())
            for pid, p in panes.items():
                if (p["claude"] and pid not in have and tiled.get(p["win"]) == 1 and p["w"] >= MIN_WIDTH and p["seen"]
                        and not p["off"] and (self.status.get(pid) or {}).get("session_id")):
                    self.open_sidebar(pid, shell_h or SHELL_H)
        # 清理：已经没有的侧栏留下的文件、不再有人看的对话记录
        # 只删窗格已经不存在、而且放了一会儿的：这一轮开头的 list-panes 之后才开的侧栏，它的 .port
        # 可能在这一轮中间写进来（第一次读大对话记录要零点几秒），那时它还不在 panes 里
        now = time.time()
        for f in RUN.iterdir():
            try:
                old = now - f.stat().st_mtime > 10
            except OSError:
                continue
            if old and f.name.split(".")[0] not in panes:
                f.unlink(missing_ok=True)
        for sb in list(self.pushed):
            if sb not in sidebars:
                del self.pushed[sb]
                self.keys.pop(sb, None)
                self.expanded.pop(sb, None)
        live = {self.paths.get((self.status.get(c) or {}).get("session_id")) for c in sidebars.values()}
        for p in list(self.trs):
            if p not in live:
                del self.trs[p]

    def clicks(self):
        """点了一项（侧栏写了 <侧栏>.open）就马上重画，不等下一整轮。"""
        for sb, port in list(self.want_last.items()):
            send(port, "last")
            del self.want_last[sb]
        for sb, caller in getattr(self, "sidebars", {}).items():
            try:
                m = (RUN / f"{sb}.open").stat().st_mtime
            except OSError:
                continue
            if self.click_seen.get(sb) != m:
                self.click_seen[sb] = m
                self.push(sb, caller, self.widths.get(sb, 40))

    def run(self):
        self.click_seen = {}
        while True:
            t0 = time.monotonic()
            try:
                self.tick()
            except Exception as e:                 # 一轮出错别让整个进程退出，记下来接着跑
                print(time.strftime("%F %T"), "tick error:", repr(e), flush=True)
            while time.monotonic() - t0 < TICK:
                time.sleep(POLL)
                try:
                    self.clicks()
                except Exception as e:
                    print(time.strftime("%F %T"), "click error:", repr(e), flush=True)


if __name__ == "__main__":
    Daemon().run()
