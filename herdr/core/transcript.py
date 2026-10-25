"""transcript.py — 读对话记录、切轮次。

每种 agent 一个读取器，接口都一样（以后加 Codex / OpenCode 就是再写一个类，登记进 READERS）：

    tr = reader_for("claude")(path)
    tr.update()      # 读新追加的部分；返回这次有没有读到新东西
    tr.visible()     # [{id, kind, prompt, reply, ts, compacted, has_text}, ...]，按时间正序

一轮 = 一次让 agent 开始干活的输入（你说的话，或者后台任务完成、别的会话发来的消息），加上它之后
agent 写的文字。reply 只留最后一段文字的第一行，够侧栏显示、够跳转时认位置。

切轮规则从 tmux 版搬来（sidebar/claude_trail.py 的 _trigger、parse_session，sidebar/trail_daemon.py 的
Transcript），两边对同一份对话记录切出来的轮次一致。
"""
import json
import re

from .i18n import t as _t

# Plain-string user entries that are harness noise, not something that makes Claude reply.
NOISE_PREFIXES = ("<local-command", "<command-name>", "<command-message>",
                  "This session is being continued from a previous conversation")
SLASH_CMD = re.compile(r"^/[\w:-]+(\s|$)")
PROMPT_MAX = 4000        # 展开时最多显示你那句话的多少个字


def text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def first_line(s):
    for line in s.splitlines():
        line = re.sub(r"[#*`>|]", "", line)
        # 下划线只剥 markdown 的强调（_重点_、__粗体__），不剥标识符里的：created_at、my_var 原样显示
        line = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", line).strip(" -")
        if line:
            return line
    return ""


def _trigger(entry):
    """If this entry starts a new stretch of Claude work, return (kind, text)."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    kind = (entry.get("origin") or {}).get("kind")
    text = text_of(content).strip()
    if kind == "task-notification":
        return ("notify", _t("后台任务完成"))
    if kind == "peer":
        return ("peer", _t("收到其他会话的消息"))
    if entry.get("isMeta") or not text:
        return None
    if kind == "human":
        return ("human", text)
    if text.startswith(NOISE_PREFIXES) or text.startswith("<") or SLASH_CMD.match(text):
        return None
    return ("human", text)


class ClaudeTranscript:
    """Claude Code 的对话记录（<配置目录>/projects/*/<会话 id>.jsonl）的增量解析：只读新追加的行，
    每轮只留侧栏要显示的东西（不留全文），十几份大对话记录常驻也占不了多少内存。"""

    agent = "claude"

    def __init__(self, path):
        self.path, self.pos, self.size = path, 0, -1
        self.stops, self.title, self.cur = [], None, None

    def update(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size == self.size:
            return False
        if size < self.pos:                       # 被截断/重写了：从头来
            self.__init__(self.path)
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        end = data.rfind(b"\n") + 1               # 最后一行可能还没写完，留到下次
        self.pos += end
        self.size = size
        for line in data[:end].decode("utf-8", "replace").splitlines():
            try:
                self.feed(json.loads(line))
            except ValueError:
                continue
        return end > 0

    def feed(self, e):
        t = e.get("type")
        if t == "ai-title":
            self.title = e.get("aiTitle") or self.title
            return
        if t == "system" and e.get("subtype") == "compact_boundary":
            # Claude's screen restarts here: earlier turns are no longer shown in it.
            for st in self.stops:
                st["compacted"] = True
            return
        trig = _trigger(e)
        if trig:
            self.cur = {"kind": trig[0], "prompt": trig[1][:PROMPT_MAX], "ts": e.get("timestamp"),
                        "id": e.get("uuid"), "reply": "", "has_text": False, "compacted": False}
            self.stops.append(self.cur)
            return
        if t == "assistant" and not e.get("isSidechain") and self.cur is not None:
            txt = text_of((e.get("message") or {}).get("content")).strip()
            if txt:
                self.cur["has_text"] = True
                self.cur["reply"] = first_line(txt)[:200]

    def visible(self):
        # A notification/peer stretch where Claude said nothing is noise; a human prompt
        # with no reply yet (still working, or interrupted) stays so you can see it.
        return [s for s in self.stops if s["has_text"] or s["kind"] == "human"]


READERS = {"claude": ClaudeTranscript}


def reader_for(agent):
    return READERS.get(agent)
