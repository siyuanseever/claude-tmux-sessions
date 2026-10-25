"""theme.py — 轨迹的颜色跟着 herdr 的主题走，和 herdr 自己的界面（左侧 Agents 栏的状态点、选中行、边框）一致。

读 herdr 的 config.toml 里 [theme] name 和 [theme.custom] 的覆盖，在 themes.json（从 herdr 源码提取的 18 套调色板）
里查出这套主题，换算成 ANSI 颜色交给 core.render，再给 fzf 一串 --color。

调色板的语义照 herdr（v0.9.5 src/app/state.rs:35-74）：green 完成 / 空闲，yellow 工作中，red 等你，
peach 警告，accent 高亮和选中边框，overlay0 次要文字，surface1 分隔线。
"""
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
THEMES = json.loads((HERE / "themes.json").read_text())["themes"]
DEFAULT = "catppuccin"     # herdr 的默认主题

# herdr 接受的主题别名（src/config/theme.rs canonical_theme_name）
ALIASES = {"catppuccin-mocha": "catppuccin", "latte": "catppuccin-latte", "light": "catppuccin-latte",
           "tokyonight": "tokyo-night", "tokyo-day": "tokyo-night-day", "tokyonight-day": "tokyo-night-day",
           "gruvbox-dark": "gruvbox", "onedark": "one-dark", "onelight": "one-light",
           "solarized-dark": "solarized", "lotus": "kanagawa-lotus", "rosepine": "rose-pine",
           "rosepine-dawn": "rose-pine-dawn", "dawn": "rose-pine-dawn"}

# "terminal" 主题用的是终端自己的 16 色：ratatui 的颜色名 → (SGR 前景色, fzf 的颜色名)
NAMED = {"black": (30, "black"), "red": (31, "red"), "green": (32, "green"), "yellow": (33, "yellow"),
         "blue": (34, "blue"), "magenta": (35, "magenta"), "cyan": (36, "cyan"), "gray": (37, "white"),
         "darkgray": (90, "bright-black"), "lightred": (91, "bright-red"), "lightgreen": (92, "bright-green"),
         "lightyellow": (93, "bright-yellow"), "lightblue": (94, "bright-blue"),
         "lightmagenta": (95, "bright-magenta"), "lightcyan": (96, "bright-cyan"), "white": (97, "bright-white"),
         "reset": (39, "-1")}


def herdr_config():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    path = Path(base) / "herdr" / "config.toml"
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def palette(conf=None):
    """当前 herdr 主题的调色板 {token: "#rrggbb" 或颜色名}，加上 [theme.custom] 的覆盖。"""
    conf = herdr_config() if conf is None else conf
    th = conf.get("theme") or {}
    name = str(th.get("name") or DEFAULT).lower().replace(" ", "-").replace("_", "-")
    name = ALIASES.get(name, name)
    pal = dict(THEMES.get(name) or THEMES[DEFAULT])
    for k, v in (th.get("custom") or {}).items():
        if k in pal and isinstance(v, str):
            pal[k] = v.strip().lower()
    return pal


def _rgb(c):
    c = c.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{6})", c)
    if m:
        return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"#([0-9a-f]{3})", c)
    if m:
        return tuple(int(ch, 16) * 17 for ch in m.group(1))
    return None


def ansi(c):
    """调色板里的一个颜色 → ANSI 前景色序列。"""
    rgb = _rgb(c)
    if rgb:
        return "\033[38;2;%d;%d;%dm" % rgb
    return "\033[%dm" % NAMED.get(c.strip().lower(), NAMED["reset"])[0]


def fzf_colour(c):
    rgb = _rgb(c)
    if rgb:
        return "#%02x%02x%02x" % rgb
    return NAMED.get(c.strip().lower(), NAMED["reset"])[1]


def apply(render):
    """把 core.render 的颜色整套换成 herdr 主题的，返回给 fzf 的 --color 值。"""
    p = palette()
    render.set_colors(
        USED_C=ansi(p["overlay1"]),    # 柱子正常时的填充
        GREY=ansi(p["overlay0"]),      # 标签、次要文字
        DIMC=ansi(p["surface1"]),      # 柱子底纹 ░、很淡的字
        WARN_C=ansi(p["peach"]),       # 该留意了
        ALERT_C=ansi(p["red"]),        # 真的要注意了
        TIME_C=ansi(p["blue"]),        # 匀速线 / 自动压缩线
        CYAN=ansi(p["teal"]),          # 主界面正显示着的轮次
        BUSY_C=ansi(p["yellow"]),      # 状态：工作中
        DONE_C=ansi(p["green"]),       # 状态：完成 / 空闲
    )
    sel = "-1" if p["selection_bg"] == "reset" else fzf_colour(p["selection_bg"])
    return ",".join([f"bg+:{sel}", f"pointer:{fzf_colour(p['accent'])}", "gutter:-1",
                     f"border:{fzf_colour(p['surface1'])}", f"separator:{fzf_colour(p['surface1'])}",
                     f"scrollbar:{fzf_colour(p['surface1'])}"])
