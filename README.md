<h1 align="center">claude-tmux-sessions</h1>

<p align="center">
  <b>Run several <a href="https://claude.com/claude-code">Claude Code</a> teams from tmux: who needs you, what everyone is doing, what this session said, and one-click account switching.</b>
</p>

<p align="center">
  One Claude account per project, one session per role, and you only step in when a
  session actually needs you. This toolkit makes that workflow run: a status bar,
  a picker, a per-session sidebar and account switching — all mouse-friendly.
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="tmux + bash + python3" src="https://img.shields.io/badge/tmux%20%2B%20bash%20%2B%20python3-1f425f.svg">
  <img alt="Claude Code hooks" src="https://img.shields.io/badge/Claude%20Code-hooks-D97757.svg">
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="docs/img/en/demo.gif" alt="Demo: status bar → picker → jump to the session waiting on you → switch account from the sidebar" width="960">
</p>

<p align="center"><sub>The whole toolkit in 15 seconds: status bar, picker, jumping to the session that waits for you, and switching accounts from the sidebar. Rendered by the real scripts on demo data — <a href="docs/demo/">docs/demo</a>.</sub></p>

> This is one piece of my Claude Code workbench. Voice input (how it corrects itself, how an LLM proofreads,
> how Claude maintains the vocabulary for me) and a local web dashboard (usage, configuration) are written up
> in **[docs/workbench.md](docs/workbench.md)**, with screenshots of every part.

## The five pieces

| | What it covers | Where |
|---|---|---|
| **Status bar** | Every session's state: who's waiting on you (`⏸`), who finished and you haven't looked (`✔`), who's running (`▶`); on the right, machine CPU / memory / swap / disk | tmux status line, visible from any window, every segment clickable |
| **Picker** | All sessions at a glance, grouped by tmux session, live preview, type a number to jump | `prefix + g`, or click the left end of the status bar |
| **Sidebar** | **This** session: every turn it took (click one and the main pane scrolls there), context and 5h / 7d quota, uncommitted-file count | docked to the right of each Claude, opens automatically when the window is wide enough |
| **git / file pop-ups** | lazygit or yazi floating over the right of the window, in this Claude's directory; click outside to close | buttons on the sidebar |
| **Account switch** | Move this session to another Claude account and keep the same conversation; refuses if it's busy or you have an unsent draft, and asks the lead to wind down its team first | the ⇄ button on the sidebar |

Anything global lives in the status bar; anything about the current session lives in the sidebar.
States come from Claude Code hooks, not polling.

