# 第一阶段结果：轨迹侧栏做成 herdr 插件（claude-trail）

写于 2026-10-25，herdr 0.9.5、fzf 0.74.4、Python 3.12.4，分支 `herdr-plugin`（从 `dcba8c0` 分出）。
交接说明见 [HANDOFF.md](HANDOFF.md)，代码在仓库的 [`herdr/`](../../herdr/)。

## 结论

**做得成，体验追得上 tmux 版，有几处更好。** 7 条验收标准全部达成。

- 跳转更快：最早一轮 0.22–0.25 秒，tmux 版 0.47–0.58 秒；最新一轮 0.002 秒，tmux 版 2.3 秒。
  原因是读屏 / 发键走 herdr 的 socket，一次 0.3 毫秒（tmux 一次约 8 毫秒），而且每次点击不用新起 python。
- 更省 CPU：空闲时一个侧栏 10 分钟约 0.6 秒（约 0.1% 单核）。tmux 版是一个常驻进程每秒轮询所有窗格。
- 不要钩子：会话 id 直接问 herdr（`pane.get` 的 `agent_session`）。账号从 Claude 自己写的
  `<配置目录>/sessions/<pid>.json` 判断。tmux 版要等 Claude 触发一次钩子才知道会话 id，续接后没说话的会话找不到。

herdr 这边的主要限制是：

- 插件不能自己画界面，所以列表还是 fzf
- `send-keys` 不认 Home / End / PageUp / PageDown，要自己发转义序列
- 分割比例被限制在 0.1–0.9，窄版最窄约 15 列，做不到 2–3 列
- 右键菜单里放不进插件动作

这些都绕过去了，见下面「herdr 的限制」。

验收期间按用户的要求（主会话转达）加了几样东西，见「第一阶段里追加的需求」：

- 默认自动打开
- 宽版 / 窄版按宽度自动切换，没有「关闭」
- 配置文件里的按钮（默认 lazygit、yazi）
- 三档图标

## 验收标准逐条

### 1. 打开后右边出现整列高的侧栏，宽约 34%，Claude 窗格不乱 — 达成

`herdr pane layout` 的输出（列宽 × 行高）：

| tab | 布局 | 说明 |
|---|---|---|
| 测试 tab（herdr-sidebar 停在左边） | `w1:pH 32x45 \| w1:pC 99x45 \| w1:p0 51x45` | 51 / (99+51) = 34%，高 45 = 整个 tab 高 |
| l2 测试 tab（herdr-sidebar 停在右边） | `w1:pW 99x45 \| w1:pZ 51x45 \| w1:pX 32x45` | 右边的 herdr-sidebar 保持 32 列 |
| 用户的 tab 1、tab 2（只有 Claude） | `w1:p1 120x45 \| w1:p1C 62x45` | 62 / 182 = 34% |

- 开法：`plugin.pane.open`（`placement = "split"`，`direction = "right"`，`focus = false`），
  再 `pane.resize` 把中间那条线推到 66 / 34。`plugin.pane.open` 没有比例参数，开出来是对半分。
- Claude 窗格内容不乱：开侧栏只改宽度，Claude 自己按新宽度重排（和 tmux 版一样）。
- 踩过的坑：第一版对**侧栏**做 `resize right`，在 herdr-sidebar 停在右边的 tab 里，把 herdr-sidebar
  从 32 列挤成了 18 列，侧栏自己还是对半分。规律是 resize 挪的是窗格在那个方向上的那条边。
  所以要缩窄侧栏，对 Claude 做 `right`；要加宽侧栏，对侧栏做 `left`。已修，herdr-sidebar 的宽度已恢复。

### 2. 列出的轮数和 tmux 版一致，`~/.claude-l2` 的会话也能列 — 达成

| 会话 | 账号 | 插件版 | tmux 版 `parse_session` | 表头 |
|---|---|---|---|---|
| `aa325ab9`（T Max侧边轨迹滚动同步） | 默认 `~/.claude` | 41 轮 | 41 轮 | `Opus 5.5/high · siyuan.l2`，ctx 35% 355k |
| `837ce5fd`（How to earn money） | `CLAUDE_CONFIG_DIR=~/.claude-l2` | 28 轮 | 28 轮 | `Opus 5.5/high · siyuan.l`，5h 1%、7d 12% |

