# 交接：把轨迹侧栏做成 herdr 插件（第一阶段：可行性原型）

写于 2026-10-25。背景和取舍见 [../decisions/2026-10-25-herdr.md](../decisions/2026-10-25-herdr.md)。

## 一句话目标

在 [herdr](https://herdr.dev)（0.9.5）里做出一个**停靠在 Claude 窗格右边、占满整个高度**的轨迹侧栏插件。
它要列出这个 Claude 会话的每一轮，点一轮，Claude 窗格就滚到那一轮。
这一阶段只回答一个问题：**这件事在 herdr 上做不做得成、体验能不能追上 tmux 版**。做成了再进入第二阶段（打磨、发布）。

## 为什么做这个

- 用户的方向：把 tmux 版里值得的部分搬成 herdr 插件，开源发布、拿 star。
- herdr 插件市场里没有同类：唯一接近的 `speardragon/herdr-agents-history`（3 star）是所有 agent 混在一起的工具调用流，
  不按轮次分组，也不能跳转。额度类插件有十几个，但都只往 herdr 左侧栏塞一行字。用户试过 `herdr-agent-usage`，嫌粗糙，已卸载。
  所以侧栏表头那几条 ctx / 5h / 7d 柱子也是卖点。

## 已经查清的事实（带出处）

| 问题 | 结论 | 出处 |
|---|---|---|
| 插件能不能画自己的界面 | 不能。只能开终端窗格、浮窗，或往侧栏塞文字。所以侧栏要做成一个终端程序（像现在的 fzf） | herdr 文档 `plugins.mdx`："native non-terminal plugin UI are not part of plugin v1" |
| 插件窗格能不能常驻 | 能。`[[panes]]` 设 `placement = "split"`，打开后就是普通窗格 | `plugins.mdx` 的 Panes 一节 |
| 能不能停在右边、占满高度 | 能，但要自己处理：开分屏 → `pane.swap` → `pane.resize` → 修成整列高度 | 参考 `alexarthurs/herdr-sidebar` 的 `src/ensure.rs:748-900` |
| 怎么知道 Claude 窗格对应哪个会话 | `herdr pane get <id>` 返回 `agent_session.value`，就是 Claude 的 session id。**不需要 tmux 版的钩子和状态文件** | 实测 `herdr pane list` |
| 跳转要的两个动作 | 读屏幕：`herdr pane read <id> --source visible`（对应 tmux `capture-pane`）；发按键：`herdr pane send-keys <id> <key>...`（对应 tmux `send-keys`） | `herdr pane --help`；tmux 版在 `sidebar/claude_trail.py:832, 852` |
| Claude 在 herdr 里怎么滚动 | 在自己的屏幕里滚，herdr 没有宿主回滚（`scroll.max_offset_from_bottom` = 0），和 tmux 里一样要给 Claude 发键 | 实测 `herdr pane get` |
| 事件推送 | 插件可以挂 `[[events]]`：`pane.focused`、`pane.agent_status_changed` 等，不用轮询 | `plugins.mdx`、`socket-api.mdx:826` |
| 本地开发怎么装 | `herdr plugin link <目录>` 链接本地插件，不用发布 | `herdr plugin --help` |

## 可以直接复用的代码

`sidebar/claude_trail.py` 里这些和 tmux 无关，可以抽成共用的核心：

- 解析对话记录：`parse_session`、`get_session`（第 74、109 行）
- 额度和上下文：`read_usage`、`_quota_line`、`_ctx_line`、`quota_colour`、`status_lines`（第 179–530 行）
- 当前账号：`current_account`（第 350 行）

和 tmux 绑死、要换成 herdr 的：

- `session_path` / `_pane_entry`（第 531–548 行）：读的是 tmux 状态文件，改成从 `herdr pane get` 拿 session id。
  再到 `~/.claude/projects` 和 `~/.claude-l2/projects` 两个目录里找对话记录（用户有两个 `CLAUDE_CONFIG_DIR`）。
- 跳转里的 `_tmux("capture-pane" …)` 和 `send-keys`（第 832–971 行）：换成上面两个 herdr 命令。
- `sidebar/trail_daemon.py`：用 `tmux list-panes` 每秒轮询，平均占约 8.5% 单核（2026-10-25 实测）。herdr 版改用事件，不照搬轮询。

## 第一阶段要做的

1. 在独立分支和 worktree 里开工（见下面"怎么开工"），插件放在仓库的 `herdr/` 子目录。
   herdr 市场支持子目录里的 manifest，以后用 `herdr plugin install threedaycat/claude-tmux-sessions/herdr` 安装。
2. 写 `herdr/herdr-plugin.toml`：一个打开侧栏的 action、一个 `placement = "split"` 的 pane。
3. 侧栏程序先用 Python 加 fzf，最大限度复用 `claude_trail.py`（先求跑通，语言以后再定）。
4. 做到下面的验收标准。

## 验收标准（每条都要有证据：命令输出、截图或数字）

1. 在 herdr 里对一个 Claude 窗格执行打开动作，右边出现整列高的侧栏，宽度约 34%，Claude 窗格的内容不乱。
2. 侧栏列出这个会话的每一轮，和 tmux 版对同一个会话列出的轮数一致。`~/.claude-l2` 账号的会话也要能列。
3. 点任意一轮，Claude 窗格滚到那一轮的最终回复。挑最早、中间、最新三轮各测一次，记下用时，和 tmux 版对比。
4. 表头有模型、账号，以及 ctx / 5h / 7d 三条柱子，规则和 tmux 版一样。
5. 侧栏开着、Claude 空闲时，插件相关进程 10 分钟的 CPU 增量（`ps -o time`）合计不超过 6 秒（约 1%）。
6. 切到别的工作区再切回来，侧栏还在；关掉侧栏，没有残留进程。
7. tmux 版行为不变：在 tmux 里打开侧栏、点一轮，照常工作。

## 不许做的

- 不改 `main` 分支，不推送，不发布，不给仓库加 `herdr-plugin` 标签。发布等用户确认。
- 不改 `~/.claude*/settings.json`，不装或卸载其他 herdr 插件，不改 `~/.config/herdr/config.toml` 里不属于本插件的内容。
- 不重启 herdr server，除非先告诉用户：重启会打断所有窗格，包括你自己。
- 起 headless Chrome 之类的东西前，先读 `~/.claude/CLAUDE.md` 里的规矩。
- 另一个会话正在 `main` 上改 tmux 版的轨迹侧栏（滚动同步、`trail_daemon.py` 降 CPU）。不要在 `main` 上动 `sidebar/`。

## 做完怎么交回

在本目录写 `PHASE1-RESULT.md`：逐条写验收标准是否达成，附证据，再写遇到的 herdr 限制和第二阶段建议。
做不成也写清楚卡在哪里、证据是什么。这本身就是有用的结论，决定记录里"什么证据出现就重启开发"要用到。

## 怎么开工

```bash
cd ~/projects/claude-tmux-sessions
git worktree add ../claude-tmux-sessions-herdr -b herdr-plugin
cd ../claude-tmux-sessions-herdr
claude   # 在 herdr 里开，好直接测；进来后说「读 docs/herdr-plugin/HANDOFF.md，做第一阶段」
```

做完、合进 `main` 之后，用 `git worktree remove ../claude-tmux-sessions-herdr` 收掉 worktree。

## 后面的阶段（这次不做，只是方向）

- 第二阶段：打磨，写英文 README 和演示 GIF，打上 `herdr-plugin` 标签发布。
- 第三阶段候选：账号切换做成插件动作（基于多个 `CLAUDE_CONFIG_DIR`，主打"不碰钥匙串、不改 `~/.claude.json`"，
  和现有的 `herdr-account-switch` 区分开）；fzf 选择器做成浮窗。
- 不搬的：底部状态栏（herdr 左侧栏已经显示每个 agent 的状态）、tmux-resurrect 恢复（herdr 自带）、
  git / 文件浮窗（已有 `herdr-file-viewer` 和 lazygit 浮窗）。
