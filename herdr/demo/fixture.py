#!/usr/bin/env python3
"""演示用的假 $HOME：一份编出来的 Claude Code 对话记录、假账号、新鲜的额度读数、herdr 和插件的配置、一个小 git 仓库。

    fixture.py <假 HOME> <插件目录>     → 打印会话 id

里面没有任何真实的会话、路径或账号：对话是编的，账号是 you@example.com，额度读数是写死的。
额度是写进假 .claude.json 的缓存（插件默认只读这份，不去查真实额度）。
"""
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME, PLUGIN = Path(sys.argv[1]), Path(sys.argv[2])
WORK = HOME / "work" / "photo-tidy"
SID = "0d3e6a2c-5b1f-4c8e-9a47-3e2f1b6c9d10"

TURNS = [
    ("Set up a small Python CLI that sorts a photo folder into year/month folders.",
     "Done. `photo-tidy` is a single-file CLI with no dependencies:\n\n"
     "- `tidy.py <folder>` reads each photo's EXIF date and moves it to `YYYY/MM/`\n"
     "- photos without EXIF fall back to the file's modification time\n"
     "- `--dry-run` prints the moves without touching anything\n\n"
     "I ran it on the sample folder in `tests/fixtures`: 14 photos, all moved where expected."),
    ("What happens if two photos end up with the same name in the same month?",
     "Right now the second one would overwrite the first — that's a real bug, thanks for asking.\n\n"
     "I changed `move_photo()` so a clash gets a suffix: `IMG_0042.jpg`, then `IMG_0042-1.jpg`, `IMG_0042-2.jpg`.\n"
     "Added a test that tidies two different files with the same name into one month; both survive.\n\n"
     "All 9 tests pass."),
    ("Can it skip files that are already in the right place?",
     "Yes. Before moving, it now checks whether the photo's current folder already matches `YYYY/MM`.\n"
     "If it does, the file is left alone and counted as `kept`.\n\n"
     "Running it twice in a row is now a no-op: the second run reports `0 moved, 14 kept`."),
    ("Add a progress line, the real folder has 40k photos.",
     "Added a single updating progress line on stderr: `12,480 / 40,112  31%  ETA 2m10s`.\n\n"
     "It only redraws every 200 files, so it costs nothing measurable. With `--quiet` it's off,\n"
     "and it switches itself off when stderr isn't a terminal, so logs stay clean."),
    ("It's slow on the NAS. Where does the time go?",
     "I profiled a run over 2,000 photos on a network share. 81% of the time is reading EXIF:\n"
     "the library reads the whole file just to get the header.\n\n"
     "Switched to reading only the first 64 KB, which is where the EXIF block lives.\n"
     "Same 2,000 photos: 94 s before, 11 s after. Results are identical — I diffed the move plans."),
    ("Some old scans have no EXIF and a wrong mtime. Ideas?",
     "Two options, both opt-in so nothing changes by default:\n\n"
     "1. `--date-from-name`: parse dates like `2009-07-14` or `20090714` out of the file name\n"
     "2. `--unsorted <folder>`: anything without a trustworthy date goes there instead of a guessed month\n\n"
     "I'd start with 2: it never puts a photo in the wrong place. Want me to add it?"),
    ("Yes, add --unsorted.",
     "Added. Photos without EXIF go to the `--unsorted` folder when you pass it; without the flag,\n"
     "behaviour is unchanged (mtime fallback).\n\n"
     "The summary line now has a third number: `13,002 moved, 26,991 kept, 119 unsorted`."),
    ("Write a short README.",
     "Wrote `README.md`: what it does in one sentence, install, the four flags with an example each,\n"
     "and what it never does (delete files, touch anything outside the folder you give it).\n\n"
     "Kept it under 60 lines."),
    ("Is it safe to run on the original photos?",
     "It only moves files within the folder you pass, never deletes, and `--dry-run` shows the full plan first.\n"
     "The one risk is a crash halfway through: files are moved one at a time, so nothing is lost,\n"
     "but the folder would be half sorted. Running it again finishes the job.\n\n"
     "I'd still do a `--dry-run` on the real folder first and skim the plan."),
    ("Commit everything.",
     "Committed as `a41c9e2`: CLI, clash suffixes, skip-in-place, progress line, faster EXIF reads,\n"
     "`--unsorted`, README and 12 tests. All tests pass; the dry run on the sample folder is clean."),
]