- 账号的判断：`pane.process_info` 找到 Claude 的 pid（74634），`~/.claude-l2/sessions/74634.json` 存在、
  `~/.claude/sessions/74634.json` 不存在，所以是 l2。5h 1% / 7d 12% 和 `~/.claude-l2/tmux-usage-live.json` 一致。
- 对话记录按 `<配置目录>/projects` 找，默认目录和 `~/.claude-*` 都找。两个目录的 `projects` 在这台机器上
  是同一个软链（`~/.claude-l2/projects -> ~/.claude/projects`）。
- 两边一致是因为插件直接 import tmux 版的 `trail_daemon.Transcript`，切轮的规则是同一份代码。

### 3. 点任意一轮，Claude 滚到那一轮的最终回复；最早、中间、最新各测一次，和 tmux 版比 — 达成

同一个会话（41 轮），Claude 窗格都是 99×45，每次先回到最底再点。插件版数字来自
`~/.local/state/herdr/plugins/claude-trail/jump.log`；tmux 版是在后台 tmux 会话里，对同样大小的窗口跑
`sidebar/claude_trail.py jump`，从命令开始到结束计时（含起 python，真实点击就是这样起的）。

| 目标 | 插件版（两次） | tmux 版（两次） | 回复停的行（插件 / tmux） |
|---|---|---|---|
| #1 最早 | 0.220 / 0.247 秒 | 0.584 / 0.474 秒 | 第 2 / 第 4 行 |
| #21 中间 | 0.324 / 0.355 秒 | 0.574 / 0.611 秒 | 第 4 / 第 4 行 |
| #41 最新 | 0.003 / 0.002 秒 | 2.322 / 2.321 秒 | 第 13 / 第 15 行（最后一轮到不了顶，内容就到这儿） |

- 真实鼠标路径也测了：往侧栏窗格发一个 SGR 鼠标点击，点 #40，0.92 秒跳到，最终回复停在顶部。
- 跳转算法照搬 `claude_trail.cmd_jump`，只换了读屏 / 发键。另外加了一处优化：屏幕上没有
  「Jump to bottom」就说明已经在最底，不再发 Ctrl+End、也不再往下推。改之前，最新一轮要 1.6–1.8 秒，
  每次「按了没动」都要空等 0.6 秒才确认到头；tmux 版也有同样的问题，第二阶段可以顺手改。
- 用户自己在 herdr 里用的记录（自己的会话 `52eca44c`）：点第 1 轮 1.27 秒，点最新一轮 0.002 秒。

### 4. 表头有模型、账号，以及 ctx / 5h / 7d 三条柱子，规则和 tmux 版一样 — 达成

- 渲染就是 tmux 版的 `claude_trail.status_lines`（同一个函数），颜色和阈值规则自然一样。
- 同一个会话两边并排读屏：tmux 版 `Opus 5.5/high · siyuan.l2`、`ctx 35% 355k`、`5h 27% 2h20m`、`7d 6% 6d00h`；
  插件版 `Opus 5.5/high · siyuan.l2`、`ctx 35% 355k`、`5h 27% 2h25m`、`7d 6% 6d00h`
  （5h 差 5 分钟是两次截屏相隔 5 分钟，倒计时在走）。
- tmux 版的模型 / 上下文来自 `ctx_to_tmux.py` 钩子写的 tmux 窗格选项；插件版直接调它的 `last_usage` 读对话记录尾巴。
- 额度旧了（默认 30 分钟）照样让 `usage-refresh.py` 去查，判据和 `trail_daemon.read_usage` 一样。

### 5. Claude 空闲时，插件进程 10 分钟 CPU 增量合计 ≤ 6 秒 — 达成

`ps -o time`，测量脚本在 scratchpad 的 `cpu.sh`。

第一轮（13:19:50–13:29:50，两个侧栏，各对着一个空闲的 Claude）：

