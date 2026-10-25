"""i18n.py — 界面文字的中英切换（Python 这一半；shell 那一半是同目录的 i18n.sh）。

中文原文就是键（像 gettext 的 msgid）：zh 时 t() 原样返回，所以中文界面和接入之前一字不差；
en 时去同目录的 en.tsv 里查，查不到也原样返回（宁可漏一句中文，不能空掉）。

    from i18n import t, dur
    t("等确认")                      → "等确认" / "Waiting"
    t("完成 {d}前").format(d=dur(s)) → "完成 4分钟前" / "done 4m ago"

带占位符的键照 str.format 的写法；zh 时 format 的就是原文，结果和原来的 f-string 一样。

取语言的顺序：环境变量 CLAUDE_TMUX_LANG > ~/.config/claude-tmux/lang（install.sh --lang 写的）
> 默认 zh（老用户没重装也保持中文）。只认 en / zh，别的值都当 zh。

只在 en 时才读词表文件，zh 一个字节都不多读。用这个模块的脚本都在仓库里、经由
~/.claude/hooks 下的软链跑，所以 import 前要按 realpath 找回仓库的 lib/（见各调用处）。
"""
import os

_HERE = os.path.dirname(os.path.realpath(__file__))
TSV = os.path.join(_HERE, "en.tsv")


def lang_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "claude-tmux", "lang")


def _norm(v):
    v = (v or "").strip().lower()
    return "en" if v.startswith("en") else ("zh" if v else "")


def current():
    v = _norm(os.environ.get("CLAUDE_TMUX_LANG"))
    if not v:
        try:
            with open(lang_file(), encoding="utf-8") as f:
                v = _norm(f.readline())
        except OSError:
            v = ""
    return v or "zh"


LANG = current()
EN = LANG == "en"
_table = None


def load(path=TSV):
    """en.tsv → {中文: English}。一行一条：键、一个 tab、值；空行和 # 开头的行跳过。"""
    table = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                k, v = line.split("\t", 1)
                table[k] = v
    except OSError:
        pass
    return table


def t(s):
    """界面上的一句话：zh 原样，en 查表（查不到也原样）。"""
    global _table
    if not EN:
        return s
    if _table is None:
        _table = load()
    return _table.get(s, s)


def dur(secs):
    """一段时长：zh `12秒` / `4分钟` / `1.5小时`，en `12s` / `4m` / `1.5h`。"""
    secs = max(0, int(secs))
    if secs < 60:
        return t("{n}秒").format(n=secs)
    if secs < 3600:
        return t("{n}分钟").format(n=secs // 60)
    return t("{n}小时").format(n=f"{secs / 3600:.1f}".rstrip("0").rstrip("."))


def join(items):
    """列名字用的顿号：zh `a、b`，en `a, b`。"""
    return (", " if EN else "、").join(items)