def transcript():
    t0 = datetime.now(timezone.utc) - timedelta(minutes=50)
    out = [{"type": "ai-title", "aiTitle": "photo-tidy CLI", "sessionId": SID}]
    tokens = 18_000
    for i, (ask, reply) in enumerate(TURNS):
        ts = t0 + timedelta(minutes=5 * i)
        out.append({"type": "user", "uuid": str(uuid.uuid4()), "sessionId": SID,
                    "timestamp": ts.isoformat().replace("+00:00", "Z"), "origin": {"kind": "human"},
                    "message": {"role": "user", "content": ask}})
        tokens += 21_000
        out.append({"type": "assistant", "uuid": str(uuid.uuid4()), "sessionId": SID, "effort": "high",
                    "timestamp": (ts + timedelta(seconds=40)).isoformat().replace("+00:00", "Z"),
                    "message": {"role": "assistant", "model": "claude-opus-5-5",
                                "content": [{"type": "text", "text": reply}],
                                "usage": {"input_tokens": 4, "cache_creation_input_tokens": 1200,
                                          "cache_read_input_tokens": tokens, "output_tokens": 600}}})
    return out


def main():
    proj = HOME / ".claude" / "projects" / ("-" + str(WORK).strip("/").replace("/", "-"))
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{SID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in transcript()))
    (HOME / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 400_000}))

    # 额度：写进假 .claude.json 的 cachedUsageUtilization（插件默认只读 Claude Code 的这份缓存，不碰任何凭据）
    now = datetime.now(timezone.utc)
    cache = {"fetchedAtMs": int(time.time() * 1000) - 120_000, "accountUuid": "demo-account",
             "utilization": {"five_hour": {"utilization": 38, "resets_at": (now + timedelta(hours=2, minutes=14)).isoformat()},
                             "seven_day": {"utilization": 21, "resets_at": (now + timedelta(days=4, hours=6)).isoformat()}}}
    (HOME / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "you@example.com", "accountUuid": "demo-account"},
                                                   "cachedUsageUtilization": cache}))

    # herdr 和插件的配置：图标用 unicode 档，渲染时不依赖 Nerd Font 字体
    cfg = HOME / ".config" / "herdr"
    (cfg / "plugins" / "config" / "statetrail").mkdir(parents=True, exist_ok=True)
    # live_usage = false：默认会用钥匙串里的真令牌去查（钥匙串不跟着假 $HOME 走），演示里就会录进真实用量
    (cfg / "plugins" / "config" / "statetrail" / "config.toml").write_text('icons = "unicode"\nlang = "en"\nlive_usage = false\n')
    (cfg / "config.toml").write_text('[theme]\nname = "catppuccin"\n\n[ui]\nstatus_indicators = "dots"\n')

    # lazygit 第一次运行会弹欢迎框，盖住整个浮窗：告诉它已经看过了（macOS 上状态文件在 Library/Application Support）
    try:
        ver = subprocess.run(["lazygit", "--version"], capture_output=True, text=True).stdout
        ver = ver.split("version=")[1].split(",")[0]
    except (OSError, IndexError):
        ver = ""
    for d in (HOME / "Library" / "Application Support" / "lazygit", HOME / ".config" / "lazygit"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.yml").write_text(f"startuppopupversion: 5\nlastversion: {ver}\n")

    # 给 Git 按钮（lazygit）看的小仓库
    WORK.mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(HOME), "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "GIT_AUTHOR_NAME": "you",
           "GIT_AUTHOR_EMAIL": "you@example.com", "GIT_COMMITTER_NAME": "you", "GIT_COMMITTER_EMAIL": "you@example.com"}
    if not (WORK / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=WORK, env=env, check=True)
        for name, body, msg in [("tidy.py", "# photo-tidy\n", "Sort photos into year/month folders"),
                                ("tests/test_tidy.py", "def test_clash(): pass\n", "Suffix clashing names"),
                                ("README.md", "# photo-tidy\n", "README")]:
            (WORK / name).parent.mkdir(parents=True, exist_ok=True)
            (WORK / name).write_text(body)
            subprocess.run(["git", "add", "."], cwd=WORK, env=env, check=True)
            subprocess.run(["git", "commit", "-q", "-m", msg], cwd=WORK, env=env, check=True)
        (WORK / "tidy.py").write_text("# photo-tidy\n# --unsorted\n")      # 一处没提交的改动
    print(SID)


main()