| 进程 | 开始 | 结束 | 增量 |
|---|---|---|---|
| python（对 `w1:pC`） | 0:00.07 | 0:00.14 | 0.07 秒 |
| fzf（对 `w1:pC`） | 0:00.09 | 0:00.66 | 0.57 秒 |
| python（对 `w1:pW`） | 0:00.05 | 0:00.10 | 0.05 秒 |
| fzf（对 `w1:pW`） | 0:00.09 | 0:00.64 | 0.55 秒 |
| 测量中途用户在自己的 Claude 上开的第三个侧栏（全程在用，不算空闲） | — | 0.22 + 0.48 | 0.70 秒 |

两个空闲侧栏合计 1.24 秒 / 10 分钟；连上用户在用的那个，合计 1.94 秒，都低于 6 秒。
每个空闲侧栏约 0.6 秒 / 10 分钟（约 0.1% 单核），大头是 fzf 自己。

第二轮（13:41:22–13:51:22，当时的代码，含一个「收起成细条」的轨迹）：

| 进程 | 开始 | 结束 | 增量 | 这 10 分钟里 |
|---|---|---|---|---|
| python（测试 Claude `w1:p13`，收起态，没有 fzf） | 0:00.10 | 0:00.12 | 0.02 秒 | Claude 空闲 |
| python + fzf（用户的 `w1:p1`） | 0:00.07 + 0:00.01 | 0:00.24 + 0:00.29 | 约 0.45 秒 | 用户中途收起又展开过一次（fzf 换成了新的 11380） |
| python + fzf（我自己的 `w1:p2`） | 0:00.06 + 0:00.01 | 0:00.16 + 0:00.61 | 0.70 秒 | 对着正在干活的我，状态一直在变 |

合计约 1.2 秒 / 10 分钟。之后「收起成细条」改成了窄版（窄版也是 fzf），空闲时的开销应当和宽版一样，
约 0.6 秒 / 10 分钟。**窄版没有单独再测一轮 10 分钟**，第二阶段发布前补测。


自动打开的事件钩子每次切 tab 起一个 python，实测每次约 0.04 秒 CPU（`/usr/bin/time -p`，3 次都是 0.03–0.04 秒）。
空闲时没有事件就不跑；快速连续切 tab 时会多起几次（日志里有 1 秒内切了十来次的情况）。

### 6. 切到别的工作区再切回来，侧栏还在；关掉侧栏没有残留进程 — 达成

- 新建临时工作区 `w5`，`workspace focus w5`（`workspace list` 显示焦点在 w5），再 `tab focus w1:t2` 切回来。
  前后三个侧栏进程的 pid 都是 `57389 76437 98060`，三个 tab 的布局原样。临时工作区已关掉。
- 关闭：toggle 关（第一版的行为）之后，python 和 fzf 两个进程都消失，`run/` 下的临时文件删干净。
  第一版只删掉了一半文件：herdr 关窗格时紧跟着来了第二个信号，把清理打断了。已改成只认第一次信号。
- Claude 那一格关掉（关测试 tab）：两个侧栏的 python 和 fzf（77977/77979、77829/77831）都退出，临时文件删干净。
- 里面的 Claude 退出（专用测试会话里发 `/exit`）：19 秒后轨迹进程（44910）退出，窗格收回，
  Claude 那一格回到整宽 150 列，临时文件删干净。15 秒是故意留的宽限：切账号时 Claude 会退出再重启。

### 7. tmux 版行为不变 — 达成

- 本分支相对分叉点 `dcba8c0`，`sidebar/ hooks/ lib/ bin/ tmux/` 零改动（`git diff --stat dcba8c0 HEAD -- ...` 为空），
  只新增了 `herdr/` 和本文档。插件只 import tmux 版的代码，不改它。
- 实际跑了一遍：在后台 tmux 会话 `trailtest` 里续接同一个会话，用本 worktree 的 `sidebar/trail-sidebar.sh` 开侧栏。
  侧栏正常出现（41 轮，表头如上）。点 #33 后，主界面跳到 #33 的最终回复（「查完了。…」停在第 4 行）。
  测完 tmux 会话已关。
