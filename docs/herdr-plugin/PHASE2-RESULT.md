# 第二阶段结果：claude-trail 准备发布

写于 2026-10-25，herdr 0.9.5、fzf 0.74.4、Python 3.12.4（运行用 Homebrew 的 3.14 也测过），分支 `herdr-plugin`。
交接说明见 [PHASE2-HANDOFF.md](PHASE2-HANDOFF.md)，第一阶段见 [PHASE1-RESULT.md](PHASE1-RESULT.md)，
代码在 [`herdr/`](../../herdr/)。

## 结论

**代码这一侧已经准备好发布，剩下的都是要你拍板的事。**

- `herdr/` 已经自成一体，能直接搬成新仓库：不再 import 本仓库的 `sidebar/` `hooks/` `lib/` `bin/`，
  自带 README（英文 + 中文）、LICENSE、演示 GIF 和可复现的录制装置。
- 卡在「不许做」上的有三件：从 GitHub 真装一次（要先推送）、建新仓库、打 `herdr-plugin` 标签。
  它们列在最后的「发布前检查清单」里。
- 范围变过两次，都是你定的：
  1. 单独成仓库、tmux 版冻结不迁，所以第 1 条验收改了；
  2. UI 向 herdr 看齐，加了不少原交接里没有的东西。
  各条都附了提交号。

## 验收标准逐条

### 1. 共用核心之后两边轮次一致、跳转照常、tmux 版不退步 — 按改过的范围达成

你在这一阶段定了「claude-trail 单独成仓库，tmux 版冻结、不迁到共用核心」
（[决定记录](../decisions/2026-10-25-herdr.md) 最后一节，91f5bef）。
所以这一条变成：`herdr/` 自带共用核心，切出来的轮次和 tmux 版一致，tmux 版一行不动。

- **共用核心**（da4cf73）：`herdr/core/`
  - `transcript.py`：读对话记录、切轮次，按 agent 挑读取器，现在只有 Claude，给 Codex / OpenCode 留了口子
  - `claude.py`：配置目录、账号、模型和上下文、额度
  - `render.py`：柱子、宽版和窄版的列表
  - `jump.py`：跳转算法，读屏 / 发键只依赖 `screen()` `send()` `height`
  - `i18n.py`

  `trail.py` 只剩 herdr 适配层。
- **搬代码没有改变任何输出**：
  - 4 个会话（2 个带 `/compact`）× 两个账号 × 宽版 49/80 列、窄版 13/28 列 × 折叠/展开，共 32 份
  - 旧代码（e5cbd2c 原样解出来）和新代码紧挨着各渲染一次，**32/32 逐字一致**
  - 之后改颜色时又比了一次：去掉颜色码 32/32 一致
- **自成一体**：
  - 把 `herdr/` 单独复制到空目录里，能 import、能读会话（41 轮），所有模块都来自它自己的 `core/`
  - grep 确认不再引用仓库其他目录
- **轮次一致**：插件 import 的切轮规则就是从 tmux 版搬来的那份。合并 main 之后又核对了一次：用户 tab 1 的会话插件 45 轮，`parse_session` 45 轮。
- **跳转照常**（实机，专用测试会话）：
  - 跳 #1：0.91 秒找到
  - 跳 #3：0.001 秒
  - 录 GIF 时，在隔离 herdr 里跳 #3：0.115 秒
- **tmux 版没动**：本分支对 `sidebar/` `hooks/` `lib/` `bin/` `tmux/` 没有任何提交，`main` 那边照常演进。

### 2. 按钮配置：默认 lazygit / yazi，改了能生效，命令不存在有提示 — 达成

- 不写配置时是 Git（lazygit）和文件（yazi）两个按钮。
- 实机：点窄版底部的 Git，收到 `foot 1 2`，lazygit 在浮窗里起来，目录是 Claude 那一格的
  （用一条立即退出的命令验证：浮窗里 `pwd` 写出的就是 Claude 的目录）。
