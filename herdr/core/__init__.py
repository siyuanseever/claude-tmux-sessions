"""statetrail 的共用核心：和终端复用器无关的部分。

    transcript.py   读对话记录、切轮次（每种 agent 一个读取器，现在只有 Claude）
    claude.py       Claude 专属：配置目录、账号、订阅额度、模型和上下文大小
    render.py       画出来的东西：额度柱子、宽版 / 窄版的列表
    jump.py         让 agent 的屏幕滚到某一轮（读屏、发键交给适配层）
    i18n.py         中英文界面

适配层（herdr 插件是 ../trail.py）只管：读屏、发键、开窗格、收事件、画 fzf。
这些代码最早来自 tmux 版（threedaycat/claude-tmux-sessions 的 sidebar/、hooks/ctx_to_tmux.py、bin/usage-refresh.py），
两边切出来的轮次一致；tmux 版保持原样，两边不再共享代码。
"""