- 注意：`main` 已经往前走了（`b893df7`，另一个会话改了 `trail_daemon.py`，常驻进程 CPU 从 8.6% 降到 1.35%）。
  合并时插件 import 的是 main 上的新 `trail_daemon.py`，要再跑一遍第 2、4 条确认接口没变。

## 第一阶段里追加的需求（主会话转达，用户确认过）

| 需求 | 做法 | 证据 |
|---|---|---|
| 默认打开：tab 里有 Claude 才开，停在右边，不抢左边 | 事件钩子 `tab.focused`、`pane.agent_detected` → `trail.py auto`：tab 里正好一个 Claude、宽 ≥120 列、还没有轨迹时开。同时来几个事件时用文件锁串行，开出来先写登记，防止重复开。配置 `auto_open = false` 关掉 | 新开测试 tab 起 Claude，`pane_agent_detected` 之后自动出现轨迹（51 列，右边），左边 herdr-sidebar 32 列不动，只开了一个。用户切到 tab 1 时也自动给 `w1:p1` 开了。中间踩过一个坑：第二次起 Claude 没开，原因是判断时要求窗格已经有会话 id，而 Claude 刚被认出来时 id 还没报上来。改成只看 `agent == "claude"`，重放同一个事件就开了 |
| 全程鼠标可操作：关掉以后要能点回来 | 没有「关闭」。宽版和窄版两种画法，按轨迹窗格的实际宽度自动切换（tty ≥ 30 列画宽版）。在 herdr 里拖分隔线时实时跟着变：窗格进程收 SIGWINCH，经 wakeup fd 叫醒主循环，不轮询。左上角 » / « 一键拉到窄版（最窄）/ 拉回宽版；toggle 动作（`prefix+t`）同样。Claude 退出或那一格关掉才真关 | 见下面「宽窄自适应实测」 |
| 窄版是能长期用的版本 | 自上而下：« / 状态图标 / 模型缩写（`Opus5.5/hi`）/ 账号缩写（`l2`）/ ctx、5h、7d 百分比（颜色规则同宽版柱子）/ 分隔线 / 每轮一行（`#41 那我们`，点了照样跳转）/ 底部固定的按钮（fzf 的 footer）。放不下的按 账号 → 模型 → 7d 丢 | 15 列时读屏：`«`、`✓`、`✦ Opus5.5/hi`、`@ l2`、`◧ 5%`、`◷ 33%`、`▦ 7%`、分隔线、`#1 只回复：O`、`#2 这是第二`，底部 `± ▤`（用 unicode 档转写） |
| 按钮可配置，默认用户的 lazygit、yazi | `config.toml` 的 `[[buttons]]`（label、command、可选 icon），宽版在表头第 1 行（图标加字），窄版在底部（只要图标）。点了在 Claude 的目录里开本插件的 popup，经 `sh -c` 跑 command；找不到命令在表头提示「找不到 X」 | 点窄版底部 Git：收到 `foot 1 2`，lazygit（pid 76923）在浮窗里起来，随即用 `popup.close` 关掉。浮窗的目录：用一条立即退出的命令验证，`pwd` 写出的是 Claude 那一格的目录。配置坏值、坏文件都回退到默认值（离线测过） |
| 图标优先，别人没装 Nerd Font 时有兜底 | `icons = "nerd" \| "unicode" \| "text"`，默认 nerd。一张表 `ICONS`，每个图标三档 | 三档各离线渲染了一遍宽版和窄版，见 README 的示意图 |
| 名字：核心是「Claude 会话轨迹」，别叫 sidebar | 插件 id `claude-trail`，manifest 和文案都写 trail，发布前再定名 | `herdr/herdr-plugin.toml` |
| 不依赖 herdr-sidebar、herdr-file-viewer | 试做过「左栏」按钮（调 herdr-sidebar 的动作）和调 herdr-file-viewer 的「文件」按钮，用户更正后删掉，改成本插件自己的浮窗 | `trail.py` 里没有对别的插件的调用 |
| 右键固定回 herdr 菜单 | 每次起 fzf 前 `pane.input.set right_click=herdr` | 返回 `{'type': 'ok'}`。`pane get` 不返回这个字段，没法读回来核对 |
| 配置只开放确有需要的几项 | `auto_open`、`default_mode`（打开时宽版还是窄版宽度）、`width`、`icons`、`[[buttons]]` | README「配置」一节 |

