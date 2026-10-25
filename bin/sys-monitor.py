#!/usr/bin/env python3
"""资源监视浮窗：点 tmux 右下角「CPU 内存 交换 磁盘」那一段弹出（float-tool.sh 的 tool:sys）。

看三件事：谁在吃 CPU、谁在吃内存（按「软件」归类，再列最大的几个进程；claude 会话认出是哪个窗格）、
磁盘一共多大、用了多少、哪些目录最占地方。每 3 秒刷新；q 退出，r 立刻刷新，d 重新统计磁盘目录。
  sys-monitor.py          浮窗里跑（循环刷新）
  sys-monitor.py --only cpu|mem|swap|disk   只显示一项（点右下角对应那一段弹的小窗）
  sys-monitor.py --once   只打印一次（测试用）

内存口径同活动监视器「已使用」（匿名页 + wired + 压缩器），和 tmux 右下角那段 (tmux-sysstat.sh) 一致。
CPU 总数用 iostat（和 top 一致）；各进程的 CPU% 是 ps 给的「近一分钟平均」，加起来会比总数低，只用来排名。
磁盘目录统计要几十秒到几分钟（du 扫整个家目录），所以放后台、结果缓存 30 分钟，没出来前先显示上一次的。
"""
import json
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import threading
import tty
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
from i18n import t as _t, EN  # noqa: E402  界面文字：zh 原样，en 查 lib/en.tsv

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", HOME + "/.local/state"), "sys-monitor")
DU_CACHE = os.path.join(STATE, "du.json")
DU_TTL = 30 * 60

B, D, R, G, Y, C, O = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"


def run(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout).stdout
    except Exception:
        return ""


def gb(b):
    return f"{b / 1073741824:.1f}G" if b >= 1073741824 else f"{b / 1048576:.0f}M"


def sev(v, y, r):   # 值越大越糟：绿 / 橙 / 红
    return R if v >= r else Y if v >= y else G


def pad(s, n):   # 按显示宽度补空格（中文占两格）
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(0, n - w)


def bar(frac, width, color):
    n = max(0, min(width, round(frac * width)))
    return f"{color}{'█' * n}{O}{D}{'░' * (width - n)}{O}"


# —— 软件归类：按进程的可执行路径 / 命令行认 ——
GROUPS = [
    ("claude 会话", re.compile(r"(^|/)claude( |$)|/claude/versions/|\.local/bin/claude|ClaudeCode\.app")),
    ("Chrome", re.compile(r"Google Chrome")),
    ("Colima / Docker 虚拟机", re.compile(r"Virtualization\.VirtualMachine|colima|limactl")),
    ("安卓模拟器", re.compile(r"qemu-system|/emulator/|netsimd")),
    ("TCSAS 开发者工具", re.compile(r"TCSAS-Devtools")),
    ("iTerm", re.compile(r"iTerm")),
    ("WindowServer 窗口合成", re.compile(r"WindowServer")),
    ("编译 (go/clang/swift…)", re.compile(r"/go/|/pkg/tool/|clang|swift-frontend|/link( |$)")),
    ("node / bun", re.compile(r"(^|/)(node|bun)( |$)")),
    ("python", re.compile(r"[Pp]ython")),
    ("tmux / fzf", re.compile(r"(^|/)(tmux|fzf)( |$)")),
    ("Docker 容器进程", re.compile(r"docker|containerd")),
]


def head_of(args):
    """只看「可执行文件那一段」（第一个 ` -选项` 之前）。整条命令行里的文字不算：
    `zsh -c "…qemu-system…"` 这种只是命令里提到名字，不是模拟器本身。"""
    i = args.find(" -")
    return (args[:i] if i > 0 else args)[:240]


def group_of(args):
    h = head_of(args)
    for name, rx in GROUPS:
        if rx.search(h):
            return name
    return "其它"


def processes():
    out = run("ps", "-axo", "pid=,ppid=,rss=,%cpu=,etime=,args=")
    procs = {}
    for ln in out.splitlines():
        p = ln.split(None, 5)
        if len(p) < 6:
            continue
        try:
            procs[int(p[0])] = dict(pid=int(p[0]), ppid=int(p[1]), rss=int(p[2]) * 1024, cpu=float(p[3]),
                                    et=p[4], args=p[5])
        except ValueError:
            pass
    return procs


