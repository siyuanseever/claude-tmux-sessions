"""jump.py — 让 Claude 自己的屏幕滚到某一轮，那一轮的最终回复停在屏幕上方。

Claude Code 没有「跳到第几轮」的命令，所以照人手的做法：从近的那一头开始（Ctrl+Home / Ctrl+End），
往那一轮翻页（PageUp / PageDown 正好半屏，不像滚轮连着滚会加速），每翻一下读一次屏，直到那一轮回复的
开头几个字出现，再把它往上推到屏幕顶部附近。你自己的话（❯ 开头的行，包括顶上粘住的那一行）不算，
因为你常常会引用 Claude 的话。

翻多远不是一屏一屏试出来的：每看一屏都认一下现在停在第几轮，用刚走过的那一段算出「一轮大概几个半屏」，
再照着把剩下的路一次翻掉。翻过头就掉一次头、锁死方向，改成一屏一屏挪。

算法从 tmux 版 sidebar/claude_trail.py 的 cmd_jump 搬来。读屏、发键交给 term（适配层实现）：

    term.screen()        → 现在屏幕上的文字
    term.send(*keys)     → 一次发出去；keys 是 "C-Home" "C-End" "PageUp" "PageDown"，或原样发的转义序列
    term.height          → 屏幕有几行
"""
import re
import time

REDRAW_WAIT = 0.015      # 翻一次之后先等多久再读屏（秒）
JUMP_BUDGET = 6.0        # 秒：一次跳转最多翻这么久，再翻就是在跟你的滚轮抢屏幕
MAX_ROLLS = 400          # screenfuls before giving up: the turn may be gone after /compact or /clear
AT_BOTTOM = "Jump to bottom"   # Claude 往上翻了才在底部显示这句；没有就是在最底
PROMPT = "❯"

_DROP = re.compile(r"[\s*`_~#>|\\]")


def norm(text):
    """屏幕上的一行 / 针，两边都过一遍：去掉空白和 markdown 记号字符再比。
    以前只在针这边剥记号，回复开头是「~/work/…」「my_var」时永远对不上。"""
    return _DROP.sub("", text)


def needle(reply):
    """一轮回复开头的一小段（规范化过）。太长会折到下一行，所以只取第一行的前 12 个字。"""
    for line in (reply or "").splitlines():     # 没有回复的轮次没有针：屏幕上只有 ❯ 开头的提问行
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)        # [text](url) -> text
        line = norm(line)
        if line:
            return line[:12]
    return ""


def needles_of(replies):
    """每轮一根针；整个会话里不唯一的置空：「好的，我来看一下」这种开头好几轮都一样，
    拿它认位置会把差着一百多轮的两块屏幕认成同一处，跳转就在两屏之间来回弹。"""
    needles = [needle(r) for r in replies]
    dup = {n for n in needles if needles.count(n) > 1}
    return ["" if n in dup else n for n in needles]


def locate(screen_text, needles):
    """屏幕上现在有哪几轮：返回认出来的轮次下标（升序）。你自己的话（❯ 开头）不算。"""
    rows = [norm(r) for r in screen_text.splitlines() if not r.lstrip().startswith(PROMPT)]
    return [i for i, n in enumerate(needles) if n and any(n in r for r in rows)]