- 改成别的能生效：
  - 离线配 `tig`、`btop`、一个缺 `command` 的坏按钮：前两个生效，坏的被跳过
  - 实机临时配了 `action = "claude-trail.toggle"`，点它，轨迹从 38 列切到 15 列（7efe164）
- 命令不存在：配 `gitui-not-installed`，表头提示「找不到 gitui-not-installed」（英文界面「… not found」）。
- 按你的意见，自定义按钮和 Claude 相关的按钮分开：宽版、窄版都放在最底下一行，用分隔线隔开。

### 3. 照着 README 从零安装，一次成功 — 部分达成（真正从 GitHub 装要等推送）

`herdr plugin install <owner>/<repo>` 只认 GitHub 上的仓库。本分支没推送，新仓库也没建，所以**没有真正从 GitHub 装过**。

做了能做的部分：

- **干净环境**：在私有 tmux 里用全新的假 `$HOME` 起了一个完全隔离的 herdr，`herdr plugin link` 进去，从零跑了一遍。
  **这一遍抓到一个真 bug**：新装的用户状态目录里没有 `run/`，`sidebars()` 一列目录就崩，
  **自动打开对新用户从来不生效**。我这台机器早就建过这个目录，所以一直没暴露。已修（7ef94e9）。
- **安装命令**：herdr 0.9.5 从 GitHub 装子目录插件时，是把整个仓库检出、`plugin_root` 指向子目录
  （已装的 herdr-sidebar 就是这样），所以单独成仓库后 `herdr plugin install owner/repo` 直接装仓库根。
- **缺依赖不再静默失败**（c7b28f7）：
  - 要求 Python 3.11+、fzf 0.71+。0.71 是 `--id-nth` 加入的版本，查 fzf 的 CHANGELOG 得到
  - 模拟三种情况都报得清楚：fzf 太旧（假装 0.60）、系统自带 Python 3.9.6、PATH 里没有 fzf
  - 窗格里直接列出缺什么、怎么装，并停住等你看
- **摩擦最大的一处**：macOS 自带的 Python 是 3.9.6，陌生人在 Mac 上大多要先 `brew install python`。README 写在安装第一步。

推送以后要补的真机步骤列在检查清单里。

### 4. GIF 15 秒以内 — 达成

[`herdr/docs/demo.gif`](../../herdr/docs/demo.gif)：12.6 秒、456KB。

- **分镜**：轨迹自动打开 → 点 #3（Claude 滚到第 3 轮，底部出现 Jump to bottom）→ » 拉到窄版 → « 拉回宽版 → 点 Git 开 lazygit。
- 交接里写的「收起成细条、再展开」后来改成了宽版 / 窄版。
- **录制装置** `herdr/demo/`（7ef94e9）：
  - 私有 tmux 加假 `$HOME`，起完全隔离的 herdr（`env -i`，socket 在假 `$HOME` 里）
  - 窗格里跑 `fake_claude.py`：照 Claude 全屏界面画一份编出来的对话
  - 插件是真代码，点击是往 herdr 客户端发真实的鼠标序列
  - 画面经 ANSI → HTML → headless Chrome（策略 `GenAILocalFoundationalModelSettings` = 1，固定配置目录）→ ffmpeg
- **诚实边界**写在 `herdr/demo/README.md`：Claude 是假的、数据是编的、图标用 unicode 档。
- **录完的清理**：假 `$HOME` 已删，私有 tmux 没有 server。有一个早先手动试验时留下的演示用 herdr server（`HOME=/private/tmp/herdrdemo`，pid 27199）是我结束的；动手前核对过它的 `HOME` 和 socket，不是你的。

### 5. 发布前检查清单 — 见最后一节

## 这一阶段另外做的（你的要求，经主会话转达或直接说的）

