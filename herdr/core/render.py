"""render.py — 侧栏里画出来的东西：额度柱子、模型行、两种列表（宽版每轮两行、窄版每轮一行）。

从 tmux 版搬来（sidebar/claude_trail.py 的 status_lines 一族、sidebar/trail_daemon.py 的 rows_of / wrap），
柱子的颜色和阈值规则不变。颜色是模块里的几个常量，适配层用 set_colors() 整套换掉（比如跟着 herdr 的主题）。
"""
import re
import unicodedata
from datetime import datetime

from .i18n import t as _t

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
# 没有要提醒的时候就淡灰，别晃眼睛；要提醒才上颜色，这样颜色一出现就是信号。
USED_C = "\033[38;2;158;158;158m"  # 淡灰：已经用掉的，一切正常
WARN_C = "\033[38;2;215;135;0m"    # 橙：该留意了
ALERT_C = "\033[38;2;215;95;95m"   # 红：真的要注意了
TIME_C = "\033[38;2;0;135;215m"    # 蓝：窗口已经走过的时间 / 自动压缩的那条线
GREY = "\033[38;2;108;108;108m"
DIMC = "\033[38;2;88;88;88m"
CYAN = "\x1b[36m"
BUSY_C = WARN_C                    # 状态：工作中（窄版的状态点、还在干活的那一轮）
DONE_C = DIMC                      # 状态：完成 / 空闲
COLORS = ("USED_C", "WARN_C", "ALERT_C", "TIME_C", "GREY", "DIMC", "CYAN", "BUSY_C", "DONE_C")


