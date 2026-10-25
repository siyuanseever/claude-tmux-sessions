# claude-trail（herdr 插件，第一阶段原型，名字发布前再定）

Claude Code 会话轨迹，停在 Claude 窗格右边、占满整列高：每轮一项，点一项，Claude 窗格就滚到那一轮的最终回复。
有宽版和窄版两种画法，按轨迹窗格的实际宽度自动切换，两种都能长期用。

验收结果和数字见 [../docs/herdr-plugin/PHASE1-RESULT.md](../docs/herdr-plugin/PHASE1-RESULT.md)。

## 本地装

```bash
herdr plugin link "$PWD/herdr"          # 在仓库根目录
herdr plugin action invoke claude-trail.toggle
```

绑个快捷键（`~/.config/herdr/config.toml`，然后 `herdr server reload-config`）：

```toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "claude-trail.toggle"
description = "Claude trail"
```

## 两种画法

宽版（轨迹窗格 ≥ 30 列）：

```
 »  ⇊   ±   ▤   ⟳ 3 分钟前          ← 按钮：拉到窄版 / 主界面到最底 / 配置里的按钮（Git、文件）/ 刷新额度
 41 轮
 Opus 5.5/high · siyuan.l2
 ctx ███████░░░│░░░░░  35% 355k       ← 柱子规则和 tmux 版一样（就是同一份代码）
 5h  ████░░░░░░│░░░░░  27% 2h25m
 7d  ██│░░░░░░░░░░░░░   6% 6d00h
 ────────────────────────────
 #40 10/15 22:05 还有就是你最下面那个表格…    ← 每轮两行：你说的话 + Claude 回复的开头
     对，上一版是我写漏了：占比那列…
```

窄版（不到 30 列，最窄约 15 列）：

```
 «            ← 拉回宽版（和宽版的 » 在同一个位置）
 ✓            ← 状态：工作中 / 等你 / 完成
 ✦ Opus5.5/hi
 @ l2         ← 账号：只留和别的账号不一样的那截
 ◧   35%      ← ctx / 5h / 7d：平时灰，快到上限才上色
 ◷   27%
 ▦    6%
 ─────────
 #40 还有就是   ← 每轮一行：轮号 + 你那句话的开头，点一行照样跳转
 #41 那我们
 ─────────
  ±   ▤       ← 配置里的按钮，固定在底部
```

（上面用的是 `icons = "unicode"` 的符号；默认的 Nerd Font 图标长得不一样。）

窄版放不下的字段按 账号 → 模型 → 7d 的顺序丢。

## 行为

- **默认打开**：切到一个 tab、或 tab 里起了 Claude，tab 里正好有一个 Claude、它够宽（≥120 列）、
  还没有轨迹时，自动在它右边开。没有 Claude、或有好几个（agent teams）时不开
- **宽度**：在 herdr 里拖分隔线就能改，宽版 / 窄版跟着实时切换（窗格收到 SIGWINCH 就重画，不轮询）。
  左上角的 » / « 一键拉到窄版、拉回宽版；`claude-trail.toggle` 动作（快捷键）也一样
- **没有「关闭」**：窄版就是省地方的形态，任何时候都有鼠标点得到的入口（herdr 的右键菜单是写死的，
  插件动作进不去）。Claude 那一格关了、或里面的 Claude 退出 15 秒后，轨迹跟着退出
- 窄版最窄是这条分割的 10%（约 15 列）：herdr 把分割比例限制在 0.1–0.9
- 只挪 Claude 和轨迹之间那条线，旁边的 herdr-sidebar 等窗格不动
- Claude 状态一变（开始干活 / 停下 / 等确认）列表就刷新
- 本窗格的右键固定交给 herdr 菜单（每次画面重起时设一次），误点「Send right-clicks to pane」也会被改回来

## 配置

`herdr plugin config-dir claude-trail` 打印的目录下的 `config.toml`。文件不存在就全用默认值；
写错的项回退到默认值。

```toml
auto_open = true          # 切到有 Claude 的 tab 时自动打开
default_mode = "wide"     # 打开时多宽："wide"（下面 width 那么宽）或 "narrow"（最窄）
width = 0.34              # 宽版占 Claude 那一格的比例
icons = "nerd"            # "nerd"：Nerd Font 图标；没装 Nerd Font 用 "unicode" 普通符号，或 "text" 全写成字

# 按钮：在 Claude 那一格的目录里，用本插件的浮窗跑 command（经 sh -c）。命令找不到会在表头提示。
# 写了 [[buttons]] 就整组替换默认的两个。icon 可选：写图标名（git、files）按上面 icons 那一档取字形，
# 也可以直接写一个字形。图标和 label 只显示一个：有图标就只画图标，icons = "text" 或没配图标时只写 label
[[buttons]]
label = "Git"
command = "lazygit"
icon = "git"

[[buttons]]
label = "文件"
command = "yazi"
icon = "files"
```

别人可以换成 gitui / tig、ranger / nnn，或者加自己的按钮（比如 `btop`）。

## 依赖

- herdr 0.9.5+、python3（3.11+，要 `tomllib`）、fzf 0.74+（用到 `--listen=<unix socket>`、`click-header`、`click-footer`、`--id-nth`）
- 按钮默认用 lazygit、yazi，没装就提示
- 仓库里的 `sidebar/`、`hooks/ctx_to_tmux.py`、`lib/`：解析对话记录、宽版表头直接 import 它们
- 额度数据来自 `bin/usage-refresh.py`（经 `~/.claude/hooks/usage-refresh.py` 软链，`install.sh` 装）

## 文件

- `herdr-plugin.toml`：一个动作（toggle）、两个事件钩子（自动打开）、轨迹窗格（`placement = "split"`）、
  按钮用的浮窗（`placement = "popup"`）
- `trail.py`：`toggle` / `auto` / `pane` 三个子命令。状态在 `~/.local/state/herdr/plugins/claude-trail/`
  （`run/` 每个轨迹的登记和 FIFO，`jump.log` 每次跳转的用时，`events.log` 自动打开收到的事件，
  `pane.err` 窗格程序的报错；`touch debug` 会把每条点击命令也记进 `pane.err`）