| 要求 | 做法 | 提交 | 证据 |
|---|---|---|---|
| UI 向 herdr 看齐，颜色跟着主题 | 从 herdr v0.9.5 `src/app/state.rs` 提取 18 套调色板（Apache-2.0，注明出处）；读 herdr 的 `[theme] name` 和 `[theme.custom]`；状态点和 Agents 栏同色（工作中 yellow / 等你 red / 完成 green）；fzf 的选中行、指针、边框也用主题色 | 0282a0d | 读屏 `--format ansi`：前景是 catppuccin 的 surface1 / overlay0 / overlay1 / blue，选中行背景 `#313244`，窄版「完成」是 `#a6e3a1` |
| tab 用 Claude 的会话名命名 | label 还是默认编号、或还是我们上次起的名字才改；你手动改过就不碰；超过 24 列截断 | 3ecdcbc | 测试 tab「11」→「第一句测试」；手动改成「我手动起的名」后 Claude 又回复一轮没被覆盖；用户 tab 1 自动变成「Herdr 与 tmux Claude 会…」，tab 2「herdr插件」没动 |
| 按钮图标和文字只显示一个 | 有图标只画图标，`icons = "text"` 或没配图标只写字 | e5cbd2c | 三档离线渲染 |
| 自定义按钮挪到底部；支持 `action` 调别的插件 | 见第 2 条 | 7efe164 | 见第 2 条 |
| 给 Claude 留够宽度 | Claude 自带的 `/diff` 面板要终端 ≥110 列（code.claude.com/docs/en/interactive-mode.md「Diff panel」）；宽版封顶到「两格合计 − 110 − 边框」，放不下就开窄版 | a1a57bd | 有 herdr-sidebar 的 tab：Claude 112（tty 110）+ 轨迹 38；没有 herdr-sidebar 的 tab：Claude 120 + 轨迹 62 |
| 英文界面 | 词表从 tmux 版的 383 行精简到插件用到的 44 条，补齐新文字 | 9c6104e | `CLAUDE_TRAIL_LANG=en` 渲染：「14m ago」「54 turns」「yazi not found」 |
| 按钮开的工具不用 popup | herdr 的 popup 只能居中、是整个会话的模态窗口：盖住 Claude、点外面不关、鼠标退不出来（你报的）。改成开在 Claude 右边，占 Claude 那一块的 60%；焦点离开 0.4 秒后复查就关；lazygit 叠加 `quitOnTopLevelReturn`，和 yazi 一样 Esc / q 都能退；退出后焦点还给 Claude | 1074e5e | 隔离 herdr 里实测：点 Git → Claude 46 + lazygit 69 + 轨迹 59；Esc 退出后焦点回 Claude；点 Claude（外面）关掉；yazi 按 q 退；再点同一按钮关；开着 Git 点文件换成 yazi。代价：herdr 没有不挤别人的浮动窗格，Claude 会暂时变窄、重新换行 |
| 只显示 herdr 没有的 | 窄版表头删掉 Claude 的状态图标（herdr 左侧 Agents 栏已有）；这条原则记进决定记录 | 1074e5e、1e822fd | 重录的 GIF 窄版那帧第 2 行直接是模型 |
| 额度自己查，不再依赖 tmux 版的脚本（后来改成选配，默认只读 Claude Code 的缓存，见检查清单） | 从 `bin/usage-refresh.py` 搬到 `core/claude.py`：钥匙串读令牌只在内存里用、不刷新不写凭据、30 分钟节流、手动 20 秒防连点、同账号文件锁；读数写在插件自己的状态目录 | da4cf73 | 两个账号各强制查一次都拿到读数（1.48 秒 / 0.73 秒），20 秒内再查不发请求 |

另外回答过你的一个问题：Claude Code 自带的侧边栏是 `/diff` 面板（内置 mod `cc-plugin-diff`，v2.1.287+）。
它在终端 ≥144 列、Claude 开始改文件时自动弹出，你关掉后在再次运行 `/diff` 之前一直不弹。
你最近见不到它，多半是轨迹把 Claude 挤到了 144 列以下。上面「留够 110 列」能保证手动 `/diff` 开得出来，
但 tab 1、tab 2 里 Claude 是 120 列，仍然到不了自动弹出要的 144 列。

