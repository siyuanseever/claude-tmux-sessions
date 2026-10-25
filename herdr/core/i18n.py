"""i18n.py — 界面文字的中英切换。从 tmux 版 lib/i18n.py 搬来，只留 Python 这一半。

中文原文就是键（像 gettext 的 msgid）：zh 时 t() 原样返回；en 时去同目录的 en.tsv 里查，查不到也原样返回
（宁可漏一句中文，不能空掉）。

    from core.i18n import t
    t("等确认")                      → "等确认" / "Waiting"

取语言的顺序：环境变量 CLAUDE_TRAIL_LANG > set_lang()（插件读配置里的 lang 后调）> 系统 locale
（LC_ALL / LC_MESSAGES / LANG 以 zh 开头就是中文）> en。只认 en / zh。
"""
import os

_HERE = os.path.dirname(os.path.realpath(__file__))
TSV = os.path.join(_HERE, "en.tsv")


def _norm(v):
    v = (v or "").strip().lower()
    return "en" if v.startswith("en") else ("zh" if v.startswith("zh") else "")


def _from_locale():
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(k)
        if v:
            return "zh" if v.lower().startswith("zh") else "en"
    return "en"


LANG = _norm(os.environ.get("CLAUDE_TRAIL_LANG")) or _from_locale()
_table = None


def set_lang(lang):
    """配置文件里写了 lang 就用它（环境变量 CLAUDE_TRAIL_LANG 还是优先）。"""
    global LANG
    if not os.environ.get("CLAUDE_TRAIL_LANG") and _norm(lang):
        LANG = _norm(lang)


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
    if LANG != "en":
        return s
    if _table is None:
        _table = load()
    return _table.get(s, s)
