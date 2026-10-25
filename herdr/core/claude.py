"""claude.py — Claude Code 专属的：配置目录、账号、订阅额度、模型和上下文大小。

两个账号可以同时在线（CLAUDE_CONFIG_DIR 各指一个目录，比如 ~/.claude 和 ~/.claude-l2），额度和登录账号
都是各自一份，所以这里的函数都按配置目录来。

从 tmux 版搬来：current_account / _auto_compact_at 来自 sidebar/claude_trail.py，last_usage / short_model
来自 hooks/ctx_to_tmux.py，额度查询来自 bin/usage-refresh.py。tmux 版保持原样不动。
"""
import fcntl
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .i18n import t as _t

DEFAULT_CFG = Path.home() / ".claude"
# 额度读数存在哪：插件（适配层）启动时改成它自己的状态目录
USAGE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "statetrail"


# ---- 配置目录、会话、账号 --------------------------------------------------------

def config_dirs():
    """可能的 CLAUDE_CONFIG_DIR：默认的 ~/.claude，加上 ~/.claude-*（一个账号一个目录，比如 ~/.claude-l2）。
    STATETRAIL_CONFIG_DIRS（冒号分隔）可以改。"""
    env = os.environ.get("STATETRAIL_CONFIG_DIRS")
    if env:
        return [Path(os.path.expanduser(d)) for d in env.split(":") if d]
    extra = sorted(d for d in Path.home().glob(".claude-*") if (d / "projects").is_dir())
    return [DEFAULT_CFG, *extra]


def config_dir_of_pid(pid):
    """这个进程的 Claude 用的是哪个配置目录：Claude Code 自己会给每个进程写 <配置目录>/sessions/<pid>.json，
    看这个文件落在哪个目录就知道了（tmux 版靠钩子把 CLAUDE_CONFIG_DIR 记下来，这里不需要钩子）。"""
    if pid:
        for d in config_dirs():
            if (d / "sessions" / f"{pid}.json").exists():
                return d
    return DEFAULT_CFG