def set_colors(**colors):
    """换颜色：值是 ANSI 前景色序列，比如 "\\033[38;2;166;227;161m"。没给的保持原样。"""
    g = globals()
    for k, v in colors.items():
        if k in COLORS and v:
            g[k] = v


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def cols(text):
    """Display width, CJK counting double — what fzf uses to decide where a line wraps."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in ANSI.sub("", text))


def fit(text, width):
    """把一段带颜色的状态塞进固定宽度：短了补空格，长了截掉（按显示宽度，中文算两格）。"""
    if not width:
        return ""
    body = " " + text
    w = cols(body)
    if w <= width:
        return body + " " * (width - w)
    out, used = "", 0
    for ch in ANSI.sub("", body):
        if used + cols(ch) > width:
            break
        out, used = out + ch, used + cols(ch)
    return out + " " * (width - used)


def clip(text, width):
    """按显示宽度截（中文算两格）。截了就丢掉颜色，调用方在后面补 RESET。"""
    plain = ANSI.sub("", text)
    if cols(plain) <= width:
        return text
    out, w = "", 0
    for ch in plain:
        if w + cols(ch) > width:
            break
        out, w = out + ch, w + cols(ch)
    return out


def oneline(s):
    return " ".join(s.split())


def when(ts):
    if not ts:
        return ""
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%m/%d %H:%M")


# ---- 额度和上下文柱子 ----------------------------------------------------------

WINDOW_SECS = {"5h": 5 * 3600, "7d": 7 * 86400}
CTX_RE = re.compile(r"(\d+)%\s*\((\d+)k\)")
BAR_FIXED = 10           # 一行的固定开销：`ctx ` 4 列 + ` 100%` 5 列 + 尾巴前的那个空格 1 列


def _left_str(secs):
    """还有多久重置，窄到塞得进一行：1d14h / 2h01m / 37m。"""
    if secs <= 0:
        s = _t("已过")
        return " " * max(0, 6 - cols(s)) + s     # 固定 6 格（中文两个字 4 格，前面补 2 格）
    d, r = divmod(int(secs), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m".rjust(6)


def _bar_w(width, tails=()):
    """三条柱子（ctx / 5h / 7d）共用一个宽度，列才对得齐；按最长的那条尾巴反推，尾巴就不会被挤出右边界。
    窄到连 4 格都放不下就三条都不画柱子：三条要一起有或一起没有，才还是对齐的。"""
    longest = max((cols(t) for t in tails), default=6)
    room = width - BAR_FIXED - longest
    return 0 if room < 4 else room


def _bar_line(label, frac, mark, tail, bar_w, used_c=None):
    """`5h  ████▌░░░░│░░░░  24% 2h01m`

    实心 = 已经用掉的比例；蓝色竖线 = 那条参照线（额度是「窗口已经走过的时间」，上下文是「自动压缩的那条线」）。
    线在实心右边就是还有富余，被实心盖过去就是过了。"""
    used_c = used_c or USED_C
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
    """额度柱子的颜色：按「比匀速线超前多少」，不是按用了多少。5h 用掉 38%，在窗口第 3 小时是正常，
    在第 10 分钟是要命。超出匀速线 20 个百分点起橙、35 个起红；绝对值到 80% / 93% 也分别橙、红。"""
    if frac is None:
        return USED_C
    over = frac - mark if mark is not None else 0.0
    if frac >= 0.93 or over >= 0.35:
        return ALERT_C
    if frac >= 0.80 or over >= 0.20:
        return WARN_C
    return USED_C


def _window(w, label):
    """(用掉的比例, 匀速线的位置, 还剩多久的秒数)，认不出的是 None。"""
    pct = (w or {}).get("utilization")
    left = None
    try:
        dt = datetime.fromisoformat((w or {}).get("resets_at") or "")
        left = (dt - datetime.now().astimezone()).total_seconds()
    except (TypeError, ValueError):
        pass
    mark = None if left is None else 1.0 - max(0.0, min(1.0, left / WINDOW_SECS.get(label, 5 * 3600)))
    return (None if pct is None else float(pct) / 100), mark, left


def _quota_tail(w):
    _, _, left = _window(w, "5h")
    return _left_str(left) if left is not None else "     ?"


def _quota_line(label, w, bar_w):
    frac, mark, left = _window(w, label)
    tail = _left_str(left) if left is not None else "     ?"
    return _bar_line(label, frac, mark, tail, bar_w, quota_colour(frac, mark))


def ctx_colour(ctx, compact_at):
    """上下文没有「匀速」可言，按离自动压缩那条线多远：过八成橙、过线红。"""
    m = CTX_RE.search(ctx or "")
    if not m:
        return USED_C
    tokens, line = int(m.group(2)) * 1000, compact_at
    return ALERT_C if tokens >= line else WARN_C if tokens >= line * 0.8 else USED_C


def _ctx_line(model, ctx, bar_w, tail, compact_at, hard):
    """`ctx ██████│░░░░░░░░  21% 216k`：百分比已经是相对这个模型的上下文窗口算好的，直接当填充比例。
    参照线是自动压缩那条，按同一个窗口折算（haiku 20 万，其余 100 万）。"""
    m = CTX_RE.search(ctx or "")
    if not m:
        return None
    pct = int(m.group(1))
    window = 200_000 if "haiku" in (model or "").lower() else 1_000_000
    colour = ctx_colour(ctx, compact_at or hard)
    return _bar_line("ctx", pct / 100, None if not compact_at else compact_at / window,
                     tail, bar_w, colour)


def _fresh_mark(u):
    """只在有话说的时候出声：正在查 ⟳，刚查失败 ✗。"""
    if not u:
        return ""
    att, got, err = u.get("attempted_at_ms") or 0, u.get("fetched_at_ms") or 0, u.get("error_at_ms") or 0
    if att > got and att > err:
        return f" {GREY}⟳{RESET}"
    if err > got:
        return f" {ALERT_C}✗{RESET}"
    return ""


def usage_state(u, now):
    """刷新按钮后面那一小段：在查、查完了多久、查失败了。要塞进按钮后面的固定宽度，所以写短。"""
    if not u:
        return f"{DIMC}{_t('无读数')}{RESET}"
    att, got, err = u.get("attempted_at_ms") or 0, u.get("fetched_at_ms") or 0, u.get("error_at_ms") or 0
    if att > got and att > err:
        return f"{WARN_C}{_t('查询中…')}{RESET}"
    if err > got:
        return f"{ALERT_C}{_t('查询失败')}{RESET}"
    age = int(now - got / 1000)
    if age < 90:
        return f"{USED_C}{_t('刚刚更新')}{RESET}"
    if age < 3600:
        return f"{DIMC}{_t('{n} 分钟前').format(n=age // 60)}{RESET}"
    return f"{DIMC}{_t('{n} 小时前').format(n=min(age // 3600, 99))}{RESET}"


def status_lines(width, model, ctx, usage, account, compact_at, hard):
    """宽版表头里的模型 / 账号一行，加上 ctx / 5h / 7d 三条柱子（有几行看有没有数据）。"""
    # fzf 的表头左边留 2 列（和列表项的 ▌ 对齐），按整格宽排会被挤出右边界截成「…」
    width = max(24, width - 2)
    u = usage
    m = CTX_RE.search(ctx or "")
    ctx_tail = f"{m.group(2)}k" if m else None
    tails = [t for t in (ctx_tail, _quota_tail((u or {}).get("five_hour")),
                         _quota_tail((u or {}).get("seven_day"))) if t]
    bar_w = _bar_w(width, tails)
    lines = []
    if model or account:
        head = model
        if account and len(f"{model} · {account}") <= width:   # 窄的时候宁可不显示账号，也别把模型截了
            head = f"{model} · {account}" if model else account
        lines.append(f"{GREY}{head}{RESET}")
    line = _ctx_line(model, ctx, bar_w, ctx_tail, compact_at, hard) if ctx_tail else None
    if line:
        lines.append(line)
    elif ctx and not model:
        lines.append(f"{GREY}{ctx}{RESET}")
    if u:
        lines.append(_quota_line("5h", u.get("five_hour"), bar_w) + _fresh_mark(u))
        lines.append(_quota_line("7d", u.get("seven_day"), bar_w))
    return lines


def quota_pct(u, label, key):
    """窄版用的 (百分比, 颜色)：颜色和柱子同一套规则。"""
    frac, mark, _ = _window((u or {}).get(key), label)
    if frac is None:
        return None
    return frac * 100, quota_colour(frac, mark)


def ctx_pct(ctx, compact_at):
    """窄版用的 (百分比, 颜色)。"""
    m = CTX_RE.search(ctx or "")
    if not m:
        return None
    return int(m.group(1)), ctx_colour(ctx, compact_at)


# ---- 列表 --------------------------------------------------------------------

FOLD, UNFOLD = "fold", "unfold"   # 折叠行的 id：点「展开」写 fold，点「收起」写 unfold


def wrap(text, width):
    """按显示宽度（中文算两格）折行，给展开的那一项用；fzf 本身不折行。"""
    lines = []
    for para in text.splitlines() or [""]:
        cur, w = "", 0
        for ch in para:
            cw = cols(ch)
            if w + cw > width:
                lines.append(cur)
                cur, w = "", 0
            cur += ch
            w += cw
        lines.append(cur)
    return lines


def rows_of(stops, busy, opened=None, width=40, expanded=False, here=(), missing=frozenset()):
    """宽版：每轮两行（你说的话 + 回复的开头），fzf --read0 的条目，id 和显示内容之间一个 tab。

    opened = 点开的那一轮：它的「你说的话」整段展开。/compact 之前的轮次收成最上面的一行（主界面已经跳不过去了），
    expanded 时照常列出来，那一行变成「收起」。here = 主界面屏幕上正显示着的轮次下标，最左一列画一条亮边。"""
    out = []
    gone_n = sum(1 for st in stops if st["compacted"])
    if gone_n:
        span = f"#1–#{gone_n}" if gone_n > 1 else "#1"
        act = _t("点这里收起") if expanded else _t("点开展开")
        out.append(f"{UNFOLD if expanded else FOLD}\t{DIM}"
                   f"{_t('{span} 已压缩（{n} 轮）· {act}').format(span=span, n=gone_n, act=act)}{RESET}")
    for n, st in enumerate(stops, 1):
        if st["compacted"] and not expanded:
            continue
        ask = oneline(st["prompt"])[:300] if st["kind"] == "human" else _t("（{p}）").format(p=st['prompt'])
        if busy and n == len(stops):
            reply = _t("（Claude 还在干活…）")
        else:
            reply = st["reply"] or _t("（没有文字回复）")
        gone = " · " + _t("已压缩") if st["compacted"] else ""
        if st["id"] in missing:
            gone += " · " + _t("主界面已无")
        mark = f"{CYAN}┃{RESET}" if n - 1 in here else " "
        meta = f"{mark}{DIM}#{n} {when(st['ts'])}{gone}{RESET}"
        if st["id"] in missing:
            ask = f"{DIM}{ask}{RESET}"
        if st["id"] == opened and st["kind"] == "human":
            body = "\n".join(" " + mark + " " + ln for ln in wrap(st["prompt"].strip(), max(10, width - 4)))
            out.append(f"{st['id']}\t{meta} {BOLD}{_t('你说的：')}{RESET}\n{body}\n {mark} {DIM}{reply}{RESET}")
            continue
        out.append(f"{st['id']}\t{meta} {ask}\n {mark} {DIM}{reply}{RESET}")
    return "\0".join(out)


def narrow_rows_of(stops, busy, width, expanded=False, missing=frozenset()):
    """窄版：每轮一行，轮号加你那句话的前几个字（`#41 那我们`），不放回复的开头。"""
    room = max(4, width - 3)                       # 左边 2 列 + 指针
    out = []
    gone_n = sum(1 for st in stops if st["compacted"])
    if gone_n:
        out.append(f"{UNFOLD if expanded else FOLD}\t"
                   + clip(f"{DIMC}#1–#{gone_n} {'−' if expanded else '+'}{RESET}", room))
    for n, st in enumerate(stops, 1):
        if st["compacted"] and not expanded:
            continue
        ask = oneline(st["prompt"]) if st["kind"] == "human" else _t("（{p}）").format(p=st["prompt"])
        colour = (BUSY_C if busy and n == len(stops) else DIMC if st["id"] in missing else "")
        out.append(f"{st['id']}\t" + clip(f"{DIMC}#{n}{RESET} {colour}{ask}", room) + RESET)
    return "\0".join(out)
