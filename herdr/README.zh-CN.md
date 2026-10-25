# claude-trail

**Claude Code 会话的每一轮，贴在正在运行的 Claude 旁边：点一轮，Claude 就滚到那一轮。**

[herdr](https://herdr.dev) 插件。停在 Claude Code 窗格右边，按轮次列出这个会话：你问了什么，Claude 最终回复的开头。
点一轮，Claude 自己的屏幕就滚到那一轮的最终回复。上面是模型、账号，以及上下文、5 小时、7 天三条额度柱子。

[English](README.md)

![claude-trail 在 herdr 里：打开、点一轮跳转、拉窄、拉宽、点 Git 按钮](docs/demo.gif)

claude-code-history-viewer、claude-code-transcripts 这类工具是事后翻看会话的。
claude-trail 管的是你正在用的这个会话：贴在运行中的 Claude 旁边，跟着它走。

## 有什么

- **轨迹。** 每轮一行，点一行 Claude 就跳过去，通常不到一秒。`/compact` 之前的轮次收成一行。
  Claude 一开始干活、一停下，列表就刷新。
- **模型、账号、额度。** `ctx`、`5h`、`7d` 三条柱子，平时灰色，快出问题了才上色：额度柱子按「用得比时间走得快多少」变橙、变红，
  不是单看用了多少。两个 Claude 账号同时在线（各用一个 `CLAUDE_CONFIG_DIR`）也没问题，每个窗格显示自己那个账号的。
- **宽版和窄版。** 拖分隔线就能改宽度：30 列以上是完整的宽版，不到 30 列是窄版（状态、模型和账号的缩写、百分比、每轮一短行）。
  左上角的 `»` / `«` 一键在两种宽度之间切换。没有关闭按钮，窄版就是把它收起来的方式。
- **跟着 herdr。** 颜色取自你的 herdr 主题，状态点和 herdr 左侧 Agents 栏用同一套颜色；没起过名的 tab 会用 Claude 的会话名命名。
- **自己的按钮。** 最底下一行放你自己的工具，默认是 Git（lazygit）和文件（yazi），在 Claude 的目录里弹浮窗打开。

## 安装

需要：herdr 0.9.5+、Python 3.11+、fzf 0.71+（实测 0.74）。可选：lazygit、yazi（默认的两个按钮用）。
macOS 自带的 Python 是 3.9，要先装一个新的：

```bash
brew install python fzf          # 要默认按钮的话再加 lazygit yazi
herdr plugin install <owner>/<repo>
```

缺了什么，claude-trail 会说出来（herdr 通知，或者在它的窗格里写出来），不会静默失败。

然后开一个有 Claude Code 的 tab，轨迹会自己在右边打开。想用快捷键切换，在 `~/.config/herdr/config.toml` 里绑上动作，
再执行 `herdr server reload-config`：

```toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "claude-trail.toggle"
description = "Claude trail：打开，或在宽版 / 窄版之间切换"
```

Claude Code 要用全屏界面（`/tui fullscreen`），跳转滚的就是它。

## 配置

`herdr plugin config-dir claude-trail` 打印的目录下的 `config.toml`。每一项都可以不写；文件不存在或写错了就用默认值。

```toml
auto_open = true          # tab 里正好有一个 Claude 时自己打开
default_mode = "wide"     # 打开时多宽："wide" 或 "narrow"
width = 0.34              # 宽版占 Claude 那一格的比例（总会给 Claude 留 110 列）
icons = "nerd"            # "nerd"（Nerd Font）、"unicode"（普通符号）或 "text"（全写成字）
lang = ""                 # "en" / "zh"；不写就按系统 locale
tab_names = true          # 没起过名的 tab 用 Claude 的会话名命名

# 最底下一行的按钮。写了 [[buttons]] 就整组替换默认的两个。
# 有图标就只画图标，没有图标（或 icons = "text"）就只写 label。
[[buttons]]
label = "Git"
command = "lazygit"       # 在浮窗里跑，目录是 Claude 那一格的（经 sh -c）
icon = "git"              # 图标名（git、files）或者直接写一个字形

[[buttons]]
label = "文件"
command = "yazi"
icon = "files"

# 按钮也可以不跑命令，改成调别的 herdr 插件的动作：
# [[buttons]]
# label = "左栏"
# action = "herdr-sidebar.open-sidebar"
```

## 读什么、动什么

- **读** Claude Code 的对话记录（`<配置目录>/projects/*/<会话>.jsonl`）和 `.claude.json`（取账号名）。从不写它们。
- **额度：** 为了显示真实的 5 小时 / 7 天用量，它调 Claude Code 的 `/usage` 也在调的接口
  （`api.anthropic.com/api/oauth/usage`），最多 30 分钟一次，用的是 Claude Code 存在 macOS 钥匙串
  （或 `<配置目录>/.credentials.json`）里的 OAuth 令牌。令牌只在这一次请求里放在内存中，从不写下来、不打印、不刷新。
  这个接口不是公开文档里的 API，变了的话柱子就安静地不更新。
- **发键** 给 Claude 窗格只为滚屏（PageUp / PageDown / Ctrl+Home / Ctrl+End / 滚轮），从不往输入框里打字。
- 它自己的状态放在插件的状态目录（`~/.local/state/herdr/plugins/claude-trail/`）。

## 已知限制

- **窄版最窄约 15 列。** herdr 把分割比例限制在 0.1–0.9，窗格最窄是它那条分割的 10%。
- **右键菜单里没有入口。** herdr 0.9.5 的窗格菜单是写死的，插件加不进去
  （[herdrdev/herdr#5550](https://github.com/herdrdev/herdr/issues/5550)）。所以收起轨迹靠窄版，不靠关闭按钮。
- **一个 tab 一个 Claude。** 同一个 tab 里有 agent teams 的队友时不自动打开。
- **Claude 屏幕上已经没有的轮次**（`/compact` 之后，或续接的会话里较早的部分）会标出来，点了直接告诉你，不会一直翻。
- **整列高**指的是 Claude 那一格的整列高；你把 Claude 那一格上下切开的话，轨迹只有 Claude 那部分高。
- 目前只支持 Claude Code。读对话记录是单独一层（`core/transcript.py`），以后可以接别的 agent。

## Roadmap

- 在列表里标出 Claude 屏幕现在停在哪一轮
- 每轮的 token 用量；展开一轮里的工具调用；标出 `/compact` 发生的位置
- 跨会话搜索
- 别的 agent（Codex、OpenCode）

## 怎么做的

每个轨迹是一个小 Python 进程加一个 fzf。它订阅 herdr 里自己那个 Claude 窗格的事件（状态变化、窗格关闭），
没事件时就睡着，空闲时约占单核 0.1%。读 Claude 的屏幕、让它滚动都走 herdr 的 socket。
`core/` 放和 herdr 无关的部分（对话记录、额度、画面、跳转算法），`trail.py` 是 herdr 这一侧。

## 许可证

MIT。`themes.json` 里的主题配色提取自 herdr（Apache-2.0）。
