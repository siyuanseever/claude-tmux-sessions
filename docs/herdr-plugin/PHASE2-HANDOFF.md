# 交接：claude-trail 第二阶段（准备发布）

写于 2026-10-25。第一阶段结论见 `PHASE1-RESULT.md`（在 `herdr-plugin` 分支）：7 条验收全部达成，跳转比 tmux 版快，空闲约 0.1% 单核。
背景和取舍见 [../decisions/2026-10-25-herdr.md](../decisions/2026-10-25-herdr.md)。

## 一句话目标

把 claude-trail 做到**陌生人照着 README 能装上、能看懂、愿意点 star** 的程度。到"准备好发布"为止，发布这一步等用户确认。

## 定位（写 README 时照这个说）

- 核心是**贴在正在运行的 Claude 旁边的会话轨迹**：每轮一行，点一下 Claude 就滚到那一轮的最终回复。
  同类高 star 项目（claude-code-history-viewer 2.3k、claude-code-transcripts 1.7k、claude-code-log 1.3k、
  claude-code-trace 381、agent-trail 40）都是"事后翻看"的独立应用，没有一个贴在运行中的 agent 旁边、能跳转。这就是差异点。
- 第二位：表头的模型、账号，以及 ctx / 5h / 7d 额度柱子。herdr 生态里的额度插件只往左侧栏塞一行字。
- 附赠：可配置的按钮（默认 Git = lazygit、文件 = yazi），在 Claude 的目录里弹浮窗打开。
- 和 herdr-sidebar 是竞争关系：claude-trail **不依赖、不调用任何别的插件**，只依赖独立的终端程序。

## 要做的

1. **共用核心**：现在的 `herdr/trail.py` 是 `sidebar/claude_trail.py` 的复制改写。把"读对话记录、切轮次、算额度、画柱子"抽成一份共用代码，
   tmux 版和 herdr 版只保留薄的适配层（读屏、发键、开窗格）。读对话记录这一层做成可替换的，给以后支持 Codex / OpenCode 留口子，这次只实现 Claude。
2. **用会话名给 tab 命名**：用户没手动起名的 tab，用 Claude 的会话名（`terminal_title_stripped`）命名，会话名变了跟着改。
3. **安装体验**：在一台干净的环境里，按 README 走一遍 `herdr plugin install threedaycat/claude-tmux-sessions/herdr`。
   列出所有前置依赖（python3、fzf、可选的 lazygit / yazi），缺了要给出明确提示，不能静默失败。
4. **英文 README**（`herdr/README.md`），加中文版：一句话定位、演示 GIF、安装、按钮配置、已知限制（细条最窄 15 列、右键菜单进不了插件动作，附 herdr issue #5550）。
5. **演示 GIF**：沿用 `docs/demo/` 的做法。起 headless Chrome 之前先确认 Chrome 策略
   `GenAILocalFoundationalModelSettings` = 1（见 `~/.claude/CLAUDE.md`），复用固定的配置目录，用完收掉。
6. **候选名字**：给用户 3 个候选，附 GitHub 撞名情况。已查过的：`claude-trail` 有 42 个同名仓库，而且用户自己有一个私有的旧仓库 `threedaycat/claude-trail`；
   `herdr-trail` 被 `catoncat/herdr-trail` 占了，那是一个备忘插件；`herdr-claude-trail` 是 0 个。以后要支持多种 agent 的话，名字别绑死 Claude。
7. **Rust 要不要改写**：只为了安装方便，不为性能，Python 版已经够省。估算改写的工作量，和"要求用户装 python3 + fzf"的摩擦对比一下，给用户一个建议，不要直接动手改写。

可以借鉴、但这次**不做**的（记进 README 的 Roadmap 就行）：每轮 token 用量、展开工具调用、标出 `/compact` 的位置、跨会话搜索。

## 验收标准

1. tmux 版和 herdr 版都改用共用核心之后，两边对同一个会话列出的轮次一致，跳转照常。tmux 版不能退步。
2. 按钮配置：不写配置时默认是 lazygit 和 yazi；改成别的命令能生效；命令不存在时有提示。
3. 照着 README 从零安装，一次成功。记录用了哪些命令、花了多久。
4. GIF 在 15 秒以内，演示打开、点一轮跳转、收起成细条、再展开、点 Git 按钮。
5. 交一份发布前检查清单 `PHASE2-RESULT.md`：名字候选、Rust 建议、待用户确认的事项。

## 不许做的

- 不推送，不发布，不打 `herdr-plugin` 标签，不改 GitHub 仓库名。这些都等用户确认。
- 不改 `~/.claude*/settings.json`，不碰别的 herdr 插件和用户的 herdr 配置里不属于本插件的部分。
- 测试时不往 `--resume` 出来的别人的会话里输入。要输入就用 `--fork-session`，或者专门开一个测试会话。测完关掉测试 tab。
- 不重启 herdr server，除非先告诉用户。
