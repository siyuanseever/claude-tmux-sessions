# 演示 GIF 的录制装置

`docs/demo.gif` 是这套脚本录的，任何人都能复现出同一张，图不会随着代码演进悄悄失真，也不用把真实会话发出去。

```bash
python3 demo/gif.py            # → docs/demo.gif，各帧在 $TMPDIR/statetrail-gif/
python3 demo/gif.py --stage    # 只搭好环境不录，tmux -L herdrdemo attach -t demo 进去手动试
```

| 文件 | 做什么 |
|---|---|
| `gif.py` | 在私有 tmux server（`-L herdrdemo`）里用假 `$HOME`（`/private/tmp/herdrdemo`）起一个完全隔离的 herdr，按分镜点击、截帧、拼 GIF，最后全收掉 |
| `fixture.py` | 造假 `$HOME`：编出来的对话记录、假账号 `you@example.com`、写死的额度读数、herdr 和插件的配置、一个小 git 仓库 |
| `fake_claude.py` | 假 Claude：把对话记录画成 Claude 全屏界面的样子，能翻页、到顶、到底、滚轮，向 herdr 报上自己是 claude |
| `render.sh` + `ansi2html.py` | ANSI → HTML → headless Chrome 截图（从 claude-tmux-sessions 的 `docs/demo/` 搬来） |

要的东西：herdr、tmux、python3、fzf、lazygit、ffmpeg、Google Chrome。起 Chrome 之前先确认它不会下载本地 AI 模型
（`defaults read com.google.Chrome GenAILocalFoundationalModelSettings` 要是 `1`）；Chrome 用固定的配置目录，不每次新建。

## 诚实边界

1. **Claude 是假的。** `fake_claude.py` 不调模型，只把一份编好的对话记录画出来；它的界面照 Claude Code 全屏模式的样子画
   （`❯` 你的话、`⏺` 回复、往上翻时底部的「Jump to bottom」），这正是 statetrail 认位置要用的特征，所以图上的跳转是真跳。
2. **数据是编的。** 对话、账号、额度百分比都是 fixture。额度读数的时间是「刚刚」，插件 30 分钟内不会去查，演示不碰任何真实账号。
3. **轨迹是真的。** 插件是 `herdr plugin link` 进隔离 herdr 的真代码：自动打开、列表、跳转、宽窄切换、底部按钮开的 lazygit
   浮窗都是它自己做的。点击是往 herdr 客户端发真实的鼠标序列。
4. **图标用 unicode 档、配色是渲染脚本的调色板加 herdr catppuccin 主题。** 栅格化走浏览器，不依赖 Nerd Font；
   默认配置（`icons = "nerd"`）下真实的图标长得不一样。

## 踩过的坑

- 新装的 herdr 先弹欢迎框，只认回车、Esc 关不掉，开着时**所有鼠标事件都被它吞了**；回车后再弹一页 integrations 设置，Esc 关。
- 假 Claude 报会话 id 要用 `--source herdr:claude`（herdr 自带 Claude 集成的来源），用自定义来源报，`agent_session` 一直是空的。
- 窗口要够宽（200 列）：轨迹要给 Claude 留 110 列，150 列时它会以窄版打开。
- lazygit 第一次运行的欢迎框会盖住浮窗：fixture 里预先写好它的 `state.yml`。
- 录制中途出错时 tmux 关了，但 herdr server 还在；下次开头先 `server stop` 它，再删假 `$HOME`。