Running Claude Code in [herdr](https://herdr.dev) instead of tmux? The same sidebar is a herdr plugin there:
[statetrail](https://github.com/siyuanseever/statetrail).

<p align="center">
  <img src="docs/img/en/screen.png" alt="The whole screen: Claude working on the left, the sidebar with turns and quota on the right, status counts and the window list at the bottom" width="960">
</p>


---

## The problem

You keep several Claude Code sessions going in parallel — different tmux windows,
different projects. One's mid-task, one just finished, and one has been
**blocked on a permission prompt for twenty minutes**, quietly stalled, while
you stared at a different window. So you cycle through windows to check who
needs you, which is exactly the bookkeeping a computer should be doing.

This makes that state **ambient**: panes report their own status via Claude Code
hooks, your status line shows the whole picture from any window, and one key
opens a picker that jumps you to the right pane.

Nothing polls — the hooks fire only when something actually changes, and the picker reads one
small JSON file. The one long-running process is the sidebar's (kept alive by launchd on macOS),
and it only reads newly appended transcript lines.

## Four states, read by shape

|  | State | Meaning |
|---|-------|---------|
| `⏸` | **WAIT** | Claude needs an explicit **approve/deny**. It is *stalled*. The only state that notifies you. |
| `▶` | **RUN** | Claude is working. Nothing for you to do. |
| `✔` | **DONE** | Finished, **unread** — a result to go look at. |
| `✓` | **READ** | Finished, and you've already visited it. Quiet. |

Colour carries the same information (red / yellow / green / blue), but the icon
means you can read the list without relying on it.

## What you get

- **A WAIT that won't vanish on you.** When a pane is blocked on a permission prompt, the `⏸`
  count in the status bar turns red, and so does that window's cell in the window list, **until
  you deal with it**; click `⏸` to jump to the one that has waited longest. Plus a one-shot flash,
  a sound, and a macOS notification — several channels, because any one of them can miss you.
- **One key to the right pane.** `prefix + g` opens an `fzf` list of every
  tracked pane, grouped by session, with a live preview that follows the cursor.
  `Enter` jumps. Or skip the UI entirely: `prefix + a` goes straight to whichever
  pane needs you most.
- **Type-the-number jumps.** Press a row's number to go there. Two-digit rows
  work too (`1` then `2` → pane 12), and single digits still fire instantly when
  they can't begin a bigger number.
- **An inbox, not a dashboard.** Visiting a `DONE` pane marks it `READ`; `ctrl-x`
  archives ones you don't care about. Both come back on their own the next time
  that pane does something new — so the list stays short without you curating it.
- **Scales past a screenful.** The quiet panes — `READ`, and unread `DONE` you
  haven't touched in hours — **collapse by default**, leaving only what's
  actionable, with a line under each session saying exactly what it's holding
  back (`⋯ 收起 3 个(2 已读 · 1 搁置) · a 展开`). A session with only *one*
  quiet pane doesn't collapse at all: that line would take the row it saved.
  `a` expands everything. Row numbers are assigned *before* collapsing, so a
  pane's number never changes when you toggle — it just means visible numbers
  can skip.
- **Session mode.** `h` flips the cursor to session headers, and the preview
  becomes one card per pane in that session: its task line, model, context-window
  meter, and a quote of Claude's last reply — read from each pane's transcript,
  not scraped off the screen. One glance answers "what is everyone up to".
- **Your real usage quota, ambiently.** The status segment and the picker footer
  both show your actual 5h/7d rate-limit windows (from Claude Code's own cached
  `/usage` data), plus live per-model token counts from transcripts.
- **Survives a tmux crash.** Optional [tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect)
  integration re-runs `claude --resume <session_id>` in each restored pane, so
  your sessions come back — not just empty shells. `bin/resurrect_hooks.py` also
  keeps sessions another program owns out of the save (`@resurrect-skip-sessions`),
  stops resurrect's prefix match from restoring `foo` into an existing `foo-bar`,
  and closes restored copies of panes marked `@resurrect_skip` (claude-trail's sidebar);
  install.sh prints the four tmux lines to add.

## Heads up before you install

- **English or Chinese UI.** Pick one at install time with `./install.sh --lang en` (or `--lang zh`);
  without the flag it follows your system language. The choice is stored in
  `~/.config/claude-tmux/lang`. To change it, run `./install.sh --lang …` again: that also restarts the sidebar
  daemon, which reads the language once at start. `CLAUDE_TMUX_LANG` overrides it too, but only reaches scripts
  started by key bindings if it is set in the tmux server's environment.
- **Developed and used daily on macOS.** The macOS-specific parts are isolated to
  two things: the system notification (`terminal-notifier`/`osascript`) and the
  alert sound (`afplay`). Everything else is plain tmux + bash + python3 and
  *should* work on Linux — those two calls fail silently — but that path is
  **untested**, so treat Linux as unverified rather than supported. Reports
  welcome.
- **Needs a Nerd Font.** Sidebar and status-bar buttons are [Nerd Font](https://www.nerdfonts.com/)
  icons. Turn off "use a different font for non-ASCII text" in your terminal, or the icons get drawn
  by a font that doesn't have them and show up as question marks.
- **A few spots read the screen.** States come from hooks, but the sidebar's "jump to turn N" and
  the account switch's draft check have to read Claude Code's screen text, so a Claude Code UI
  change may need a matching fix there.
- **It writes to your Claude Code config.** `install.sh` symlinks a dozen-odd scripts into
  `~/.claude/hooks/`, merges hooks (`UserPromptSubmit`, `Stop`, `Notification`, `PostToolUse`,
  `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`) into `~/.claude/settings.json`,
  preserving whatever's already there, and on macOS installs a launchd service for the sidebar.
  If you'd rather do that by hand, read `install.sh` first — it's ~150 lines and does nothing
  clever. Uninstall instructions are below.

## Install

```bash
git clone https://github.com/siyuanseever/claude-tmux-sessions.git ~/projects/claude-tmux-sessions
~/projects/claude-tmux-sessions/install.sh
```

**Requirements:** tmux, [fzf](https://github.com/junegunn/fzf), python3, and a
Claude Code with hooks support. Optionally
[terminal-notifier](https://github.com/julienXX/terminal-notifier) so clicking
the WAIT notification jumps to the pane.

`install.sh` symlinks the scripts into `~/.claude/hooks/`, merges the hooks into
`~/.claude/settings.json`, and on macOS installs the sidebar daemon as a launchd service
(skip it with `CLAUDE_TMUX_NO_DAEMON=1 ./install.sh`). Safe to re-run.

Three steps are left to you, since tmux configs vary. In `~/.tmux.conf` (or
`~/.tmux.conf.local` if you use [gpakosz/.tmux](https://github.com/gpakosz/.tmux)):

**1. Keys, mouse and hooks** — one line:

```tmux
source-file ~/projects/claude-tmux-sessions/tmux/claude-tmux.conf
```

It binds `prefix + g` (picker), `prefix + a` (jump to whoever needs you most), status-bar clicks,
click-outside-to-close for the pop-ups, a sidebar that doesn't steal focus, and marks a pane as
seen when you focus it. Want other keys? Copy [`tmux/claude-tmux.conf`](tmux/claude-tmux.conf) and edit.

**2. Status bar** — splice these into your `status-left` / `status-right`:

```tmux
#(~/.claude/hooks/status-badge.sh states '' '#S')  # state counts ⏸ ✔ ▶ ✓ (this session only), clickable
#(~/.claude/hooks/float-tool.sh --buttons)         # sidebar toggle
#(~/.claude/hooks/tmux-sysstat.sh)                 # CPU / memory / swap / disk, clickable
#(~/.claude/hooks/status-badge.sh quota)           # (optional) 5h / 7d quota; the sidebar has it already
```

**3. Account switching** (optional) — list your accounts and their `CLAUDE_CONFIG_DIR` in
`~/.config/claude-tmux/accounts.json`:

```json
{"accounts": {"work": {"config_dir": "~/.claude"},
              "home": {"config_dir": "~/.claude-home"}}}
```

Reload with `tmux source-file ~/.tmux.conf`, then run `/hooks` once in any
**already-running** Claude Code session so it picks up the new config — freshly
started sessions get it automatically.

which renders like this: the session name and the four counts on the left (a zero turns grey but
keeps its place), then the sidebar button and the window list, machine resources on the right:

<p align="center">
  <img src="docs/img/en/statusbar.png" alt="Status bar: session name, the counts ⏸ 1 ✔ 2 ▶ 1 ✓ 2, the sidebar button, the window list with a state per window, CPU / memory / swap / disk on the right" width="960">
</p>

That segment is an aggregate — it tells you *how many* are waiting, not *which
window*. For the per-window answer, put `#{@claude_win}` in your window list:

```tmux
set -g window-status-format         '#I#{E:@claude_win} #W'
set -g window-status-current-format '#I#{E:@claude_win} #W'
```

Every window that holds a Claude pane then carries its own state right in the
window list — `⏸` waiting on you, `✔` finished and unread, `◑` still running,
`✓` finished and already seen, dim `✔` unread but hours old — with a count
appended when one window holds several (agent teams):

```
1 ✓ journal    3 ✔ web-app         6 ◑ data-eval    2 ⏸2▶ deploy
```

Three things it does on purpose:

- **RUN moves.** A lone running pane shows Claude's own spinner glyph, taken
  live from that pane's terminal title, so "still working" reads without being
  read. It falls back to a static `▶` when the pane isn't drawing one, and stays
  static when a window holds several running panes — four spinners in a window
  list is a light show, not information.
- **Only what wants you is coloured.** RUN is muted gold (you can't help it),
  WAIT and DONE keep full brightness (you can).
- **Seen windows fade.** When nothing in a window is unread, the badge *and the
  window name* go dim — except for the window you're in, which stays legible.

For that fade to mean anything, switching to a window has to count as reading
it. Add the hook:

```tmux
set-hook -g pane-focus-in 'run-shell -b "python3 ~/.claude/hooks/tmux_status_update.py mark-seen #{pane_id}"'
```

Without it, `read` is only set when you arrive through the picker or
`prefix + a`, so a window you switched to and read stays a bright unread `✔`.
`mark-seen` is deliberately gentler than `mark-read`: it leaves `blocked` panes
alone, so cycling past a window can't silently dismiss a WAIT alert.

`#{E:...}` (expand twice) is required, not decoration: the RUN badge is itself a
tiny format. Plain `#{@claude_win}` prints its source text. Nothing runs at
render time — the hooks write the badge into a window-scoped user option, so the
bar flips the instant a state changes.

With [gpakosz/.tmux](https://github.com/gpakosz/.tmux), put it in
`tmux_conf_theme_window_status_format` and
`tmux_conf_theme_window_status_current_format` in `~/.tmux.conf.local` instead.

**If your window names follow Claude's pane title** (a common `automatic-rename-format`
trick), they already start with Claude's own status character — `✳` idle, `◑◐`
spinning. That character only separates "running" from "not running", which is
the part the badge already says better, so two icons end up saying one thing.
Strip it in the format and keep the badge:

```tmux
set -g window-status-format '#I#{E:@claude_win} #{s|^[^ -~] ||:window_name}'
```

The substitution drops *one* leading non-ASCII character plus its space — tmux's
`s///` matches characters, not bytes, so CJK window names survive intact. Don't
reach for a `{1,4}`-style quantifier: the `}` closes the `#{...}` and the whole
format silently falls apart.

The name column has no home-grown source any more: a session you `/rename`d is
already written into the terminal title by Claude Code, so the picker just reads
`pane_title`; a session you didn't rename has Claude Code's own title there.
(2026-09-28 removed `bin/session_label.py`, which asked a small model to invent a
name, and `bin/claude_sessions.py`, which parsed the `/rename` name — the first
was never reached, the second duplicated the terminal title.)

```tmux
set -g window-status-format '#I#{E:@claude_win} #{?#{@claude_label},#{@claude_label},#{s|^[^ -~] ||:window_name}}'
```

A name you chose always wins; set `CLAUDE_TMUX_LABEL_MODEL` to use another model.

### Check it's working

In any Claude Code pane inside tmux, send a prompt, then:

```bash
python3 -c 'import json;print(json.dumps(json.load(open("'"$HOME"'/.claude/tmux-claude-status.json")),indent=2,ensure_ascii=False))'
```

You should see an entry for that pane with `"status": "running"` (or `"done"`
once it finishes). Nothing there? The hooks aren't firing — run `/hooks` in that
session, and check that `~/.claude/settings.json` lists
`tmux_status_update.py`.

### Uninstall

```bash
launchctl bootout gui/$(id -u)/com.claude-tmux.sidebar; rm -f ~/Library/LaunchAgents/com.claude-tmux.sidebar.plist
rm ~/.claude/hooks/{tmux_status_update.py,claude-tmux-picker.sh,jump-top.sh,status-badge.sh,restore-claude.sh,resurrect_hooks.py,float-picker.sh,usage-refresh.py,status-click.sh,float-tool.sh,tmux-sysstat.sh,sys-monitor.py,ctx_to_tmux.py}
rm -f ~/.claude/tmux-claude-status.json ~/.claude/tmux-claude-restore.json ~/.claude/tmux-quota-cache.json
rm -rf ~/.local/state/claude-trail; rm -f ~/.config/claude-tmux/lang
```

Then remove the `tmux_status_update.py` and `ctx_to_tmux.py` hook entries from
`~/.claude/settings.json`, and the `source-file …/claude-tmux.conf` line and status-bar
segments from your tmux config.

## Using the picker

<p align="center">
  <img src="docs/img/en/picker.png" alt="The picker: five sessions and twenty-odd panes, folded down to the ones that want you, one of them blocked on a permission prompt, with a live preview of the highlighted pane" width="960">
</p>

- `j` / `k` (or ↑ / ↓) move between panes — session headers are skipped, so every
  stop is a real Claude pane and the preview follows it.
- **A row number** jumps straight there. `/` switches to search-by-name instead
  (`Esc` returns to navigation).
- `h` / `←` enters session mode; `l` / `→` leaves it.
- `a` toggles between "only what needs me" (the default) and every tracked pane.
  Set `CLAUDE_TMUX_SHOW_ALL=1` if you'd rather always start expanded. The pane
  you're currently in is never collapsed, even when it's a quiet one.
- `p` collapses the preview so the list gets the full width — worth it once
  you're tracking a dozen-plus panes and want to scan names and paths. Press it
  again to bring the preview back, or set `CLAUDE_TMUX_PREVIEW_WIDTH` (default
  `50` — an even split, so the preview is about as wide as the pane it's
  showing) to change it. `CLAUDE_TMUX_PREVIEW_WIDTH=0` starts with it hidden
  (`p` still opens it); `CLAUDE_TMUX_USAGE_FOOTER=0` drops the usage footer.
- **Mouse.** A single click on a row jumps there (no double-click); a click on
  the `⋯ 收起` row unfolds it. Every word in the key-hint header is a button —
  `全部` is `a`, `预览` is `p`, `总览` is `o`, `✕ 关闭` closes.

- `o` opens the overview: one screen for "I'm back — what's the situation".
  **The first line is the answer** — `5 个有结果等你看`, or `没人等你 · 2 个还在跑`.
  Below it, **one card per tmux session**, most urgent session first, each
  titled with what it holds (`✔ 3  ▶ 1  +6 安静`) and the team running in it.
  The 5h quota, the 7-day window, today's tokens and the 14-day sparkline sit
  pinned at the bottom while you walk the cards. `j`/`k` move, `Enter` jumps
  (a pane row to its pane, a card title to where you last were in that
  session), `r` refreshes, `q` returns. The preview is **state, not screen** —
  the list already shows you screens: where this pane is, how long it's been
  there, how full its context is, which team owns it, what it's read in today.
  Read-only: looking at it marks nothing read.
- `t` opens the token page: an overview of the window (turns, and the four token
  classes as share bars) above a **per-session ranking you can walk**. It sorts
  by **tokens read in** and highlights **mean context per turn**, because cost is
  roughly turns × the context each turn carried and ~98% of the tokens are
  re-reads of existing context — so turn count alone misjudges which session is
  expensive. Sessions are listed by name, not by id — whatever you or your team
  called them, falling back to the session's opening question for ones that never
  got a name. `j`/`k` move and the preview shows that session in full: what it
  was asked to do, where its tokens went, a per-day sparkline, and its live
  screen if it's still open (the tail of its last reply if it isn't). `Enter`
  takes you to that session's pane; on one that has already closed it says so
  and stays put. `1` / `7` switch between today and the last 7 days, `p`
  collapses the preview, `r` recounts, `q` returns to the list.
- `l` on a team lead's row unfolds its teammates so `j`/`k` walk them; `h` on
  one of them folds the team back up. `h`/`l` are already one level out and one
  level in — session mode is the level above panes — and on these two kinds of
  row they just mean one level further. On every other row they do exactly what
  they always did. See below.
- `f` narrows the list to your Agent Teams and the panes on them — only live
  when you actually have a team. `l` is the small version of it: `f` answers
  *who is on which team*, `l` answers *who is on this one*. See below.
- `Enter` jumps · `ctrl-x` archives the highlighted pane · `q` / `Esc` closes.

### Mouse-only: click the status bar

With tmux 3.7+ the picker opens as a **floating pane** anchored to the bottom of the window,
exactly as tall as its rows, and closed by clicking anywhere else (`bin/float-picker.sh`). A
`display-popup` can't do that last part — tmux drops clicks outside a popup — but a floating pane
is a real pane, so focus leaving it closes it. Every segment of the status bar is a button (the
bindings are in `tmux/claude-tmux.conf`):

| Click | What happens | Behind it |
|---|---|---|
| Session name | The picker in session view (cursor on the session, the preview is a card per pane) | `float-picker.sh` |
| `⏸ 1` | Jumps straight to the Claude that has waited longest | `status-click.sh` → `jump-top.sh` |
| `✔ 2` / `▶ 1` / `✓ 2` | The picker with just those panes (never folded); if there's exactly one, it jumps there instead | `status-click.sh` → `float-picker.sh` |
| Sidebar button | Toggles the sidebar of the current Claude | `float-tool.sh` |
| CPU / memory / swap / disk | A small pop-up with that item in detail: what uses CPU and memory, which folders take the most disk | `float-tool.sh` → `sys-monitor.py` |
| `5h` / `7d` (if you added the quota segment) | Refreshes the real 5h / 7d usage now, then a card with the numbers and time to reset | `status-click.sh` → `usage-refresh.py` |
| Window name | Switches to that window | tmux itself |

Clicking the same thing again closes the float; clicking another swaps it. Here is a click on `✔ 2`:

<p align="center">
  <img src="docs/img/en/click-done.png" alt="After clicking ✔: a picker with only the done-unread panes floats up from the bottom, with a live view of the highlighted one" width="960">
</p>

Clickable words in the picker's header are drawn as grey chips; expanding everything inside a float
reopens it at the new height (tmux only grows a floating pane downward).

The quota segment (`status-badge.sh quota`): 5h and 7d are two cells each, drawn the same way so their heights can be
compared. The first is usage, in yellow: it grows up from the bottom (▁ → █). The second is how much of the window has elapsed, in blue, growing up
from the bottom too — ▁ just after it opened, █ about to reset. Both sit on a
grey track, so the grey left above a bar is how far it is from 100%. Usage taller
than time means you're burning faster than the window refills. `status-badge.sh states` / `status-badge.sh quota`
print the two halves separately, so the Claude states can sit next to the
session name on the left and the quota at the far right. Clicking the quota
opens a card at once with the last reading, its time and a ⟳ refreshing mark;
when the request comes back (~2 s) the card switches to the new numbers,
unless you've closed it by then. Clicking it
more than once in 20 s shows the card again without a new request. `bin/usage-refresh.py`
asks the same endpoint `/usage` does, using the OAuth token Claude Code keeps
in the Keychain — read in memory for that one request, never written, never
refreshed — every 30 min (`CLAUDE_TMUX_USAGE_REFRESH_MIN`) or on a click. The
endpoint isn't a documented public API; if it changes, the bar falls back to
Claude Code's own cache.

### Agent Teams

If you run Claude Code teams, the picker names each teammate's pane after the
teammate — its real name instead of the shared window title — and summarises
the team
on the header of the session it's running in — no separate block, since a team
and the session it was spawned in are the same thing. Selecting that header
gives you a two-part preview: the full roster and shared task list on top —
including the members that have no pane to jump to — then the session's own
panes below.

Teammate rows are indented under their lead and are, by default, shown rather
than selected: `j`/`k` step over them and they have no jump number, so a team
costs one stop in the list instead of one per member. The lead is the useful
destination anyway — its teammates are in the same tmux window, a native
pane-switch away.

When you do want to move among them, press `l` on the lead: that one team's
members become stops, `j`/`k` walk them, and `h` folds it back. It is an
excursion rather than a mode to remember you are in — walking out of the team
folds it again, and so does rebuilding the list (`a` / `f` / `ctrl-x`) or
jumping away by number. Use `f` to see every team at once.

The name is printed in the colour Claude Code assigned that teammate, so
saying "this pane is on a team" costs the list no columns at all — the name
column is the one that runs out of room, and a word in front of every member
row was spending a fifth of it to repeat what the session header says once.
Colour is never the only thing carrying it: a teammate row also has no number
in its gutter, `j`/`k` steps over it, and its preview names the team in words.

The team rows cost nothing if you don't use teams: with no `~/.claude/teams/`
directory the picker does a single `stat`, adds no row and annotates none.
`f` is inert and isn't advertised in the header.

One related change does apply to everybody: rows are now named from the pane's
own title rather than its tmux window's. For a pane that has a window to
itself those are the same string and nothing looks different. For several
Claude panes sharing one window they aren't — the window title is all of their
titles concatenated — so those rows now show each pane's own name instead of
the same jumble repeated.

A session you renamed yourself with `/rename` is shown by that name (Claude Code
puts it in the terminal title, which is what the picker reads), ahead of
every automatic source. Only names you chose count — Claude Code also
generates one for every session, and a generated name is just the directory,
which the row already tells you at the other end. Nothing changes if you have
never renamed anything.

Point `CLAUDE_TMUX_TEAM_LABELS` at a JSON file (`{"member-name": "word"}`) to
choose the word used for each member's role in the previews. The picker prints
whatever you put there and doesn't interpret it.

## The sidebar

A strip to the right of each Claude, opened automatically when you switch to a window that's
wide enough (≥120 columns); the status-bar sidebar icon toggles it. A small shell on top (for a
quick `git` without leaving the window), the sidebar below:

- **A row of buttons**: scroll to bottom, git (with the uncommitted-file count), files, refresh
  quota (with how fresh it is), ⇄ switch account (with the account you're on), close. The git /
  file pop-ups float on the right and close when you click the Claude on the left.
- **Quota**: context, 5h, 7d bars — grey until something is about to go wrong.
- **One item per turn**: what you said + how Claude's reply starts. Click it and the main pane
  scrolls to that turn's final reply; turns before a `/compact` fold into one row at the top.

It only reads the transcripts Claude Code already writes under `~/.claude/projects/`, and only
sends scroll keys to the main pane — never touches the input box. One daemon serves every sidebar
and only reads newly appended lines. Details (in Chinese) in [`sidebar/README.md`](sidebar/README.md).

<p align="center">
  <img src="docs/img/en/sidebar.png" alt="Sidebar: buttons and three quota bars in the header, one item per turn below, the latest highlighted" width="560">
</p>

## git and file pop-ups

The git and files buttons on the sidebar open a tool in this Claude's directory (`bin/float-tool.sh`),
**floating on the right at 60 % of the width**; the Claude on the left keeps its size and doesn't
re-wrap. Click outside to close it, click the same button again to close it, click the other one to
swap. The width lives in `~/.local/state/float-tool-width` (30–95).

- **git is [lazygit](https://github.com/jesseduffield/lazygit)** (`brew install lazygit`): see which
  files Claude changed, read the diff, commit — all with the mouse. If the directory isn't a repository
  (say `~/work`, with many repositories under it), it first lists the repositories up to three levels
  down, most recently touched first; click one.
- **Files is [yazi](https://github.com/sxyazi/yazi)** (`brew install yazi`): click to select, scroll to
  page, a live preview on the right. The pop-up is for looking, not editing — clicking outside closes
  it, and an editor inside would lose unsaved work. So "open" on a text file goes to
  `float-tool.sh --edit`, which runs `$EDITOR` in a new tmux window in the same session and closes the
  pop-up. For that, add to `~/.config/yazi/yazi.toml`:

  ```toml
  [opener]
  edit = [
    { run = '~/.claude/hooks/float-tool.sh --edit %s', desc = "Editor (new tmux window)", orphan = true, for = "unix" },
  ]
  ```

<p align="center">
  <img src="docs/img/en/git.png" alt="git pop-up: lazygit on the right with changed files and a diff, Claude unchanged on the left" width="960">
</p>

<p align="center">
  <img src="docs/img/en/files.png" alt="Files pop-up: yazi on the right with parent folder, current folder and preview" width="960">
</p>

## Account switching

A single account has 5h / 7d usage caps. Click ⇄ on the sidebar and the session exits and
`claude --resume`s the same session id under the other account, so the conversation carries on
(the accounts' `projects/` directories need to point at the same place, e.g. a symlink). It
refuses when:

- the session is working or waiting on a confirmation;
- there's an unsent draft in the input box (exiting would lose it);
- the window has agent-team teammates — then it first types a wind-down request to the lead, and
  switches once the teammates have exited.

The button shows `…` while switching and `✗` if it was refused, with the reason in the tmux
message line. CLI: `bin/switch_account.py --to <account> --pane %3 [--dry-run]`.

## Why not just…

- **…name my tmux windows?** Claude Code already drives your window titles with a
  spinner, so you can see *that* something's running — but not whether it's
  blocked on you, and not without looking at every window. `⏸` is the
  part window names can't do.
- **…use Claude Code's own session list?** It's per-instance and shows sessions,
  not *where they live in tmux*. This is the other direction: pane-first, so
  "jump to it" is one keypress.
- **…just watch for the notification?** macOS notifications are transient and
  easy to miss when you're in another app or another Space. That's precisely why
  `⏸` here stays on the status bar and only clears when you go deal with it.

## How it works

Claude Code hooks write one file, `~/.claude/tmux-claude-status.json`, keyed by
tmux pane id:

| Hook | Writes |
|------|--------|
| `UserPromptSubmit` | `running` |
| `Stop` | `done` |
| `Notification` | `blocked` (a `permission_prompt`) or `input` (idle) — the hook reads the notification type to tell them apart |
| `PostToolUse` | clears `blocked` → `running`. A tool ran, so the prompt got answered |
| `SessionEnd` | deletes the entry |

Every reader first *prunes* that file against `tmux list-panes -a`, so closed
panes — and panes that have fallen back to a plain shell because Claude exited —
disappear on their own. The picker (`bin/list-rows.sh` → `fzf`) and the status
badge (`bin/status-badge.sh`) both read it. In the UI, `input` (idle) collapses
into the `DONE`/`READ` pair, and a finished pane you never return to ages out
after `CLAUDE_TMUX_IDLE_STALE_SECS` (default 2h): it leaves the status bar and
sinks, dimmed, to the bottom of the picker.

**Full design** — the notification cascade, the read/archive model, the two
cursor modes and digit accumulator, the live preview and usage footer, glyph
widths and CJK alignment, and the tmux-resurrect integration — is in
**[DESIGN.md](DESIGN.md)**.

## License

MIT