### 宽窄自适应实测

模拟拖分隔线：用 `pane.resize` 把轨迹依次调到各个宽度，每档等 1 秒后读屏。表里的「矩形」是 `pane.layout` 报的宽度，
tty 比它窄 2 列（herdr 画的边框，实测矩形 51×45 时 `stty size` 是 43 49）。

| 矩形宽 | 画的是 | 最长一行 | Claude / 左栏 |
|---|---|---|---|
| 60 | 宽版 | 58 | 90 / 32 |
| 32（tty 30） | 宽版，按钮行末尾被 fzf 截成「⟳…」 | 30 | 118 / 32 |
| 31（tty 29） | 窄版 | 29 | 119 / 32 |
| 25、20、16 | 窄版 | 23、18、14 | 125–134 / 32 |
| 1（夹到 15） | 窄版，列表截成「#2 这是第二」 | 13 | 135 / 32 |
| 40 | 宽版 | 38 | 110 / 32 |
| 135（夹到 115，Claude 只剩 35） | 宽版 | 113 | 35 / 32 |

每一档都没有超出窗格宽度，左边 herdr-sidebar 始终 32 列。点 » 后 `135 | 15`，点 «（同一个位置，第 1 行第 4 列）
后回到 `99 | 51`；toggle 两次依次是 15、51。

窄版的宽度：要求是 2–3 列，**做不到**。herdr 把分割比例夹在 0.1–0.9（herdr v0.9.5 `src/layout.rs:295`、`:630`
都是 `ratio.clamp(0.1, 0.9)`）。实测要 3 列、2 列、1 列，都停在 15 列（150 列分割的 10%）。
herdr-sidebar 的 PR #106（还没合）也写了同样的结论：「herdr clamps split ratios to 0.1-0.9」。

右键菜单查过了：窗格右键菜单是写死的（herdr v0.9.5 `src/client/shell/context_menu.rs:59-78`：Rename pane、
Split right、Split down、Zoom、Send right-clicks to pane、Close pane），全局菜单也是写死的（`global_menu.rs:22-51`）。
manifest 里的 `contexts` 在源码里只在 schema 和 manifest 解析里出现，界面上没用到。所以鼠标入口只能放在本插件自己的窗格里。

## herdr 的限制（第二阶段要知道的）

