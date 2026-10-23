#!/usr/bin/env python3
"""把 tmux 里空闲的 Claude 会话换到另一个账号：退出 → 在原窗格里用另一个账号的命令 --resume 同一个会话 id。

几个账号共用同一份会话记录（比如 ~/.claude-b/projects 是指向 ~/.claude/projects 的软链），所以接着聊上下文不丢。
账号在 ~/.config/claude-tmux/accounts.json 里配（见 load_accounts）；没配就只有默认的 ~/.claude 一个，切不了。
安全闸（任何一条不满足就跳过那个窗格、不动它）：
  - 必须是 idle（工作中 / 在等确认的不碰）
  - 输入框里不能有没发出去的草稿（退出会丢）
  - 进程必须是交互式 claude（队友、后台会话不碰）
  - 已经是目标账号的跳过
退出后等它真回到 shell 才敲新命令；敲完等「新进程的会话文件出现在目标账号目录里」才算成功。

用法：python3 switch_account.py --to <账号名> [--session <tmux session>] [--pane %24 ...] [--dry-run]
侧栏的 ⇄ 按钮（sidebar/claude_trail.py cmd_switch）调它；也可以被别的程序 import 后用 start_job 跑。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

HOME = pathlib.Path.home()
DEFAULT_DIR = HOME / ".claude"
ACCOUNTS_FILE = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "claude-tmux" / "accounts.json"


def load_accounts() -> tuple[dict, dict]:
    """账号名 → (配置目录, 启动时要额外设的环境变量)。

    ~/.config/claude-tmux/accounts.json 的写法：
      {"accounts": {"work": {"config_dir": "~/.claude"},
                    "home": {"config_dir": "~/.claude-home", "env": {"SOME_VAR": "x"}}}}
    config_dir 就是那个账号的 CLAUDE_CONFIG_DIR（默认账号是 ~/.claude）；env 里的值支持 ~。
    没有这个文件就只有一个默认账号，切换会被跳过并说明原因。"""
    try:
        raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8")).get("accounts") or {}
    except (OSError, ValueError):
        raw = {}
    accts, envs = {}, {}
    for name, a in raw.items():
        d = pathlib.Path(os.path.expanduser(str(a.get("config_dir") or "~/.claude")))
        accts[name] = ("claude", d)
        envs[name] = {k: os.path.expanduser(str(v)) for k, v in (a.get("env") or {}).items()}
    if not accts:
        accts, envs = {"default": ("claude", DEFAULT_DIR)}, {"default": {}}
    return accts, envs


# 账号 → (启动命令, 配置目录)。启动命令一律是 claude 本身：shell 函数（比如 claude2）不是每个窗格的
# shell 里都有，靠 CLAUDE_CONFIG_DIR 区分账号。
ACCTS, ACCT_ENV = load_accounts()

# ---- 读窗格屏幕：判断空闲 / 在干活 / 在等确认 --------------------------------------------
# 特征串照 Claude Code 现在的界面写，界面改了就会认不出（落到「空闲」，于是更保守的安全闸——输入框
# 检查——还会兜一道）。
TAIL = 40  # 抓屏幕最后多少行来判断
ASK = re.compile(r"Do you want|❯ 1\. Yes|Esc to cancel")
BUSY = re.compile(r"esc to interrupt")
CLAUDE_CMD = re.compile(r"^\d+\.\d+\.\d+$")  # Claude 窗格的 pane_current_command 是版本号
# launchd 起的进程 PATH 只有 /usr/bin:/bin，找不到 Homebrew 的 tmux
TMUX = shutil.which("tmux") or next((p for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")
                                     if pathlib.Path(p).exists()), "tmux")


def _tmux(*args: str) -> str:
    r = subprocess.run([TMUX, *args], capture_output=True, text=True, errors="replace", timeout=10)
    return r.stdout if r.returncode == 0 else ""


def screen_state(lines: list[str]) -> str:
    text = "\n".join(lines)
    return "ask" if ASK.search(text) else "busy" if BUSY.search(text) else "idle"


def tail_lines(pane: str) -> list[str]:
    raw = _tmux("capture-pane", "-p", "-t", pane, "-S", f"-{TAIL}").splitlines()
    return [l.rstrip() for l in raw if l.strip()]


def session_panes(session: str) -> list[str]:
    """一个 tmux session 里所有 Claude 窗格。"""
    out = _tmux("list-panes", "-s", "-t", session, "-F", "#{pane_id} #{pane_current_command}")
    return [ln.split()[0] for ln in out.splitlines() if len(ln.split()) == 2 and CLAUDE_CMD.match(ln.split()[1])]

SHELLS = {"zsh", "bash", "fish", "sh", "-zsh", "-bash"}
KEEP_BOOL = {"--dangerously-skip-permissions", "--chrome", "--no-chrome", "--verbose", "--ide"}
KEEP_VAL = {"--permission-mode", "--model", "--effort", "--add-dir", "--settings", "--agent", "--append-system-prompt"}
DRAFT = re.compile(r"^[\s│]*[❯>]\s?(.*?)[\s│]*$")
RULE = re.compile(r"^\s*─{3,}")   # 输入框上边那条横线（右边可能带会话名：「──── 手机页设计交接 ─」）


def tm(*a: str) -> str:
    return _tmux(*a)


def acct_of(cfg: str) -> str:
    """窗格上的 @claude_config（CLAUDE_CONFIG_DIR 原值，默认账号是空串）→ 账号名；认不出就原样返回。"""
    d = pathlib.Path(os.path.expanduser(cfg)) if cfg else DEFAULT_DIR
    return next((name for name, (_, cd) in ACCTS.items() if cd == d), cfg or "default")


def claude_proc(pane: str):
    """窗格里正在跑的 claude 进程 → (pid, 命令行参数列表)；找不到返回 None。"""
    pp = tm("display-message", "-p", "-t", pane, "#{pane_pid}").strip()
    # 窗格进程本身就是 claude（tmux 直接用 claude 起的窗格，里面没有 shell）也算
    for pid in [pp] + subprocess.run(["pgrep", "-P", pp], capture_output=True, text=True, errors="replace").stdout.split():
        args = subprocess.run(["ps", "-o", "args=", "-p", pid], capture_output=True, text=True, errors="replace").stdout.strip()
        if re.match(r"^(\S*/)?claude\d*(\s|$)", args) or "/claude/versions/" in args.split(" ")[0]:
            return int(pid), shlex.split(args), pid == pp
    return None


def session_file(pid: int, cfg_dir: pathlib.Path):
    f = cfg_dir / "sessions" / f"{pid}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


ANSI = re.compile(r"\x1b\[([0-9;:]*)m")


def _ghost(raw: str, text: str) -> bool:
    """输入框里的字是不是 Claude Code 的灰色/斜体「建议输入」（按 Tab 才会变成真输入）。
    真正敲进去的字是默认颜色；建议是斜体或灰色：提示符之后、字之前出现 SGR 2 / 3 / 38;5;238–250。"""
    i = raw.find(text[:3])
    if i < 0:
        return False
    head = raw[raw.rfind("❯", 0, i) + 1:i] if "❯" in raw[:i] else raw[:i]
    for g in ANSI.findall(head):
        for p in re.findall(r"38;5;(\d+)", g):
            if 238 <= int(p) <= 250:
                return True
        if any(x in ("2", "3") for x in g.split(";")):
            return True
    return False


def draft_text(lines: list[str], styled: list[str] | None = None):
    """输入框里没发出去的字；空 / 只有占位提示 / 灰色建议输入返回 ''；认不出输入框返回 None。

    只认紧跟在横线后面的那一行：输入框上边一定是一条横线。开着队友时输入框下面还有一块队友列表，
    选中的那个队友前面也是「❯」（「❯ ◯ polish-draft  首开体验…」），以前从下往上找第一个 ❯，
    就把它当成了没发出去的字，带队友的领队一个都切不了（2026-10-23 实测 %40）。"""
    for i in range(len(lines) - 1, 0, -1):
        ln = lines[i]
        m = DRAFT.match(ln) if RULE.match(lines[i - 1]) else None
        if m:
            t = m.group(1).strip()
            if not t or t.startswith('Try "'):
                return ""
            for raw in reversed(styled or []):
                if ANSI.sub("", raw).strip() == ln.strip() or t[:3] in ANSI.sub("", raw):
                    if _ghost(raw, t):
                        return ""
                    break
            return t
    return None


def relaunch_args(args: list[str], sid: str) -> list[str]:
    """原命令行里留下启动选项，换掉 --resume / --continue。"""
    out, i = [], 1
    while i < len(args):
        a = args[i]
        if a in KEEP_BOOL:
            out.append(a)
        elif a in KEEP_VAL and i + 1 < len(args):
            out += [a, args[i + 1]]; i += 1
        elif "=" in a and a.split("=", 1)[0] in KEEP_VAL:
            out.append(a)
        i += 1
    return out + ["--resume", sid]


TEAM_WAIT = 600     # 秒：让领队收队后，最多等这么久队友全部退出
IDLE_WAIT = 180     # 秒：队友都退出后，最多等领队这一轮停下来


def team_mates(pane: str) -> list[tuple[str, str]]:
    """同一个窗口里除了 pane 自己以外的队友 → [(窗格, 队友名)]。队友是领队起的 claude，命令行带 --agent-id。"""
    out = []
    for q in tm("list-panes", "-t", pane, "-F", "#{pane_id} #{pane_pid}").splitlines():
        qp, qpid = q.split()
        if qp == pane:
            continue
        pr = claude_proc(qp)
        if pr and "--agent-id" in pr[1]:
            a = pr[1]
            out.append((qp, a[a.index("--agent-name") + 1] if "--agent-name" in a[:-1] else qp))
    return out


def disband(pane: str, mates: list[tuple[str, str]], log) -> dict | None:
    """往领队输入框里打一句话让它收队，等队友都退出、领队停下来。成了返回 None，否则返回跳过/失败的结果。

    以前带队友的窗口一律不动，得你自己去跟领队说「收队」、等它收完再点一次切换
    （2026-10-23 用户：「我不想做两步……就想一步按个按钮都能切过去」）。
    队友本身没法单独换账号（它们是领队起的进程，领队一退出就跟着结束），所以做法是先让领队把
    结果收回来、把队友关掉，领队换了账号以后要再开队友，开出来的自然就是新账号的。"""
    names = "、".join(n for _, n in mates)
    msg = (f"【切账号】要把这个会话换到另一个账号，得先收队：请让 {names} 把手上的结果交回来，"
           f"然后把它们全部关掉。全部关掉后就停下，别的什么都不用做。")
    log(f"让领队收队（{len(mates)} 个队友）")
    tm("send-keys", "-t", pane, "-l", msg); time.sleep(0.4); tm("send-keys", "-t", pane, "Enter")
    end = time.time() + TEAM_WAIT
    while time.time() < end:
        time.sleep(3)
        left = team_mates(pane)
        if not left:
            break
        log(f"等队友退出（还剩 {len(left)} 个）")
    else:
        return {"state": "fail", "msg": f"已让领队收队，{TEAM_WAIT // 60} 分钟内队友没退完（还剩 "
                                        f"{'、'.join(n for _, n in team_mates(pane))}），没再动"}
    log("队友都退了，等领队停下来")
    end = time.time() + IDLE_WAIT
    while time.time() < end:
        time.sleep(2)
        if screen_state(tail_lines(pane)) == "idle":
            return None
    return {"state": "fail", "msg": "队友都退了，但领队一直没停下来，没再动"}


def switch_one(pane: str, to: str, dry: bool = False, log=lambda m: None, stop_bg: bool = False,
               _disbanded: bool = False) -> dict:
    """stop_bg：页面上你点了「确认停掉后台任务」才会是 True；没有命令行开关。
    带队友的窗口：先让领队收队（disband），收完再照常切。"""
    cmd, cfg_dir = ACCTS[to]
    cur_cfg = tm("show-options", "-pv", "-t", pane, "@claude_config").strip()
    if acct_of(cur_cfg) == to:
        return {"state": "skip", "msg": f"已经是 {to}"}
    if os.environ.get("TMUX_PANE") == pane:
        return {"state": "skip", "msg": "这是当前正在用的窗格"}
    lines = tail_lines(pane)
    st = screen_state(lines)
    if st != "idle":
        return {"state": "skip", "msg": "正在工作或等你确认，没动"}
    if not stop_bg and re.search(r"\d+ shells? still running|\d+ background|scheduled task|Background work|\d+ shells?\b", "\n".join(lines[-4:])):
        return {"state": "skip", "msg": "它挂着后台任务/定时任务，切换会把它们停掉，没动"}
    proc = claude_proc(pane)
    if not proc:
        return {"state": "skip", "msg": "窗格里没找到交互式 claude 进程"}
    pid, args, direct = proc
    if "--agent-id" in args:
        return {"state": "skip", "msg": "队友：随领队收队，不单独切"}
    src_dir = ACCTS[acct_of(cur_cfg)][1] if acct_of(cur_cfg) in ACCTS else DEFAULT_DIR
    info = session_file(pid, src_dir)
    if not info or info.get("kind") != "interactive" or not info.get("sessionId"):
        return {"state": "skip", "msg": "不是交互式会话（队友/后台），没动"}
    # 输入框先查：收队要往里打字，里面有你没发出去的字就不能动
    d = draft_text(lines, [l for l in _tmux("capture-pane", "-p", "-e", "-t", pane, "-S", f"-{TAIL}").splitlines() if l.strip()])
    if d is None:
        return {"state": "skip", "msg": "认不出输入框，为防丢字没动"}
    if d:
        return {"state": "skip", "msg": f"输入框里有没发出去的字（{d[:20]}…），没动"}
    mates = team_mates(pane)
    if mates:   # 重启领队会把它带的队友弄断：先让它收队，收完再切
        if _disbanded:
            return {"state": "fail", "msg": "收过一次队了，窗口里还有队友，没再动"}
        if dry:
            return {"state": "ok", "msg": f"（演练）会先让领队收队（{'、'.join(n for _, n in mates)}），收完再退出重启"}
        bad = disband(pane, mates, log)
        if bad:
            return bad
        return switch_one(pane, to, dry, log, stop_bg, _disbanded=True)
    sid = info["sessionId"]
    rl = " ".join(shlex.quote(a) for a in relaunch_args(args, sid))
    # 用 env 写法，不用 shell 函数（比如 claude2）：不是每个窗格的 shell 里都定义了它。
    # 别的账号设过的变量先 -u 掉，免得从原来那个账号的环境里带过来。
    unset = sorted({k for e in ACCT_ENV.values() for k in e} | {"CLAUDE_CONFIG_DIR"})
    sets = ([] if cfg_dir == DEFAULT_DIR else [f"CLAUDE_CONFIG_DIR={shlex.quote(str(cfg_dir))}"]) + \
           [f"{k}={shlex.quote(v)}" for k, v in ACCT_ENV.get(to, {}).items()]
    line = "env " + " ".join(f"-u {k}" for k in unset) + (" " + " ".join(sets) if sets else "") + f" claude {rl}"
    if direct:   # 没有 shell：respawn-pane 原地换进程（/exit 会把窗格带走）
        if dry:
            return {"state": "ok", "msg": f"（演练）会原地重启：{line}"}
        tm("respawn-pane", "-k", "-t", pane, line)
        return _wait_up(pane, pid, sid, cfg_dir, to, line)
    if dry:
        return {"state": "ok", "msg": f"（演练）会退出再执行：{line}"}
    log(f"{pane} 退出")
    tm("send-keys", "-t", pane, "-l", "/exit"); time.sleep(0.4); tm("send-keys", "-t", pane, "Enter")
    for _ in range(40):
        time.sleep(0.5)
        if tm("display-message", "-p", "-t", pane, "#{pane_current_command}").strip() in SHELLS:
            break
        if "Background work is running" in "\n".join(tail_lines(pane)):
            if stop_bg:   # 你在页面上确认过要停：默认选中第 1 项「Exit and stop tasks」，回车
                tm("send-keys", "-t", pane, "Enter")
                continue
            tm("send-keys", "-t", pane, "Escape")   # 「退出并停掉后台任务」的确认：取消，选 Stay
            return {"state": "skip", "msg": "它有后台任务，退出会停掉它们，已取消退出，没动"}
    else:
        return {"state": "fail", "msg": "它没有退出（可能刚好开始工作），没再动"}
    time.sleep(0.3)
    tm("send-keys", "-t", pane, "-l", line); tm("send-keys", "-t", pane, "Enter")
    return _wait_up(pane, pid, sid, cfg_dir, to, line)


def _wait_up(pane, old_pid, sid, cfg_dir, to, what) -> dict:
    """等新进程的会话文件出现在目标账号目录里，且会话 id 没变，才算切成功。"""
    for _ in range(60):
        time.sleep(0.5)
        np = claude_proc(pane)
        if np and np[0] != old_pid:
            nf = session_file(np[0], cfg_dir)
            if nf and nf.get("sessionId") == sid:
                # 窗格上的账号标记（ctx_to_tmux.py 写的 CLAUDE_CONFIG_DIR 原值，默认账号是空串）本来要等
                # 下一次发消息才更新；这期间侧栏的账号按钮、再点一次切换都会按旧账号算，所以这里先写上。
                tm("set-option", "-p", "-t", pane, "@claude_config", "" if cfg_dir == DEFAULT_DIR else str(cfg_dir))
                return {"state": "ok", "msg": f"已切到 {to}，接着原会话"}
    return {"state": "fail", "msg": f"已执行 {what}，但 30 秒内没看到它在 {to} 下起来，请看一眼窗格"}


JOBS: dict = {}


def start_job(panes: list[str], to: str, dry: bool = False, stop_bg: bool = False) -> str:
    """后台跑：一个窗格一个线程（互不相干），页面用 job id 轮询进度。"""
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"to": to, "items": {p: {"state": "queued", "msg": "排队"} for p in panes}}
    def run(p):
        JOBS[jid]["items"][p] = {"state": "running", "msg": "切换中…"}
        def log(m):   # 页面轮询时看得到进行到哪一步（收队可能要几分钟）
            JOBS[jid]["items"][p] = {"state": "running", "msg": m}
        try:
            JOBS[jid]["items"][p] = switch_one(p, to, dry, log=log, stop_bg=stop_bg)
        except Exception as e:   # 一个窗格出错不影响别的
            JOBS[jid]["items"][p] = {"state": "fail", "msg": f"{type(e).__name__}: {e}"}
    def go():
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(run, panes))
    threading.Thread(target=go, name=f"switch-{jid}", daemon=True).start()
    return jid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, choices=list(ACCTS))
    ap.add_argument("--session", help="tmux session 名；不给就看 --pane")
    ap.add_argument("--pane", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    panes = list(a.pane)
    if a.session:
        panes += session_panes(a.session)
    if not panes:
        raise SystemExit("没有要切的窗格")
    jid = start_job(panes, a.to, a.dry_run)
    while any(v["state"] in ("queued", "running") for v in JOBS[jid]["items"].values()):
        time.sleep(1)
    for p, v in JOBS[jid]["items"].items():
        print(f"{v['state']:5} {p:6} {v['msg']}")


if __name__ == "__main__":
    main()