def pane_names():
    """窗格里进程 → 「session:窗口.窗格 标题」，用来给 claude 进程起名。"""
    out = run("tmux", "list-panes", "-a", "-F", "#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_title}")
    m = {}
    for ln in out.splitlines():
        a = ln.split("\t")
        if len(a) == 3 and a[0].isdigit():
            m[int(a[0])] = f"{a[1]} {a[2].lstrip('✳⠂⠐ ').strip()}"
    return m


def label(proc, procs, panes):
    args = proc["args"]
    if group_of(args) == "claude 会话":
        cur = proc["pid"]
        for _ in range(5):
            if cur in panes:
                return panes[cur]
            cur = procs.get(cur, {}).get("ppid", 0)
        if "daemon" in args or "bg-" in args:
            return _t("claude 后台会话机制（daemon / pty-host）")
        return _t("claude（找不到所在窗格）")
    if group_of(args) == "python":
        m = re.search(r"([\w.\-/]+\.py)", args)
        return "python " + (os.path.basename(m.group(1)) if m else "")
    exe = args.split(" -", 1)[0]
    exe = re.sub(r".*/(Contents/MacOS/|bin/|Helpers/)", "", exe)
    return exe[:60]


def memory():
    vm = run("vm_stat")
    f = lambda k: int(re.search(k + r"[^\d]*(\d+)", vm).group(1)) if re.search(k, vm) else 0
    anon, wired, comp = f(r"Anonymous pages"), f(r"wired down"), f(r"occupied by compressor")
    total = int(run("sysctl", "-n", "hw.memsize") or 0)
    used = (anon + wired + comp) * 16384
    sw = run("sysctl", "-n", "vm.swapusage")
    mm = {k: float(v) * (1024 if u == "G" else 1) for k, v, u in re.findall(r"(total|used|free) = ([\d.]+)([MG])", sw)}
    return dict(total=total, used=used, wired=wired * 16384, comp=comp * 16384,
                swap_used=mm.get("used", 0) * 1048576, swap_total=mm.get("total", 0) * 1048576)


_MEMO = {}


def memo(key, ttl, fn):
    """带过期时间的缓存：按键一次重画不该为了同样的数据再等一遍 iostat / docker / lsof。"""
    now = time.time()
    v = _MEMO.get(key)
    if v and now - v[0] < ttl:
        return v[1]
    r = fn()
    _MEMO[key] = (now, r)
    return r


_CPU = {"v": (None, 0, 0)}


def cpu_bg():
    while True:
        _CPU["v"] = cpu_total_raw()
        time.sleep(2)


def cpu_total():
    return _CPU["v"] if _CPU.get("started") else cpu_total_raw()


def cpu_total_raw():
    out = run("iostat", "-c", "2", "disk0", timeout=6).strip().splitlines()
    try:
        cols = out[-1].split()
        return 100 - float(cols[5]), float(cols[3]), float(cols[4])
    except (IndexError, ValueError):
        return None, 0, 0


# —— 磁盘 ——
def disk_totals():
    out = run("df", "-k", "/System/Volumes/Data").splitlines()
    try:
        p = out[1].split()
        return int(p[1]) * 1024, int(p[2]) * 1024, int(p[3]) * 1024
    except (IndexError, ValueError):
        return 0, 0, 0