| 限制 | 影响 | 绕法 | 出处 |
|---|---|---|---|
| 插件不能画自己的界面 | 列表只能是终端程序 | fzf + 一个 python 进程 | `plugins.mdx`：native non-terminal plugin UI are not part of plugin v1 |
| `send-keys` 不认 `home` `end` `pageup` `pagedown` `ctrl+home`（都报 `invalid_key`） | 跳转要的键发不出去 | 用 `pane.send_text` 直接发终端转义序列：PageUp `\e[5~`、PageDown `\e[6~`、Ctrl+Home `\e[1;5H`、Ctrl+End `\e[1;5F`、滚轮 `\e[<65;x;yM`。测过不会进输入框 | 实测 `herdr pane send-keys` |
| `plugin.pane.open` 没有比例参数 | 开出来是对半分 | 开完再 `pane.resize` | 实测；herdr-sidebar `ensure.rs` 里有同样的注释 |
| 分割比例夹在 0.1–0.9 | 窄版最窄约 15 列 | 接受，窄版照样能用 | `src/layout.rs:295` |
| 右键菜单、全局菜单都放不进插件动作 | 鼠标入口只能自己做 | 窄版常驻，没有「关闭」 | `context_menu.rs:59-78` |
| `pane.resize` 挪的是窗格在那个方向上的那条边 | 对 Claude 做 `left`、对轨迹做 `right` 会去挤外侧的窗格 | 缩窄轨迹对 Claude 做 `right`，加宽对轨迹做 `left` | 实测把 herdr-sidebar 从 32 挤成 18，已恢复 |
| 窗格 tty 比 `pane.layout` 的矩形窄 2 列、矮 2 行（边框） | 按矩形算阈值会差一格 | 阈值按 tty 算，设宽度时加 `FRAME = 2` | `stty -f /dev/ttys051 size` → `43 49`，矩形 51×45 |
| popup 不接受 `target_pane_id` | 第一版按钮点了没反应（`invalid_params`：overlay and popup plugin panes target the active pane） | 不传，目录用环境变量 `CLAUDE_TRAIL_CWD` 传 | 实测 |
| `pane.agent_detected` 来的时候会话 id 可能还没报上来 | 按会话 id 判断「有没有 Claude」会漏开 | 只看 `agent == "claude"` | 实测 |
| 切 tab 时 `tab.focused` 和 `pane.focused` 成对来 | 两个都挂钩子就白跑一遍 | 只挂 `tab.focused` 和 `pane.agent_detected` | 插件日志里成对出现 |
| `pane.updated` 不能按窗格过滤，别的插件会一直触发（herdr-sidebar 每几秒更新一次自己的 token） | 订了就会被一直叫醒 | 不订，只订按窗格过滤的 `pane.agent_status_changed` 加上很少发生的 `pane.closed` / `pane.exited` | 订阅约 10 分钟收到 806 条 `pane_updated`，802 条来自 herdr-sidebar 的窗格（label = Sidebar），2 条来自 Claude 窗格，2 条日志截断无法解析 |
| 事件名写法不统一：状态是 `pane.agent_status_changed`（带点），关闭是 `pane_exited`（下划线） | 第一版只认下划线，状态变化一直没触发刷新，只靠 60 秒定时兜底 | 两种写法统一换成下划线再比 | 实测订阅输出 |
| `pane.agent_status_changed` 必须带 `pane_id` | 不能一条订阅管所有 Claude | 每个轨迹订自己那个 Claude | 不带时报 `missing field pane_id` |
| `HERDR_PLUGIN_EVENT_JSON` 的结构文档没写 | — | 实测是 `{"event": "tab_focused", "data": {...}}`，代码两种结构都兼容；钩子把收到的事件记在 `events.log` | 实测 |
| Claude 在主线上自己滚，herdr 没有宿主回滚（`max_offset_from_bottom` = 0） | 跳转只能给 Claude 发键 | 和 tmux 版一样 | 交接说明已记 |
| 合成的表头点击：按下、松开放在同一次写入里，fzf 不触发 `click-header`（列表点击照常） | 只影响自动化测试，真实鼠标没问题（用户真实点表头按钮成功过） | 测试时按下、松开分两次发，中间隔 0.15 秒 | 加了调试记录（状态目录里 `touch debug`）后对比 |

## 测试中的一件事：续接了正在被引用的会话

为了和 tmux 版对比，我在 herdr（`w1:pC`，pid 26662）和 tmux（`trailtest`，pid 66773）里都
`claude --resume aa325ab9…` 续接了同一个旧会话，l2 测试 tab 里续接了 `837ce5fd`。主会话提醒了这个风险，
说原主人 work-4a 在用它。核对结果：当时 `~/.claude/sessions` 里持有 `aa325ab9` 的只有这两个副本，
work-4a（`help:@10.%12`）用的是另一个会话 `a17d9d9d`。

这几个副本我只发过滚屏用的转义序列，还误发过一次 up / f1 导航键（没有回车，输入框确认是空的），没有发过任何输入。
但 Claude Code 自己在续接和退出时往对话记录里追加了记账记录（`cost-state`、`permission-mode`、`last-prompt`），
`aa325ab9` 从 3560904 字节涨到 3562545 字节，没有对话内容。三个副本都已退出，测试 tab 和 tmux 会话都已关掉。
之后要发输入的测试都用 scratchpad 里专门新开的会话（`2772bc51`，后来又起了一个 `da151c4d`），测完已退出、tab 已关。

## 第二阶段建议

