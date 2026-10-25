#!/usr/bin/env bash
# Multi-line usage panel for the picker's footer, /usage-style. Top:
# the REAL rate-limit utilization bars (5h / 7d windows + reset times)
# read from cachedUsageUtilization in ~/.claude.json — Claude Code
# caches its /usage API responses there, so no OAuth calls needed, just
# a staleness note. Below: today's tokens per model as bars and a
# 14-day sparkline. Today + 5h token counts are computed live from
# transcripts (assistant messages since local midnight, deduped by
# message id; input + cache-write + output, cache reads excluded) —
# stats-cache.json often lags a day, so it's only used for the
# historical sparkline. Runs once per picker launch, in the background
# (bg-transform-footer), so cost doesn't delay startup.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
[ -L "$SCRIPT_PATH" ] && SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
CT_LIB="$(cd "$(dirname "$SCRIPT_PATH")/../lib" && pwd)" python3 - <<'PYEOF'
import glob
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.environ.get("CT_LIB") or ".")
from i18n import t as _t, EN    # 界面文字：zh 原样，en 查 lib/en.tsv

DIM = "\033[2m"
CYAN = "\033[36m"
RESET = "\033[0m"
BAR_W = 24
SPARK = "▁▂▃▄▅▆▇█"

def fmt_tok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)

def short_model(m):
    return (m or "?").replace("claude-", "").replace("-20251001", "")

def bar(frac, width=BAR_W, colour=None):
    frac = max(0.0, min(1.0, frac))
    fill = round(frac * width)
    if colour is None:
        colour = CYAN
    return colour + "█" * fill + RESET + DIM + "░" * (width - fill) + RESET


def limit_colour(pct):
    if pct >= 90:
        return "\033[31m"  # red — about to hit the limit
    if pct >= 70:
        return "\033[33m"  # yellow — getting close
    return CYAN

now = time.time()
lt = time.localtime(now)
midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
cut5 = now - 5 * 3600
iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
midnight_iso, cut5_iso = iso(midnight), iso(cut5)
scan_since = min(midnight, cut5)

day_by_model = defaultdict(int)
win = 0
seen = set()
for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
    try:
        if os.path.getmtime(path) < scan_since:
            continue
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        continue
    for line in lines:
        if '"usage"' not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "assistant":
            continue
        ts = obj.get("timestamp") or ""
        if ts < midnight_iso and ts < cut5_iso:
            continue
        msg = obj.get("message", {})
        mid = msg.get("id")
        if mid:
            if mid in seen:
                continue
            seen.add(mid)
        u = msg.get("usage", {})
        tok = (
            (u.get("input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
            + (u.get("output_tokens") or 0)
        )
        if ts >= midnight_iso:
            day_by_model[msg.get("model")] += tok
        if ts >= cut5_iso:
            win += tok

out = []

# ---- Real rate-limit utilization, cached by Claude Code itself ----
LIMIT_LABELS = {
    "five_hour": "5小时窗口",
    "seven_day": "7天窗口 ",
    "seven_day_opus": "7天 Opus",
    "seven_day_sonnet": "7天 Sonnet",
    "extra_usage": "额外用量 ",
}
fetched_note = None
try:
    from datetime import datetime
    cfg = json.load(open(os.path.expanduser("~/.claude.json")))
    cached = cfg.get("cachedUsageUtilization") or {}
    util = cached.get("utilization") or {}
    for key, v in util.items():
        if not isinstance(v, dict):
            continue
        pct = v.get("utilization")
        if pct is None:
            continue
        resets = ""
        try:
            dt = datetime.fromisoformat(v["resets_at"]).astimezone()
            fmt = "%H:%M" if dt.date() == datetime.now().astimezone().date() else "%m-%d %H:%M"
            resets = f" {DIM}· {_t('{when} 重置').format(when=dt.strftime(fmt))}{RESET}"
        except Exception:
            pass
        label = LIMIT_LABELS.get(key, key)
        if EN:   # 中文那几个标签末尾自带的空格是对齐用的；英文另按 9 格补齐
            label = _t(label.rstrip()).ljust(9)
        out.append(
            f"{label}  {bar(pct / 100, colour=limit_colour(pct))} "
            f"{limit_colour(pct)}{pct:3.0f}%{RESET} {_t('已用')}{resets}"
        )
    if cached.get("fetchedAtMs"):
        age = now - cached["fetchedAtMs"] / 1000
        if age < 3600:
            fetched_note = _t("{n}分钟前").format(n=f"{age / 60:.0f}")
        else:
            fetched_note = _t("{n}小时前").format(n=f"{age / 3600:.1f}")
except Exception:
    pass

day_total = sum(day_by_model.values())

out.append(f"{_t('今日')}  {fmt_tok(day_total)} tok  {DIM}·{RESET}  {_t('近5h')} ≈{fmt_tok(win)} tok")
if day_total:
    # Per-model bars, scaled as share of today — they visually sum to
    # one full bar's worth.
    for m, n in sorted(day_by_model.items(), key=lambda kv: -kv[1])[:4]:
        if n < 1000:
            continue
        out.append(
            f"  {short_model(m):<11} {fmt_tok(n):>6}  {bar(n / day_total)}"
            f" {DIM}{n / day_total * 100:3.0f}%{RESET}"
        )

# 14-day sparkline from Claude Code's own stats cache (fine for history —
# it only lags on the current day, which we computed live above).
try:
    with open(os.path.expanduser("~/.claude/stats-cache.json")) as f:
        stats = json.load(f)
    today_str = time.strftime("%Y-%m-%d")
    days = [
        (d.get("date"), sum(d.get("tokensByModel", {}).values()))
        for d in stats.get("dailyModelTokens", [])
        if d.get("date") and d.get("date") < today_str
    ][-13:]
    days.append((today_str, day_total))
    peak_date, peak = max(days, key=lambda kv: kv[1])
    if peak:
        spark = "".join(
            SPARK[min(len(SPARK) - 1, int(v / peak * (len(SPARK) - 1) + 0.5))]
            for _, v in days
        )
        avg = sum(v for _, v in days) / len(days)
        out.append(
            f"{_t('近{n}天').format(n=len(days))}  {CYAN}{spark}{RESET}  "
            f"{DIM}{_t('峰值')} {fmt_tok(peak)} ({peak_date[5:]}) · {_t('日均')} {fmt_tok(avg)}{RESET}"
        )
except Exception:
    pass

if fetched_note:
    out.append(f"{DIM}{_t('※ 额度百分比为 Claude Code {note}缓存的 /usage 数据 · 其余为本地实时统计').format(note=fetched_note)}{RESET}")
print("\n".join(out))
PYEOF
