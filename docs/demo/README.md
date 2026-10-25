# 截图装置 (internal)

`docs/img/` 下的截图和 GIF 不是手工修的图,也不是拿作者真机截的 —— 是这套脚本生成的,任何人都能
复现出同一张。这样图不会随着代码演进悄悄失真,也不用把真实项目名和路径发出去。

**它是什么:** 真代码 + 演示数据。
**它不是什么:** 不是对着终端窗口拍的照片(见下面"为什么不用 screencapture")。

```bash
docs/demo/shots.sh     # → docs/img/{screen,statusbar,picker,click-done,git,files}.png,再把 docs/img/*.svg 渲染成 .png
docs/demo/gif.sh       # → docs/img/demo.gif,各帧在 $TMPDIR/claude-tmux-gif/
```

| 文件 | 做什么 |
|---|---|
| `stage-demo.sh` | 起私有 tmux server(`-L shotdemo`)、假 `$HOME`(`/private/tmp/shotroom`)、5 个 session 22 个窗口和对应的状态文件、额度缓存 |
| `stage-screen.sh` | 被 `shots.sh` / `gif.sh` source:在上面加底栏、侧栏、一个在等确认的会话,再从第二个私有 server(`-L shotouter`)attach 上去,抓外层那一格就是整屏 |
| `shots.sh` | 像鼠标点击一样调真实脚本(`status-click.sh`、`float-tool.sh`……),每一步抓一张 |
| `gif.sh` | 分镜写在文件开头,帧补齐到同一尺寸后用 ffmpeg 拼 GIF |
| `sidebar-frame.py` | 用侧栏的真代码在假数据上画一帧侧栏 |
| `render.sh` + `ansi2html.py` | ANSI → HTML → headless Chrome 截图(2 倍像素) |
| `tmux2ansi.py` | tmux 的 `#[...]` 样式转成 ANSI |

渲染用一个固定的 Chrome 配置目录(`$TMPDIR/claude-tmux-render-chrome`),只杀自己起的那个 Chrome;
起之前先确认 Chrome 不会下载本地 AI 模型(`GenAILocalFoundationalModelSettings` 策略为 1)。
git 浮窗那张要装 lazygit,文件浮窗那张要装 yazi(有 `~/.config/yazi` 就复制进假 `$HOME`,配色跟你的一样)。

## 诚实边界

四件事必须说清楚,否则这些图就是在暗示一些它没有兑现的东西:

0. **选择器那张是满屏的。** 真实的 `prefix + g` 弹在 95%×85% 的框里,`shots.sh` 用 100%:小一圈的话,
   框外露出的侧栏和 Claude 会在边上漏进来,图就花了。除了框的大小,图上其它一切都是默认行为。
1. **数据是编的。** 22 个 pane、时长、额度百分比、token 数、侧栏里的对话全是 fixture,不是谁的真实
   工作状态。配比是刻意排的:四个状态、老化变暗的 DONE、一条 `⏸ WAIT` 全覆盖。**布局是真的** ——
   每一个字符都是 `list-rows.sh` / `status-badge.sh` / `fzf` / lazygit / yazi 自己画出来的,所以图上对齐了
   就是真的对齐了。(正是这个流程逼出过两个真 bug:窗口名不截断、以及年龄列正好 15 格时把后面整列
   顶出去一格。)底栏最右边的 CPU / 内存 / 磁盘是跑脚本那台机器的真数。
2. **栅格化走的是浏览器,不是终端。** `ansi2html.py` 把每个非 ASCII 字符放进一个固定宽度的格子里再交给
   headless Chrome —— 浏览器字体的 CJK 步进并不等于两个西文字符宽,直接丢进 `<pre>` 会渲出真实终端里
   并不存在的错位。字形栅格是精确的,**但配色是这个脚本里的一套调色板,不是你的终端主题**。
3. **为什么不用 `screencapture`。** 那才是最本真的做法(真窗口、真字体、真主题),但 macOS 的屏幕录制
   权限得手工授权给跑 Claude Code 的那个终端 App。

## 附:为什么假 $HOME 放在 `/private/tmp`

macOS 的 `/tmp` 是指向 `/private/tmp` 的软链,而 tmux 报的是解析后的真实路径。`HOME` 设成
`/tmp/...` 的话 `list-rows.sh` 里的 `~` 缩写永远匹配不上,每一行都会显示绝对路径。
