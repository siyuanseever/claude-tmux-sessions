#!/usr/bin/env python3
"""resurrect_hooks.py — what tmux-resurrect gets wrong for us, fixed from its hooks.

    resurrect_hooks.py save-layout FILE   @resurrect-hook-post-save-layout
    resurrect_hooks.py pre-restore        @resurrect-hook-pre-restore-all
    resurrect_hooks.py post-restore       @resurrect-hook-post-restore-all
    resurrect_hooks.py resume [--dry-run] what restore-claude.sh runs: type
                                          `claude --resume <id>` into restored panes

Three things went wrong on the 2026-10-18 boot (and are fixed here):

1. A session launchd owns got saved and restored too. app-servers is made by
   ~/work/agent/demo/scripts/start_services.sh (launchd, at login and every
   5 min); resurrect has no business with it. Sessions named in the tmux option
   @resurrect-skip-sessions are dropped from every save (and from `last` again
   before a restore, for snapshots saved before this existed), and resume never
   types into them.

2. Whole sessions were restored into the wrong one. resurrect asks
   `tmux has-session -t app`, and tmux falls back to a *prefix* match when
   there is no exact one: with launchd's app-servers already up and app
   not yet, the answer was "yes", so every app window was created with
   `new-window -t app:N` — which also lands in app-servers. Then
   resume found no app:N.1 and skipped all seven Claudes. pre-restore
   creates each such missing session itself, as a placeholder whose only window
   is moved out to index PLACEHOLDER_WINDOW, so the exact name exists and wins;
   post-restore closes that window. Live sessions are never renamed or touched
   (a renamed app-servers would make launchd start a second one), and a
   restore that dies half-way leaves nothing worse than one empty window, which
   the next pre-restore or save cleans up (stale_cleanup).

3. claude-trail's sidebar came back as two empty shells. A pane is just a pane
   to resurrect: the sidebar (fzf) and the shell under it were saved and
   restored as plain shells, and the window then had three panes, so the
   sidebar daemon never reopened the real one. Panes with the pane option
   @resurrect_skip=1 (claude-trail sets it on both) are listed in the save as
   x_resurrect_skip lines; post-restore closes the ones that restore created,
   and the daemon reopens the sidebar the normal way. (The line type must not
   start with pane/window/state/grouped: restore.sh matches /^pane/ etc.)

resume: the restore map (~/.claude/tmux-claude-restore.json, written by the hooks
on activity) says which Claude was in which "session:window.pane". A Claude left
alone for two weeks drops out of it (RESTORE_TTL) — the two AGI ones on 10-18 —
so a pane the map doesn't know falls back to the `claude --resume <id>` the save
itself recorded as that pane's command. A map entry made under CLAUDE_CONFIG_DIR
(the second account, claude2) is resumed under the same one.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
from i18n import t as _t, EN  # noqa: E402  界面文字：zh 原样，en 查 lib/en.tsv

HOME = os.path.expanduser("~")
# Overridable so a test against a throwaway tmux server (-L) leaves the real ones alone.
RESTORE_FILE = os.environ.get("CLAUDE_TMUX_RESTORE_FILE") or os.path.join(HOME, ".claude", "tmux-claude-restore.json")
STATE_FILE = os.environ.get("CLAUDE_TMUX_RESURRECT_STATE") or os.path.join(HOME, ".claude", "tmux-resurrect-restore-state.json")
SKIP_LINE = "x_resurrect_skip"
KNOWN_LINES = ("pane", "window", "state", "grouped_session", SKIP_LINE)
SHELLS = {"zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh", "nu"}
RESUME = re.compile(r"(?:^|[\s/])claude\s.*--resume\s+(\S+)")   # `claude …` or `/path/to/claude …`
PLACEHOLDER_WINDOW = 9999
STALE_SECS = 300          # a state file this old belongs to a restore that never finished


def tmux(*args):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def resurrect_dir():
    d = (tmux("show", "-gqv", "@resurrect-dir") or "").strip()
    if d:
        return os.path.expanduser(d.replace("$HOME", HOME))
    xdg = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"),
                       "tmux", "resurrect")
    return xdg if os.path.isdir(xdg) else os.path.join(HOME, ".tmux", "resurrect")


def last_file():
    return os.path.realpath(os.path.join(resurrect_dir(), "last"))


def skip_sessions():
    return set((tmux("show", "-gqv", "@resurrect-skip-sessions") or "").split())


def read_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def write_lines(path, lines):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write("".join(l + "\n" for l in lines))
    os.replace(tmp, path)


def drop_skipped(lines, skip):
    """Every pane/window/grouped_session line of a skipped session goes."""
    if not skip:
        return lines
    out, dropping = [], False
    for l in lines:
        f = l.split("\t")
        if f[0] in KNOWN_LINES:
            dropping = f[0] in ("pane", "window", "grouped_session", SKIP_LINE) and len(f) > 1 and f[1] in skip
        # else: the rest of the previous line — resurrect writes a full command that has a
        # newline in it as is (app-servers' `zsh -ic "…"`), so it goes with that line.
        if not dropping:
            out.append(l)
    return out


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


# -- save -------------------------------------------------------------------

def cmd_save_layout(path):
    stale_cleanup()
    # Placeholder windows only live during a restore (a save can land in the middle of
    # one, or before stale_cleanup got to it); never let one into a save.
    lines = [l for l in read_lines(path) if not l.startswith(SKIP_LINE + "\t")
             and not (l.split("\t")[0] in ("pane", "window") and l.split("\t")[2:3] == [str(PLACEHOLDER_WINDOW)])]
    lines = drop_skipped(lines, skip_sessions())
    out = tmux("list-panes", "-a", "-F",
               "#{session_name}\t#{window_index}\t#{pane_index}\t#{@resurrect_skip}") or ""
    for row in out.splitlines():
        s, w, p, mark = (row.split("\t") + [""] * 4)[:4]
        if mark == "1" and s not in skip_sessions():
            lines.append(f"{SKIP_LINE}\t{s}\t{w}\t{p}")
    write_lines(path, lines)


# -- restore ----------------------------------------------------------------

def server_id():
    return (tmux("display", "-p", "#{pid}:#{start_time}") or "").strip()


def close_placeholders(state):
    """Only on the same tmux server (window ids restart from @0 with a new one, so a
    stale id could name a real window), and only a window that is still exactly the
    placeholder: at PLACEHOLDER_WINDOW, one pane."""
    if not state.get("placeholders") or state.get("server") != server_id():
        return
    for wid in state["placeholders"]:
        info = (tmux("display", "-p", "-t", wid, "#{window_id}\t#{window_index}\t#{window_panes}") or "").strip()
        if info == f"{wid}\t{PLACEHOLDER_WINDOW}\t1":
            tmux("kill-window", "-t", wid)


def stale_cleanup():
    """A restore that died before post-restore: close its placeholders, forget it."""
    try:
        age = time.time() - os.path.getmtime(STATE_FILE)
    except OSError:
        return
    if age > STALE_SECS:
        close_placeholders(load(STATE_FILE, {}))
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass


def cmd_pre_restore():
    # Whatever an earlier, unfinished restore left is not this restore's state.
    close_placeholders(load(STATE_FILE, {}))
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    path = last_file()
    lines = read_lines(path)
    if not lines:
        return
    kept = drop_skipped(lines, skip_sessions())
    if kept != lines:
        write_lines(path, kept)
    saved = {l.split("\t")[1] for l in kept if l.startswith("pane\t")}
    live = set((tmux("list-sessions", "-F", "#{session_name}") or "").split("\n")) - {""}
    placeholders = []
    for name in sorted(saved - live):
        if not any(other.startswith(name) for other in live):
            continue                      # nothing it could be mistaken for
        wid = (tmux("new-session", "-d", "-s", name, "-P", "-F", "#{window_id}") or "").strip()
        if not wid:
            continue
        tmux("move-window", "-s", wid, "-t", f"={name}:{PLACEHOLDER_WINDOW}")
        placeholders.append(wid)
    panes = (tmux("list-panes", "-a", "-F", "#{pane_id}") or "").split()
    save_json(STATE_FILE, {"server": server_id(), "placeholders": placeholders, "existing_panes": panes})


def cmd_post_restore():
    state = load(STATE_FILE, {})
    close_placeholders(state)
    existing = set(state.get("existing_panes") or [])
    n = resume(only_new=existing if state else None)
    closed = close_skipped(existing)
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    msg = [_t("已恢复 {n} 个 Claude Code 会话").format(n=n)] if n else []
    if closed:
        msg.append(_t("关掉了 {n} 个恢复出来的空侧栏").format(n=closed))
    if msg:
        tmux("display-message", ("; " if EN else "，").join(msg))


def live_panes():
    """'session:window.pane' -> [pane_id, command, cwd, @resurrect_skip]"""
    fmt = ("#{session_name}:#{window_index}.#{pane_index}\t#{pane_id}\t#{pane_current_command}"
           "\t#{pane_current_path}\t#{@resurrect_skip}")
    panes = {}
    for row in (tmux("list-panes", "-a", "-F", fmt) or "").splitlines():
        f = row.split("\t")
        if len(f) == 5:
            panes[f[0]] = f[1:]
    return panes


def skipped_coords():
    """Coordinates the save marked @resurrect_skip (sidebar + its shell)."""
    return {f"{f[1]}:{f[2]}.{f[3]}" for f in (l.split("\t") for l in read_lines(last_file()))
            if f[0] == SKIP_LINE and len(f) >= 4}


def close_skipped(existing):
    """Close the restored copies of panes saved with @resurrect_skip: only panes
    this restore made (not in `existing`) and still at a plain shell."""
    panes = live_panes()
    targets = []
    for key in skipped_coords():
        hit = panes.get(key)
        if hit and hit[0] not in existing and hit[1] in SHELLS:
            targets.append(hit[0])
    for pid in targets:                     # by id: closing one renumbers the rest
        tmux("kill-pane", "-t", pid)
    return len(targets)


def saved_resumes():
    """'session:window.pane' -> the id of `claude --resume <id>` the save recorded."""
    out = {}
    for l in read_lines(last_file()):
        f = l.split("\t")
        if f[0] != "pane" or len(f) < 11:
            continue
        m = RESUME.search(f[10].lstrip(":"))
        if m:
            out[f"{f[1]}:{f[2]}.{f[5]}"] = (m.group(1), f[7].lstrip(":"))
    return out


def resume(only_new=None, dry_run=False):
    mapping = load(RESTORE_FILE, {})
    if not isinstance(mapping, dict):
        mapping = {}
    targets = {k: (e.get("session_id"), e.get("cwd"), e.get("config_dir") or "")
               for k, e in mapping.items() if isinstance(e, dict)}
    for k, (sid, cwd) in saved_resumes().items():
        targets.setdefault(k, (sid, cwd.replace("\\ ", " "), ""))
    skip = skip_sessions()
    panes = live_panes()
    # The sidebar's shell is a plain shell in the Claude's cwd — exactly what a stale map
    # entry for that coordinate (a teammate that used to sit there) looks for. Never type there.
    sidebar = skipped_coords()
    count = 0
    for key, (sid, cwd, config) in sorted(targets.items()):
        if not sid or key not in panes or key.split(":")[0] in skip or key in sidebar:
            continue
        pane_id, cmd, here, marked = panes[key]
        if marked == "1":
            continue
        if only_new is not None and pane_id in only_new:
            continue                      # was there before this restore: not ours to type into
        if cmd not in SHELLS:
            continue                      # busy (maybe Claude already resumed) — hands off
        if cwd and cwd != here:
            continue                      # layout changed since it was recorded
        line = f"claude --resume {shlex.quote(sid)}"
        if config:
            line = f"CLAUDE_CONFIG_DIR={shlex.quote(config)} {line}"
        if dry_run:
            print(f"would resume {key} ({pane_id}, {here}): {line}", file=sys.stderr)
        else:
            tmux("send-keys", "-t", pane_id, line, "Enter")
        count += 1
    return count


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "save-layout" and len(sys.argv) > 2:
        cmd_save_layout(sys.argv[2])
    elif cmd == "pre-restore":
        cmd_pre_restore()
    elif cmd == "post-restore":
        cmd_post_restore()
    elif cmd == "resume":
        n = resume(dry_run="--dry-run" in sys.argv)
        if n and "--dry-run" not in sys.argv:
            tmux("display-message", _t("已恢复 {n} 个 Claude Code 会话").format(n=n))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