## 候选名字

以后要支持多种 agent，名字最好不绑 Claude。GitHub 仓库搜索（2026-10-25）：

| 候选 | 完全同名 | 名字里含它的 | 评价 |
|---|---|---|---|
| **`herdr-turns`（推荐）** | 0 | 0 | 直接说「按轮次」，不绑 agent，短 |
| `herdr-session-trail` | 0 | 0 | 保留 trail 这个词，长一点 |
| `herdr-waypoints` | 0 | 0 | 比喻「跳转点」，要解释一句 |
| `herdr-claude-trail` | 0 | 0 | 最直白，但绑死了 Claude |

查过、不建议的：

- `claude-trail`：42 个同名，你自己还有个私有旧仓库 `threedaycat/claude-trail`
- `herdr-trail`：已被别人占用（`catoncat/herdr-trail`）
- `agenttrail`：最热门的一个 958★；`agent-trail`：17 个同名，最高 40★
- `herdr-recap`：已有同名 herdr 插件（9★）
- `turnmark`、`turnline`、`turnmap`：各有 2 个同名

插件 id 现在是 `claude-trail`，定名后要同步改：manifest、README、状态目录名、动作名 `claude-trail.toggle`。

## Rust 要不要改写

**你已经定了：v0.1 先用 Python 发，v0.2 再看。** 下面是当时给的估算，留作 v0.2 的参考。

| | Python 版（现在） | Rust 改写 |
|---|---|---|
| 用户要装的 | Python 3.11+ 和 fzf 0.71+；macOS 自带的 Python 3.9 不够，要 `brew install python fzf` | 什么都不用，下载一个二进制（要有发布流程） |
| 内存（实测，每个轨迹） | python 21–47MB + fzf 8–10MB | 估计 5MB 左右 |
| 空闲 CPU | 约 0.1% 单核 / 轨迹（第一阶段两轮 10 分钟实测） | 差不多，瓶颈本来就不在这 |
| 代码量 | 1,770 行（去掉空行和注释），界面和鼠标交给 fzf | 估计 3,000–4,000 行：fzf 管的列表、滚动、点击、表头和 footer 都要用 ratatui 自己写 |
| 工作量 | — | 改写 2–4 天，加发布流程约 1 天（GitHub Actions 编 macOS arm64 / x86_64 和 Linux 的二进制、校验和，再加一个像 herdr-file-viewer 那样「先下载、失败再编译」的 build 脚本） |
| 本机代价 | — | 要装 Rust 工具链，超过 1GB（按规矩要先问你） |
| 回归测试 | 已有：32 份逐字比对的基准、假 Claude 加隔离 herdr 的录制装置 | 这两样能原样用来验 Rust 版 |

理由：

- 改写只为了安装方便，不为性能。
- 看 herdr 用户的样子，`brew install python fzf` 这一行的摩擦多半能接受。
- 先发出去拿到反馈，比先花 3–5 天改写更值。
- 真要改写时，现在的基准比对和录制装置就是现成的回归测试。

## 发布前检查清单

你已经定了的（2026-10-25 晚，经主会话转达）：

| 事项 | 决定 | 落实 |
|---|---|---|
| 额度查询 | 默认只读 Claude Code 缓存在 `.claude.json` 里的读数，不碰钥匙串；`live_usage = true` 才实时查询 | ec3236d：缓存可能是几天前的，表头写「N 天前」，过了重置时间的窗口画「?」；README 改写 |
| 默认图标 | 加 `icons = "auto"` 并设为默认：装了 Nerd Font 用 nerd，没装用 unicode | ec3236d：macOS 看 `~/Library/Fonts`、`/Library/Fonts`，Linux 问 `fc-list`；README 写明「装了不代表终端在用它」 |
| Rust | v0.1 先用 Python 发，v0.2 再看 | 不动 |
| 新仓库放哪 | `siyuanseever` 下 | README 的安装命令写成 `siyuanseever/<repo>` |
| 名字 | 还没定：要和「轨迹」相关，不必带 agent | 先不改名，插件 id 仍是 `claude-trail` |