def du_load():
    try:
        with open(DU_CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def du_refresh_running():
    return bool(run("pgrep", "-f", "sys-monitor.py --du").strip())


def du_start():
    """后台统计家目录和 Library 下一层的大小；写缓存。nice 到最低，不跟你抢。"""
    if du_refresh_running():
        return
    os.makedirs(STATE, exist_ok=True)
    subprocess.Popen(["nice", "-n", "19", sys.executable, os.path.abspath(__file__), "--du"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def du_job():
    def top(path, n):
        out = run("du", "-xk", "-d", "1", path, timeout=900)
        rows = []
        for ln in out.splitlines():
            k, _, p = ln.partition("\t")
            if k.isdigit() and p != path:
                rows.append((int(k) * 1024, p))
        rows.sort(reverse=True)
        return rows[:n]
    data = dict(ts=time.time(), home=top(HOME, 14), lib=top(HOME + "/Library", 8))
    tmp = DU_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, DU_CACHE)


def docker_df():
    return memo("dockerdf", 30, docker_df_raw)


def docker_df_raw():
    out = run("docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}\t{{.Reclaimable}}", timeout=8)
    return [ln.split("\t") for ln in out.splitlines() if ln.count("\t") == 2]


def short(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def render(width, height, only=None, sel=None, frozen=None, gfilter=None):
    procs, panes = processes(), pane_names()
    mem = memory()
    cpu, us, sy = cpu_total()
    load = os.getloadavg()
    ncpu = os.cpu_count() or 1
    dtot, dused, dfree = disk_totals()
    W = max(60, width - 2)
    L = []
    add = L.append
    rowmap = {}   # 屏幕第几行（从 0 起）→ pid，鼠标点击用
    shown = {}    # 榜单 key → 这一屏列出的 pid 顺序，选中时据此固定排序
    gmap, gorder = {}, []   # 软件归类行：屏幕行 → 软件名（点击筛选）；软件名的显示顺序（←→ / Tab 切换）

    titles = {"cpu": "CPU", "mem": _t("内存"), "swap": _t("内存与交换"), "disk": _t("磁盘")}
    hint = _t("q 退出 · r 刷新") + (" · " + _t("d 重新统计") if only in (None, "disk") else "") + " · " + _t("点外面也会关")
    add(f"{B}{_t('资源监视') if not only else titles[only]}{O}  {D}{time.strftime('%H:%M:%S')} · {hint}{O}")
    add("")
    bw = max(10, min(40, W - (40 if not only else 34)))
    want = lambda *k: only is None or only in k

    # 总览
    pct = mem["used"] / mem["total"] if mem["total"] else 0
    c = sev(cpu or 0, 60, 85)
    if want("cpu"): add(f"{B}⚙ CPU{O}   {bar((cpu or 0) / 100, bw, c)} {c}{(cpu or 0):.0f}%{O}  {D}{_t('用户 {us}% 系统 {sy}% · {n} 核 · 负载 {load}').format(us=format(us, '.0f'), sy=format(sy, '.0f'), n=ncpu, load=format(load[0], '.1f'))}{O}")
    c = sev(pct * 100, 80, 92)
    if want("mem", "swap"): add(f"{B}{pad(_t('▤ 内存'), 6)}{O}  {bar(pct, bw, c)} {c}{gb(mem['used'])}/{gb(mem['total'])}{O}  {D}{_t('其中 wired {wired} · 压缩器 {comp}').format(wired=gb(mem['wired']), comp=gb(mem['comp']))}{O}")
    sw = mem["swap_used"] / mem["swap_total"] if mem["swap_total"] else 0
    c = sev(mem["swap_used"] / 1073741824, 2, 4)
    if want("mem", "swap"): add(f"{B}{pad(_t('⇅ 交换'), 6)}{O}  {bar(sw, bw, c)} {c}{gb(mem['swap_used'])}/{gb(mem['swap_total'])}{O}  {D}{_t('= 拿 SSD 当内存，越多越卡')}{O}")
    c = sev(100 - dfree * 100 / dtot if dtot else 0, 80, 92)
    if want("disk"): add(f"{B}{pad(_t('▣ 磁盘'), 6)}{O}  {bar(dused / dtot if dtot else 0, bw, c)} {c}{_t('已用 {used}').format(used=gb(dused))}{O} / {_t('共 {total} · 剩').format(total=gb(dtot))} {c}{gb(dfree)}{O}  {D}{_t('（数据盘）')}{O}")
    add("")

    def section(title, key, fmt, n_groups, n_procs, total_label):
        by = {}
        for p in procs.values():
            g = group_of(p["args"])
            e = by.setdefault(g, [0, 0, 0.0])
            e[0] += p[key]
            e[1] += 1
            e[2] += p["cpu"]
        total = sum(e[0] for e in by.values()) or 1
        add(f"{B}{_t(title)}{O}  {D}{_t('按软件归类（括号里是进程个数）')}{O}")
        rows = sorted(by.items(), key=lambda kv: -kv[1][0])[:n_groups]
        if not gorder:
            gorder.extend(g for g, _ in rows)
        for g, e in rows:
            share = e[0] / total
            gmap[len(L)] = g
            on = g == gfilter
            mk = f"{B}\033[7m ▶" if on else "  "
            add(f"{mk} {pad(_t(g), 24)}{bar(share, 14, C)} {fmt(e[0]):>8} {D if not on else ''}{share * 100:3.0f}%  {_t('({n} 个)').format(n=e[1])}{O}")
        if gfilter:
            add(f"  {Y}{_t('筛选中：只看「{g}」').format(g=_t(gfilter))}{O}{D}　{_t('再点一次这一行、或按 a，恢复全部')}{O}")
        add("")
        fkey = f"{key}:{gfilter}"
        if frozen and fkey in frozen:
            top = [procs[x] for x in frozen[fkey] if x in procs]   # 选中后排序固定，只刷新数字
            note = _t("（排序已固定，只刷新数字；按 r 重新排序）")
        else:
            if only and height > 0:   # 单项小窗：把剩下的高度都用来列进程
                n_procs = max(n_procs, height - len(L) - 4)
            pool = [p for p in procs.values() if not gfilter or group_of(p["args"]) == gfilter]
            top = sorted(pool, key=lambda p: -p[key])[:n_procs]
            note = _t("（↑↓ 或点击选一个；←→ / Tab 或点上面的软件行来筛选）")
        shown[fkey] = [p["pid"] for p in top]
        add(f"  {B}{_t('最大的进程')}{O} {D}{note}{O}")
        if EN:   # 中文表头按「两个汉字 = 4 格」排的；英文另排一份，对齐同一套列宽
            add(f"{D}   {'CPU':>5} {_t('内存').lower():>7}  {'pid':<6} {_t('已开'):>11}  {pad(_t('软件'), 12)}{_t('名称')}{O}")
        else:
            add(f"{D}   {'CPU':>5} {'     内存'}  {'pid':<6} {'       已开'}  {pad('软件', 12)}名称{O}")
        for p in top:
            nm = label(p, procs, panes)
            rowmap[len(L)] = p["pid"]
            mark = f"{B}\033[7m ▶" if p["pid"] == sel else "  "
            gname = _t(group_of(p["args"])).split(" ")[0][:10]
            add(f"{mark} {p['cpu']:4.0f}% {gb(p['rss']):>7}  {p['pid']:<6} {p['et']:>11}  {pad(gname, 12)}{nm[:max(10, W - 52)]}{O}")
        add("")

    if want("mem", "swap"):
        section("▤ 谁在占内存", "rss", gb, 8, 6, "")
    if want("cpu"):
        section("⚙ 谁在占 CPU", "cpu", lambda v: f"{v:.0f}%", 8, 6, "")
    if not want("disk"):
        return (L[: max(10, height - 1)] if height > 0 else L), rowmap, procs, panes, shown, gmap, gorder

    # 磁盘
    add(f"{B}{_t('▣ 谁在占磁盘')}{O}  {D}{_t('共 {total}，已用 {used}，剩 {free}').format(total=gb(dtot), used=gb(dused), free=gb(dfree))}{O}")
    cache = du_load()
    running = du_refresh_running()
    if cache:
        age = time.time() - cache["ts"]
        add(f"  {D}" + _t("家目录下每一项的大小（统计于 {at}，{state}）").format(
            at=time.strftime('%H:%M', time.localtime(cache['ts'])),
            state=_t('正在后台重新统计…') if running else _t('按 d 重新统计')) + O)
        mx = cache["home"][0][0] if cache["home"] else 1
        for sz, p in cache["home"]:
            add(f"    {gb(sz):>7} {bar(sz / mx, 12, C)} {short(p)}")
        add(f"  {D}{_t('其中 ~/Library 下面：')}{O}")
        mx = cache["lib"][0][0] if cache["lib"] else 1
        for sz, p in cache["lib"]:
            add(f"    {gb(sz):>7} {bar(sz / mx, 12, C)} {short(p)}")
        if age > DU_TTL and not running:
            du_start()
    else:
        add(f"  {Y}{_t('还没统计过，正在后台扫家目录（几十秒到几分钟，扫完自动显示）…')}{O}")
        du_start()
    dk = docker_df()
    if dk:
        add(f"  {D}{_t('Docker（在 Colima 虚拟机的磁盘里，不在上面的家目录统计里单独列出，~/.colima 那一项就是它）：')}{O}")
        for t, size, rec in dk:
            add(f"    {t:<14}{size:>9}   {D}{_t('可回收 {n}').format(n=rec)}{O}")
    return (L[: max(10, height - 1)] if height > 0 else L), rowmap, procs, panes, shown, gmap, gorder


# ---------- 详情 / 关闭 ----------
PROTECTED = re.compile(r"(^|/)(launchd|kernel_task|WindowServer|loginwindow|Finder|Dock|SystemUIServer|"
                       r"sshd|securityd|coreaudiod|bluetoothd|mds|mds_stores|tailscaled|Tailscale|iTerm2|Terminal)( |$)")
ADB = HOME + "/Library/Android/sdk/platform-tools/adb"


def protected(p, procs):
    """不许关的：系统关键进程、tmux 服务端、别人的进程、这个监视器自己和它的上级。"""
    if p["pid"] <= 1:
        return _t("这是系统的 1 号进程")
    exe = p["args"].split(" -", 1)[0]
    if PROTECTED.search(exe):
        return _t("系统关键进程 / 终端本身，关了会让整台电脑或所有终端窗口一起出问题")
    if re.search(r"(^|/)tmux( |$)|tmux: server", p["args"]):
        return _t("这是 tmux 服务端，关了所有 tmux 窗口和里面的会话都没了")
    if run("ps", "-o", "user=", "-p", str(p["pid"])).strip() not in (os.environ.get("USER", ""), ""):
        return _t("不是你的进程（需要管理员权限，这里不做）")
    cur = os.getpid()
    for _ in range(8):
        if cur == p["pid"]:
            return _t("这是这个监视窗口自己（或它的上级）")
        cur = procs.get(cur, {}).get("ppid", 0)
        if cur <= 1:
            break
    return None


def caller_pids(procs):
    """你当前所在窗格（弹出这个浮窗的那个）里的进程号，用来提醒别把自己正在用的会话关了。"""
    me = os.environ.get("TMUX_PANE", "")
    cp = run("tmux", "show", "-pqv", "-t", me, "@picker_caller").strip() if me else ""
    root = run("tmux", "display", "-p", "-t", cp, "#{pane_pid}").strip() if cp else ""
    if not root.isdigit():
        return set()
    out, stack = set(), [int(root)]
    while stack:
        x = stack.pop()
        out.add(x)
        stack += [q["pid"] for q in procs.values() if q["ppid"] == x]
    return out


def kill_plan(p, procs):
    """正规的关法：能让它自己收拾干净的就走正规路径，没有才发 SIGTERM。返回 (说明, 动作, 提醒)。"""
    g, args = group_of(p["args"]), p["args"]
    if g == "安卓模拟器" and "qemu-system" in head_of(args):
        port = (re.search(r"-port (\d+)", args) or [None, "5554"])[1]
        avd = (re.search(r"-avd (\S+)", args) or [None, "?"])[1]
        return (_t("用 adb 让模拟器「{avd}」正常关机（emulator-{port}）").format(avd=avd, port=port), ["adb", f"emulator-{port}"],
                _t("它里面没存的东西会丢；别的会话如果正在测它，会被打断。"))
    if g == "TCSAS 开发者工具":
        return (_t("正常退出 TCSAS 开发者工具"), ["quit-app", "TCSAS-Devtools"],
                _t("它里面没保存的调试现场会丢；别的会话可能正在用它预览或上传，它们下次用时会自己再开。"))
    if g == "Colima / Docker 虚拟机":
        n = memo("dockerps", 15, lambda: run("docker", "ps", "-q", timeout=5).split())
        return (_t("colima stop（正常停掉 Docker 虚拟机）"), ["colima-stop"],
                _t("里面 {n} 个容器会全部停掉（数据在卷里不丢，colima start 后回来）。本地数据库/Redis 都会连不上。").format(n=len(n)))
    if g == "claude 会话":
        return (_t("给 claude 发 SIGTERM（让它自己退出）"), ["signal", 15],
                _t("这个对话会结束；窗格里能用 claude --resume 接回。如果它正在工作，这一轮会丢。"))
    if g == "Chrome":
        return (_t("给这个 Chrome 进程发 SIGTERM"), ["signal", 15],
                _t("只是 Chrome 的一个进程（标签页/插件/GPU），关了对应的标签页会显示「Aw, Snap」，刷新即可。关主进程会关掉整个 Chrome。"))
    return (_t("给这个进程发 SIGTERM（礼貌地请它退出）"), ["signal", 15], _t("没存的东西会丢。它不退再按 X 强制结束（SIGKILL）。"))


def kill_app(name):
    """强杀一个 .app 的全部进程（主进程 + 子进程）。主进程不理会退出和 SIGTERM 时用。"""
    run("pkill", "-KILL", "-f", f"/{name}.app/")
    return _t("退出命令没用，已强制结束 {name} 的全部进程").format(name=name)


def do_kill(p, plan):
    act = plan[1]
    try:
        if act[0] == "adb":
            return run(ADB, "-s", act[1], "emu", "kill", timeout=15).strip() or _t("已发出关机")
        if act[0] == "quit-app":
            run("osascript", "-e", f'tell application "{act[1]}" to quit', timeout=15)
            for _ in range(6):   # nwjs 应用（TCSAS）经常不理会退出命令：等 3 秒还在，就强杀整个应用
                time.sleep(0.5)
                if not run("pgrep", "-f", f"/{act[1]}.app/").strip():
                    return _t("已退出")
            return kill_app(act[1])
        if act[0] == "colima-stop":
            return (run("colima", "stop", timeout=120) or _t("已停止")).strip()[-200:]
        os.kill(p["pid"], act[1])
        return _t("已发送信号 {sig}").format(sig=act[1])
    except ProcessLookupError:
        return _t("进程已经没了")
    except PermissionError:
        return _t("没权限（不是你的进程）")


def alive(pid):
    return bool(run("ps", "-p", str(pid), "-o", "pid=").strip())


def detail(pid, procs, panes, width):
    """一个进程的详情页：返回 (行列表, 提醒/不可关原因)。"""
    p = procs.get(pid)
    if not p:
        return [f"{Y}{_t('这个进程已经退出了。')}{O}", "", f"{D}{_t('按 Esc 返回')}{O}"], "gone"
    g = group_of(p["args"])
    def _extra():
        o = run("lsof", "-p", str(pid), "-a", "-d", "cwd", "-Fn", timeout=5)
        m = re.search(r"\nn(.*)", o)
        pt = sorted(set(re.findall(r":(\d+) \(LISTEN\)", run("lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", timeout=5))))
        return (m.group(1) if m else ""), pt
    cwd, ports = memo(("extra", pid), 8, _extra)
    kids = [q for q in procs.values() if q["ppid"] == pid]
    chain, cur = [], p["ppid"]
    for _ in range(4):
        q = procs.get(cur)
        if not q or cur <= 1:
            break
        chain.append(f"{cur} {label(q, procs, panes)[:40]}")
        cur = q["ppid"]
    W = max(40, width - 4)
    def key(word):   # 详情页左边那列：中文两个或四个汉字后面补到 11 格；英文按同样的宽度补
        return f"  {pad(_t(word), 11)}" if EN else f"  {word}" + " " * (11 - 2 * len(word))
    L = [f"{B}{_t('进程详情')}{O}  {D}pid {pid}{O}", ""]
    L.append(f"  {B}{label(p, procs, panes)[:W]}{O}")
    L.append(f"{key('所属软件')}{_t(g)}")
    L.append(f"{key('已开')}{p['et']}")
    L.append("  " + _t("内存       {rss}        CPU  {cpu}%（近一分钟平均）").format(rss=gb(p['rss']), cpu=format(p['cpu'], '.0f')))
    if cwd:
        L.append(f"{key('当前目录')}{short(cwd)}")
    if ports:
        L.append(f"{key('监听端口')}{', '.join(ports)}")
    L.append("  " + _t("子进程     {n} 个").format(n=len(kids)) + (f"  {D}{_t('（{names}…）').format(names=', '.join(label(k, procs, panes)[:18] for k in kids[:3]))}{O}" if kids else ""))
    if chain:
        L.append(key("上级") + f"{D} ← {O}".join(chain[:3]))
    L.append(f"{key('命令行')}{D}{p['args'][:W * 3]}{O}")
    L.append("")
    why = protected(p, procs)
    plan = kill_plan(p, procs)
    if why:
        L.append(f"  {R}{_t('不能在这里关：{why}').format(why=why)}{O}")
    else:
        if EN:
            L.append(f"  {B}{_t('关闭方式')}{O}  {plan[0]}")
            L.append(f"  {Y}{pad(_t('注意'), 12)}{O}{plan[2]}")
        else:
            L.append(f"  {B}关闭方式{O}  {plan[0]}")
            L.append(f"  {Y}注意{O}      {plan[2]}")
        if pid in caller_pids(procs):
            L.append(f"  {R}{B}{_t('⚠ 这是你当前所在窗格里的进程（你正在用的会话）。关了你正在打字的窗口就没了。')}{O}")
    return L, why


def main():
    if "--du" in sys.argv:
        du_job()
        return
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    size = shutil.get_terminal_size((100, 40))
    if "--once" in sys.argv:
        print("\n".join(render(size.columns, 0, only)[0]))
        return
    _CPU["started"] = True
    threading.Thread(target=cpu_bg, daemon=True).start()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    st = dict(sel=None, mode="list", msg="", force=False, frozen=None, shown={}, gfilter=None)
    gmap, gorder = {}, []
    rowmap, order = {}, []
    try:
        tty.setcbreak(fd)
        # 开鼠标：点一行选中它（tmux 的 mouse on 会把点击转给在浮窗里跑的程序）
        sys.stdout.write("\033[?25l\033[?1000h\033[?1006h\033[2J")
        buf = b""
        while True:
            size = shutil.get_terminal_size((100, 40))
            if st["mode"] == "list":
                lines, rowmap, procs, panes, st["shown"], gmap, gorder = render(size.columns, size.lines, only, st["sel"], st["frozen"], st["gfilter"])
                order = [rowmap[k] for k in sorted(rowmap)]
                foot = st["msg"] or (f"{D}" + _t("↑↓ 选进程 · Enter/点击 看详情 · ←→/Tab 或点软件行 筛选 · a 看全部") + " · " + (_t("r 重新排序 · Esc 取消选中") if st["frozen"] else _t("q 退出")) + O)
                lines = lines + [foot]
            else:
                procs, panes = processes(), pane_names()
                body, why = detail(st["sel"], procs, panes, size.columns)
                lines = body + [""]
                p = procs.get(st["sel"])
                if st["mode"] == "confirm" and p:
                    plan = kill_plan(p, procs)
                    word = _t("强制结束（SIGKILL，不给它收拾的机会）") if st["force"] else plan[0]
                    lines += [f"  {R}{B}{_t('确定要 {what} 吗？').format(what=word)}{O}",
                              f"  {_t('对象：')}{' ' if EN else ''}{B}pid {p['pid']}{O}  {label(p, procs, panes)[:60]}",
                              f"  {B}{_t('按 y 确认')}{O}{_t('，其他键取消')}"]
                elif p and not why:
                    lines += [f"  {B}x{O} {_t('关闭')}   {D}{_t('X 强制结束   Esc 返回   ↑↓ 换一个')}{O}"]
                else:
                    lines += [f"  {D}{_t('Esc 返回')}{O}"]
                if st["msg"]:
                    lines += ["", "  " + st["msg"]]
            sys.stdout.write("\033[H" + "\n".join(l + "\033[K" for l in lines) + "\033[J")
            sys.stdout.flush()
            r, _, _ = select.select([sys.stdin], [], [], 3)
            if not r:
                continue
            buf += os.read(fd, 64)
            keys = []
            while buf:
                m = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buf)
                if m:
                    keys.append(("click", int(m.group(1)), int(m.group(3)) - 1) if m.group(4) == b"M" and int(m.group(1)) == 0 else None)
                    buf = buf[m.end():]
                    continue
                if buf[:3] in (b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D"):
                    keys.append({b"A": "up", b"B": "down", b"C": "right", b"D": "left"}[buf[2:3]]); buf = buf[3:]; continue
                if buf[:1] == b"\x1b" and len(buf) == 1:
                    keys.append("esc"); buf = b""; continue
                if buf[:2] == b"\x1b[":   # 不认识的转义序列，整段丢掉
                    buf = buf[3:] if len(buf) > 2 else b""; continue
                keys.append(buf[:1].decode("utf-8", "ignore")); buf = buf[1:]
            for k in filter(None, keys):
                if st["mode"] == "list":
                    if k in ("q", "Q", "\x03"):
                        return
                    if k == "esc":
                        if st["sel"] is None:
                            return
                        st["sel"], st["frozen"] = None, None   # 取消选中、恢复自动排序
                        continue
                    def set_filter(g):
                        st["gfilter"], st["sel"], st["frozen"] = g, None, None   # 换筛选就重新排序、取消选中
                    if k in ("left", "right", "\t") and gorder:
                        cyc = [None] + gorder
                        i = cyc.index(st["gfilter"]) if st["gfilter"] in cyc else 0
                        set_filter(cyc[(i + (-1 if k == "left" else 1)) % len(cyc)]); continue
                    if k in ("a", "A"):
                        set_filter(None); continue
                    if isinstance(k, tuple) and k[2] in gmap:   # 点了上面的软件行：筛选（再点一次取消）
                        set_filter(None if st["gfilter"] == gmap[k[2]] else gmap[k[2]]); continue
                    if k in ("up", "down", "\r", "\n") or isinstance(k, tuple):
                        if st["frozen"] is None:
                            st["frozen"] = {kk: list(v) for kk, v in st["shown"].items()}   # 一选中就固定当前排序，免得选着选着被挤走
                    if k in ("d", "D"): du_start()
                    elif k == "r":
                        st["msg"], st["frozen"] = "", None
                    elif k in ("up", "down") and order:
                        i = order.index(st["sel"]) if st["sel"] in order else (-1 if k == "down" else 0)
                        st["sel"] = order[(i + (1 if k == "down" else -1)) % len(order)]
                    elif isinstance(k, tuple) and k[2] in rowmap:
                        st["sel"], st["mode"], st["msg"] = rowmap[k[2]], "detail", ""
                    elif k in ("\r", "\n") and st["sel"]:
                        st["mode"], st["msg"] = "detail", ""
                elif st["mode"] == "detail":
                    if k in ("esc", "q"):
                        st["mode"], st["msg"] = "list", ""
                    elif k in ("up", "down") and order and st["sel"] in order:
                        st["sel"] = order[(order.index(st["sel"]) + (1 if k == "down" else -1)) % len(order)]; st["msg"] = ""
                    elif k in ("x", "X") and st["sel"] in procs and not protected(procs[st["sel"]], procs):
                        st["mode"], st["force"], st["target"] = "confirm", (k == "X"), (st["sel"], procs[st["sel"]]["args"])
                else:   # confirm
                    tgt = st.get("target")
                    p = procs.get(st["sel"])
                    # 进程号可能在你读确认页的时候已经被别的程序拿走：命令行不一致、或进了保护名单，一律不动
                    if k == "y" and p and tgt and tgt == (p["pid"], p["args"]) and not protected(p, procs):
                        if st["force"]:
                            try:
                                pl = kill_plan(p, procs)
                                if pl[1][0] == "quit-app": res = kill_app(pl[1][1])   # 整个应用一起杀，只杀一个进程没用
                                else: os.kill(p["pid"], 9); res = _t("已发送 SIGKILL")
                            except ProcessLookupError: res = _t("进程已经没了")
                            except PermissionError: res = _t("没权限")
                        else:
                            res = do_kill(p, kill_plan(p, procs))
                        for _ in range(10):
                            time.sleep(0.5)
                            if not alive(p["pid"]): break
                        st["msg"] = (f"{G}{_t('{res}，已退出。').format(res=res)}{O}" if not alive(p["pid"])
                                     else f"{Y}{_t('{res}，但它还在（有的要几秒）。再看一次，或按 X 强制结束。').format(res=res)}{O}")
                        st["mode"] = "detail"
                    elif k == "y":
                        st["mode"], st["msg"] = "detail", f"{Y}{_t('这个进程已经变了（退出了或进程号被别的程序用了），没动。')}{O}"
                    else:
                        st["mode"], st["msg"] = "detail", f"{D}{_t('已取消，没动。')}{O}"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?1000l\033[?1006l\033[?25h\033[2J\033[H")


if __name__ == "__main__":
    main()
