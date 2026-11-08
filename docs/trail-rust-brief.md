# 任务书：tmux 侧边轨迹的 Rust 版

给接手的 Claude 会话看。范围、理由、用户原话都在 [trail-rust-plan.md](trail-rust-plan.md)，先读它。

## 目标

做一个 tmux 版的 Rust 侧边轨迹，替掉现在的 Python + fzf 侧栏（`sidebar/` 里的 `trail_daemon.py`、`claude_trail.py`、`trail-sidebar.sh`）。
功能 = plan 里「做」的那几条 + 第二节 tmux 版独有的全部 + 第三节两边都有的。

## 背景

- herdr 版 Rust 轨迹在 statetrail 仓库的 `rust` 分支，本机 worktree 是 `~/projects/statetrail-rs`（主 checkout `~/projects/statetrail` 在 main）。
  **先 `git pull`**：2026-11-08 时本地落后 origin/rust 10 个提交。`herdr-rs/` 是没跟踪的旧目录，别动、别提交。
- herdr 版里和宿主无关的：`transcript.rs` `claude.rs` `render.rs` `jump.rs`，以及 `chapters.rs` `search.rs`（先自己核对）。
  和 herdr 打交道的：`pane.rs` `herdr.rs`。
- 用户已从 herdr 换回 tmux（2026-11-08），herdr 已关。现在用的 tmux 配置：`~/.tmux.conf.local`（dotfiles 的 `tmux/tmux.conf.local`），
  状态栏和点击都挂在 `~/projects/claude-tmux-sessions/bin/` 的脚本上；侧栏靠 hook `hooks/tmux_status_update.py`
  写的 `~/.claude/tmux-claude-status.json` 知道窗格里是哪个会话。
- 现在侧栏后台是 launchd `com.claude-tmux.sidebar`，13 个 Claude 时实测约 49MB、1–2% CPU。

## 做法要求

1. 代码放 statetrail 仓库：和 `herdr/` 并排加 `tmux/`。宿主无关的部分抽成两边共用（比如 workspace 里一个 core crate），
   herdr 版行为不能变：抽完先让 herdr 版照样编过、它已有的测试照样过。
2. 先出最小可用版：在一个窗格旁边开出 Rust 轨迹，列出轮次、点一轮能跳、表头有额度。验证过、提交，再一条条加功能。
   每加一条功能一个 commit，commit message 写验证方式。
3. Python 侧栏在 Rust 版完全验证前照常在用，不删、不停它的 launchd。切换（改 `float-tool.sh` 的 `tool:trail`、换 launchd）
   放在最后一步，**先问用户**。
4. 内存和 CPU 要实测并写进 README：目标是常驻部分明显低于现在的 49MB / 1–2%。
5. 用户在 tmux 里全程用鼠标和触控板；跳转依赖 Claude Code 全屏界面（`/tui fullscreen`）。

## 不许做

- 不改用户正在用的 tmux 配置、hook、launchd，直到第 3 条那一步问过用户。
- 不推 statetrail 的 main 分支、不发版、不上 herdr 插件市场；`rust` 分支 fetch 不落后可以推，回报旧..新。
- 不碰 `~/.claude/settings.json`。

## 完成标准

plan 里「做」的每一条、第二节每一条都在 tmux 里实际点过、看到效果；README（tmux 版）写清安装、配置、实测内存和 CPU；
最后给用户一段人话总结：做了什么、怎么切过去、还差什么。