def jump(term, replies, idx, cancelled=lambda: False):
    """翻到第 idx 轮（replies 是每轮回复的开头）。返回：

    "found"     找到了，停在屏幕上方
    "cancelled" 中途你又点了别的（cancelled() 变真）
    "timeout"   翻了 JUMP_BUDGET 秒还没找到，停在原处
    ("gone", k) 翻到头也没有：主界面里只剩第 k 轮以后的（续接的会话只显示最近一段，或被 /compact 清掉），
                k 认不出时是 None
    """
    target = needle(replies[idx])
    needles = needles_of(replies)
    height = term.height
    pages = 2 if height >= 30 else 1            # Two half-pages per look while that still keeps a reply in view
    from_top = idx < len(replies) / 2

    def needle_row(text):
        for i, row in enumerate(text.splitlines()):
            if target in norm(row) and not row.lstrip().startswith(PROMPT):
                return i
        return None

    def press(before, *keys):
        """发键、等屏幕**不再变**再返回；什么都没动（到头了）返回 None。

        ⚠️ 等的是「不再变」，不是「变了」。一次发几十上百个翻页时，Claude 会先画一次、再接着消化剩下的按键；
        看到第一次变化就返回，拿到的是半路的画面，随后按那个画面算出来的行号全是错的。"""
        term.send(*keys)
        time.sleep(REDRAW_WAIT)
        now, last = term.screen(), None
        for _ in range(60):
            if now != before and now == last:
                return now
            last = now
            time.sleep(0.01)
            now = term.screen()
        return None if now == before else now

    def where(text):
        """屏幕现在停在第几轮：最后一个开头出现在屏幕上的那一轮。"""
        rows = [norm(r) for r in text.splitlines() if not r.lstrip().startswith(PROMPT)]
        seen = None
        for i, n in enumerate(needles):
            if n and any(n in r for r in rows):
                seen = i
        return seen

    def lift(now):
        """把找到的那一轮往上推到屏幕顶部附近：量一下、推一点、再量。滚轮每格滚几行不固定（连着滚会加速），
        一次最多两格、每次重新数；一旦认不出就不推了。"""
        down = f"\x1b[<65;10;{height // 2}M"
        for _ in range(10):
            r = needle_row(now)
            if r is None or r <= 4:
                return
            if AT_BOTTOM not in now:            # 已经在最底：往下推不动，别白等 press 那 0.6 秒
                return
            if r - 4 >= height // 2:
                now = press(now, "PageDown")
            else:
                now = press(now, *[down] * min(2, r - 4))
            if now is None:
                return

    def search(nudge=True):
        now = term.screen()
        # 已经在最底时 Ctrl+End 什么都不会动，press 要干等 0.6 秒才认定「到头了」
        if from_top or AT_BOTTOM in now:
            now = press(now, "C-Home" if from_top else "C-End") or term.screen()
        last_turn, since = None, 0           # 上次认出停在哪一轮；从那以后翻了几个半屏
        prev_down, fine = from_top, False    # 翻过头一次之后就改成一屏一屏地找
        for _ in range(MAX_ROLLS):
            if needle_row(now) is not None:
                if nudge:
                    lift(now)
                return True
            if cancelled():
                return True
            if time.monotonic() > deadline:
                return None
            # 认出现在停在第几轮，就能用刚走过的那一段算出「一轮几个半屏」，按它把剩下的路一次翻掉
            # （只走 60%，宁可多来一轮也别翻过头）。认不出来就一次两个半屏。
            # ⚠️ 方向不能一直听 where 的：它偶尔认错，一错就在相邻两屏之间来回弹。掉头只准一次，之后方向锁死。
            cur = where(now)
            steps, down = pages, prev_down
            if cur is not None and not fine:
                down = cur < idx
                if down != prev_down:
                    fine = True
                elif last_turn is not None and cur != last_turn and since:
                    rate = since / abs(cur - last_turn)          # 半屏 / 轮
                    steps = max(pages, min(int(rate * abs(idx - cur) * 0.6), MAX_ROLLS))
                last_turn, since = cur, 0
            prev_down = down
            now = press(now, *["PageDown" if down else "PageUp"] * steps)
            since += steps
            if now is None:
                return False
        return False

    deadline = time.monotonic() + JUMP_BUDGET
    # Check the result once more: a redraw read half-done can send the search past it.
    for attempt in range(2):
        found = search(nudge=attempt == 0)
        if found is None:
            return "timeout"
        if cancelled():
            return "cancelled"
        if found and needle_row(term.screen()) is not None:
            return "found"
    # 翻到头也没有：看一眼主界面最上面是哪一轮，比它更早的都翻不到
    term.send("C-Home")
    time.sleep(0.4)
    top = locate(term.screen(), needles)
    term.send("C-End")
    return ("gone", top[0] if top and top[0] > idx else None)