还要你确认的：

1. **定名**。按「和轨迹相关、不必带 agent」，前面的候选里 `herdr-session-trail`、`herdr-waypoints` 符合（GitHub 上都是 0 同名）。
   `herdr-turns` 偏「轮次」不太像「轨迹」，`herdr-trail` 已被别人占了。
   定了以后我改 manifest、README、状态目录、动作名 `claude-trail.toggle`，重跑基准和录制。
2. **建新仓库**：`siyuanseever/<名字>`，public。建议用 `git subtree split --prefix=herdr` 保留历史。
3. **推送和真装一次**：推上去后在干净环境里 `herdr plugin install siyuanseever/<名字>`，记录命令和用时；README 里的 `<repo>` 换成真名。
4. **打 `herdr-plugin` 标签**进插件市场。
5. **合进 main**：本分支合进 `main` 后用 `git worktree remove` 收掉 worktree；用 subtree split 的话，`herdr/` 在这个仓库里留不留也要你定。
6. **你自己这台的额度**：默认改成读缓存以后，你这里的缓存停在 10 月 9 日（l2 是 10 月 15 日），5h / 7d 现在显示「?」。
   要像以前一样看实时数字，就在插件配置（`herdr plugin config-dir claude-trail` 那个目录的 `config.toml`）里写 `live_usage = true`。
   我没替你改。

## 遗留问题和风险

- **从 GitHub 真装一次还没测**（第 3 条）。干净环境里已经抓到过一个只有新用户才会遇到的 bug，正式装可能还有。
- **主界面停在哪一轮**（tmux 版列表里的亮边）还没做，两版都只有「点过的那一轮」高亮。
- **宽窄切换时选中位置会丢**：跨过阈值会重起 fzf，选中回到最后一轮。
- **Claude 的 `/diff` 面板仍然不会自动弹**：要 144 列，常见的 182 列 tab 里留不出来；只能保证手动 `/diff` 开得出来。
- **依赖了 herdr 没写进文档的行为**，herdr 升级时要复查：
  - 事件名一半带点、一半带下划线
  - `HERDR_PLUGIN_EVENT_JSON` 的结构
  - popup 不收 `target_pane_id`
  - 内置 claude 只认 `herdr:claude` 来源报的会话
  - tty 比 `pane.layout` 的矩形窄 2 列
- **Claude 自带的 `{` / `}`**（transcript 查看器里跳到上一条 / 下一条提问）不能直接替换现在的跳转：
  它在 Ctrl+O 打开的另一个查看器里，跳过去要按 q 才回得到主界面。可以作为备选功能「在 transcript 里定位」。
- **第一阶段的待办**「窄版补测 10 分钟 CPU」已补：14:46:16–14:56:16，窄版轨迹对着空闲的测试 Claude，python 0:00.11 → 0:00.15、fzf 0:00.01 → 0:00.56，合计 0.59 秒 / 10 分钟（约 0.1% 单核），和宽版一样。

## 留下了什么

- 插件以本地链接的方式装着，指向本 worktree 的 `herdr/`。tab 1、tab 2 里各开着一个轨迹，都是最终代码（ec3236d）。
- tab 1 被自动改名成了「Herdr 与 tmux Claude 会…」（tab 名跟会话走的功能）。不想要的话，手动改回去它就不再动。
- 状态目录 `~/.local/state/herdr/plugins/claude-trail/`：跳转日志、事件日志、`pane.err`、两个账号的额度读数、`tab-names.json`。
- 测试会话：`2772bc51`、`da151c4d`、`3e1cbb72`，都在 `~/.claude/projects/-private-tmp-claude-502-…-scratchpad-trail-test/`，各约 400KB。三个测试 Claude 都已退出，测试 tab 已关；对话记录留着没删，删不删你定。
- 没推送，没建仓库，没打标签，没改任何 `settings.json`，没碰别的插件。