def transcript_path(sid, cfg_dir):
    """会话 id → 对话记录。先在它自己的配置目录里找，再找别的（两个目录的 projects 可能是同一个软链）。"""
    for d in [Path(cfg_dir), *(x for x in config_dirs() if x != Path(cfg_dir))]:
        hits = sorted((d / "projects").glob(f"*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime)
        if hits:
            return hits[-1]
    return None


def claude_json(cfg_dir):
    """Claude Code 自己写的 .claude.json：默认那套在家目录下，不在 ~/.claude/ 里。"""
    return Path.home() / ".claude.json" if Path(cfg_dir) == DEFAULT_CFG else Path(cfg_dir) / ".claude.json"


_CJ = {}


def _claude_json(cfg_dir):
    """.claude.json 里要用的两段：登录账号、Claude Code 自己缓存的额度。按 mtime 缓存，只留这两段，
    这文件可能很大，每次渲染都读太浪费。"""
    conf = claude_json(cfg_dir)
    try:
        mt = conf.stat().st_mtime
    except OSError:
        return {}
    hit = _CJ.get(conf)
    if not hit or hit[0] != mt:
        try:
            d = json.loads(conf.read_text())
            keep = {"oauthAccount": d.get("oauthAccount") or {}, "cachedUsageUtilization": d.get("cachedUsageUtilization")}
        except (OSError, ValueError):
            keep = {}
        hit = _CJ[conf] = (mt, keep)
    return hit[1]


def current_account(cfg_dir=DEFAULT_CFG):
    """这个配置目录登的是哪个账号，取邮箱 @ 前面那段。"""
    return ((_claude_json(cfg_dir).get("oauthAccount") or {}).get("emailAddress") or "").split("@")[0]


def short_account(cfg_dir):
    """账号缩写：和别的账号比，只留不一样的那截（you.a / you.b → a / b），规矩和 tmux 版的账号按钮一样。"""
    me = current_account(cfg_dir)
    others = [current_account(d) for d in config_dirs() if d != Path(cfg_dir)]
    others = [o for o in others if o and o != me]
    if not me or not others:
        return me
    pre = os.path.commonprefix([me, *others])
    return me[pre.rfind(".") + 1:] if "." in pre else me


# ---- 模型和上下文 -------------------------------------------------------------

TAIL_BYTES = 4 * 1024 * 1024
CTX_HARD = 500_000       # 没配 autoCompactWindow 时的上下文目标线


def last_usage(path):
    """(tokens, model, effort) from the newest main-thread entry that says how big the context is.

    上下文 = 最新一条主线（非子 Agent）回复的 input + cache_creation + cache_read + output token；
    如果它之后有压缩边界，就用边界记下的压缩后大小。effort：每条 assistant 记录顶层的 "effort"。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return None, None, None
    model = effort = None
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("isSidechain"):
            continue
        if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
            post = (e.get("compactMetadata") or {}).get("postTokens")
            if post is not None:
                return int(post), model, effort
        msg = e.get("message") or {}
        if e.get("type") == "assistant" and msg.get("usage"):
            u = msg["usage"]
            model = model or msg.get("model")
            effort = effort or e.get("effort")
            if model == "<synthetic>":
                model = None
                continue
            n = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens"))
            return n, model, effort
    return None, model, effort


def short_model(mid):
    """claude-opus-5-5 → Opus 5.5, claude-haiku-4-5-20251001 → Haiku 4.5"""
    if not mid:
        return ""
    parts = mid.replace("claude-", "").split("[")[0].split("-")
    parts = [p for p in parts if not (p.isdigit() and len(p) == 8)]
    if not parts:
        return ""
    ver = ".".join(p for p in parts[1:] if p.isdigit())
    return (parts[0].capitalize() + (" " + ver if ver else "")).strip()


def model_ctx(path):
    """(`Opus 5.5/high`, `11% (111k)`)。百分比按模型的上下文窗口算：haiku 20 万，其余 100 万。"""
    tokens, mid, effort = last_usage(str(path))
    name = short_model(mid)
    if name and effort:
        name += "/" + effort
    if tokens is None:
        return name, ""
    window = 200_000 if mid and "haiku" in mid else 1_000_000
    return name, f"{tokens * 100 // window}% ({tokens // 1000}k)"


def auto_compact_at():
    """自动压缩的那条线。settings.json 里写了 autoCompactWindow 就用它；没写就退回 CTX_HARD。
    （settings.json 里的 autoCompactWindow 会被 Claude Code 自己改没，不能当成一定在。）
    关掉自动压缩就不画这条参照线。和 tmux 版一样读默认配置目录的 settings.json。"""
    try:
        d = json.loads((DEFAULT_CFG / "settings.json").read_text())
    except (OSError, ValueError):
        return CTX_HARD
    if not d.get("autoCompactEnabled", True):
        return None
    return d.get("autoCompactWindow") or CTX_HARD


# ---- 订阅额度（5h / 7d）-------------------------------------------------------

URL = "https://api.anthropic.com/api/oauth/usage"
WINDOWS = ("five_hour", "seven_day")
INTERVAL = 60 * int(os.environ.get("STATETRAIL_USAGE_REFRESH_MIN") or 30)
FORCE_GAP = 20            # 两次手动刷新至少隔多少秒：怎么连点也不会变成一串请求


def usage_file(cfg_dir):
    """一个配置目录一份读数。文件名带目录路径的哈希，两个账号互不覆盖。"""
    key = hashlib.sha256(str(Path(cfg_dir)).encode()).hexdigest()[:8]
    return USAGE_DIR / f"usage-{key}.json"


LIVE = False             # 适配层按配置 live_usage 打开：自己去查真实额度（读钥匙串里的令牌）。默认只读 Claude Code 的缓存


def cached_usage(cfg_dir):
    """Claude Code 自己缓存在 .claude.json 里的额度（cachedUsageUtilization），不碰任何凭据。

    它只在 Claude Code 自己去查（比如你跑 /usage）时才更新，可能是好几天前的：表头会写出多久前（fetched_at_ms）。
    重置时间已经过了的窗口，旧百分比不作数，当成没有读数（画成「?」）。缓存不是当前登录的这个账号的
    （换过 /login），整个不用。"""
    d = _claude_json(cfg_dir)
    c = d.get("cachedUsageUtilization") or {}
    me = (d.get("oauthAccount") or {}).get("accountUuid")
    if not c or (me and c.get("accountUuid") and c.get("accountUuid") != me):
        return None
    out = {"fetched_at_ms": c.get("fetchedAtMs") or 0, "source": "cache"}
    now = time.time()
    for w in WINDOWS:
        win = (c.get("utilization") or {}).get(w) or {}
        try:
            fresh = datetime.fromisoformat(win.get("resets_at") or "").timestamp() > now
        except (TypeError, ValueError):
            fresh = False
        out[w] = {"utilization": win.get("utilization") if fresh else None,
                  "resets_at": win.get("resets_at") if fresh else None}
    return out


def read_usage(cfg_dir):
    """表头用的额度：默认 Claude Code 的缓存；打开 LIVE 后读自己查到的（refresh_usage 写的那份）。"""
    if not LIVE:
        return cached_usage(cfg_dir)
    try:
        return json.loads(usage_file(cfg_dir).read_text())
    except (OSError, ValueError):
        return None


def _save(path, obj):
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


def keychain_service(cfg_dir):
    """存这个配置目录凭据的钥匙串条目。Claude Code 自己的叫法：默认那套是 "Claude Code-credentials"，
    别的目录在后面加目录路径（不带结尾斜杠）sha256 的前 8 位，两个账号同时登录互不覆盖。"""
    cfg = str(Path(cfg_dir))
    if Path(cfg) == DEFAULT_CFG:
        return "Claude Code-credentials"
    return "Claude Code-credentials-" + hashlib.sha256(cfg.encode()).hexdigest()[:8]


def _token(cfg_dir):
    """Claude Code 存的 OAuth access token：macOS 钥匙串里读，没有钥匙串就读 <配置目录>/.credentials.json。
    只在内存里用这一次，不写、不打印；过期了也不去续（续就得把凭据写回去），等 Claude Code 自己续。"""
    raw = ""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", keychain_service(cfg_dir), "-w"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            raw = r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not raw:
        try:
            raw = (Path(cfg_dir) / ".credentials.json").read_text()
        except OSError:
            return None, _t("找不到 Claude Code 的登录凭据")
    try:
        o = json.loads(raw).get("claudeAiOauth") or {}
    except ValueError:
        return None, _t("凭据格式认不出")
    if (o.get("expiresAt") or 0) / 1000 < time.time():
        return None, _t("令牌已过期，开一次 Claude Code 会自动续上")
    return o.get("accessToken"), None


def _fetch(cfg_dir):
    tok, err = _token(cfg_dir)
    if not tok:
        return None, err
    req = urllib.request.Request(URL, headers={
        "Authorization": "Bearer " + tok,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "statetrail",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.load(resp)
    except urllib.error.HTTPError as e:
        return None, _t("查询失败 HTTP {code}").format(code=e.code)
    except (OSError, ValueError) as e:
        return None, _t("查询失败（{err}）").format(err=type(e).__name__)
    return {w: {"utilization": (d.get(w) or {}).get("utilization"),
                "resets_at": (d.get(w) or {}).get("resets_at")} for w in WINDOWS}, None


def refresh_usage(cfg_dir, force=False):
    if not LIVE:                                # 没打开实时查询：一个请求都不发、不读钥匙串
        return
    """（LIVE 打开时）去查一次这个账号的真实额度（Claude Code 的 /usage 也是调这个接口，不是公开文档里的 API，变了就安静地失败）。

    自动刷新最多每 INTERVAL 一次（失败的也算），手动的两次至少隔 FORCE_GAP 秒；同一个账号有好几个
    轨迹同时在看时，用文件锁串起来，只发一个请求。要 ~2 秒，调用方放后台线程里跑。"""
    if not Path(cfg_dir).is_dir():
        return
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = usage_file(cfg_dir)
    now_ms = int(time.time() * 1000)
    with open(path.with_suffix(".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                              # 别的轨迹正在查
        try:
            live = json.loads(path.read_text())
        except (OSError, ValueError):
            live = {}
        last = max(live.get("fetched_at_ms") or 0, live.get("attempted_at_ms") or 0)
        if not force and now_ms - last < INTERVAL * 1000:
            return
        if force and now_ms - (live.get("attempted_at_ms") or 0) < FORCE_GAP * 1000:
            return
        live["attempted_at_ms"] = now_ms
        _save(path, live)                       # 先记下「在查」：表头显示「查询中…」
        usage, err = _fetch(cfg_dir)
        if usage:
            live.update(usage)
            live["fetched_at_ms"] = int(time.time() * 1000)
            live.pop("error", None)
        else:
            live["error"] = err
            live["error_at_ms"] = int(time.time() * 1000)
        _save(path, live)


def usage_stale(cfg_dir):
    if not LIVE:
        return False
    u = read_usage(cfg_dir) or {}
    return time.time() - max(u.get("fetched_at_ms") or 0, u.get("attempted_at_ms") or 0) / 1000 > INTERVAL