1. **抽共用核心，tmux / herdr 只做薄的适配层**（主会话转达）。现在 `herdr/trail.py` 是 `claude_trail.py` 的复制改写：
   跳转算法照搬了一份，窄版、配置、图标又是新写的，两边会越走越远。第二阶段（转 Rust 时）把「读对话记录、
   算轮次、画额度柱子、跳转算法」抽成共用核心，读屏 / 发键做成接口，tmux 和 herdr 各写一个薄的后端。
   读对话记录那层也做成可替换的，给以后支持 Codex / OpenCode 留口子。眼下插件直接 import
   `sidebar/claude_trail.py`、`trail_daemon.py`、`hooks/ctx_to_tmux.py`；从 GitHub 装是整个仓库检出
   （已装的 herdr-sidebar 的 `checkout/` 下是完整仓库，`plugin_root` 指向子目录），所以能用，但 `main` 上那边一改接口插件就会坏。
   抽核心要和在 `main` 上改 tmux 版的那个会话商量好再动。
2. **tab 名跟着 Claude 的会话名**（主会话转达的用户需求）：轨迹在某个 tab 里打开时，如果这个 tab 没被手动命名过，
   就用 Claude 的会话名（终端标题，`herdr agent list` 里的 `terminal_title_stripped`）给它 `tab rename`，
   会话名变了也跟着改。要先弄清楚怎么判断「用户手动命名过」（`tab list` 里的 label 是不是默认的数字）。
3. **主界面停在哪一轮**（tmux 版列表最左边的亮边，窄版也要）：现在两版都只有 fzf 的选中高亮（点过的那一轮）。
   要读屏才知道主界面停在哪，tmux 版每秒截屏是 CPU 大头。herdr 有 `pane.scroll_changed` 事件，但 Claude 在自己的屏幕里滚，
   宿主的 scroll 不变，大概率收不到。可以只在轨迹是当前 tab 时，每 2 秒读一次屏（一次 0.3 毫秒）。
4. **宽窄切换时保住选中的那一轮**：跨过阈值会重起 fzf，选中回到最后一轮。可以把当前选中的 id 带给新的 fzf（`pos` 动作）。
5. **Claude 那一格本身被上下切开时的整列高**：现在切的是 Claude 那一格，它要是只占半列，轨迹也只有半列。
   herdr-sidebar 的 `full_height_repair`（把窗格经临时 tab 来回挪）是现成的做法，比较绕。
6. **切账号按钮**（tmux 版的 ⇄）：交接里列的第三阶段候选，基于多个 `CLAUDE_CONFIG_DIR`。
7. **tmux 版顺手修**：「已经在最底就不发 Ctrl+End」那处优化，tmux 版最新一轮的跳转能从 2.3 秒降到 0 秒左右。
8. **发布前**：英文 README 和演示 GIF、定名、`herdr-plugin` 标签、`min_herdr_version` 在更老的版本上验证
   （现在写的是实测过的 0.9.5）、窄版补测 10 分钟 CPU、python 3.11 以下没有 `tomllib` 要么声明要么兜底。
9. **窄版宽度**：herdr 将来要是放开 0.1 的下限，再看要不要更窄。

## 留下了什么

- 插件以本地链接的方式装着（`herdr plugin link`，指向本 worktree 的 `herdr/`），没有发布，没有推送。
  用户的 tab 1、tab 2 里各开着一个轨迹（最终代码）。
- `~/.config/herdr/config.toml` 里的 `prefix+t → claude-trail.toggle` 是主会话加的，我没有改这个文件。
- 状态目录 `~/.local/state/herdr/plugins/claude-trail/`：`jump.log`（跳转记录）、`events.log`（自动打开收到的事件，
  超过 200KB 自动清空）、`pane.err`。合计 116KB。调试开关 `debug` 已删。
- 两个专用测试会话 `2772bc51`、`da151c4d`：对话记录在 `~/.claude/projects/-private-tmp-claude-502-…-scratchpad-trail-test/`，
  各约 430KB，Claude 已退出。留着没删：它们在 Claude 自己的会话列表里，删不删由你定。
