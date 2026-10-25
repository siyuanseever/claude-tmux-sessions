---
title: I run a dozen Claude Code sessions at once. This tmux console tells me which one needs me
published: false
tags: claude, tmux, productivity, opensource
cover_image: https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/screen.png
---

<!-- Draft for an English platform (front matter is dev.to's format). Images point at the repository's main branch,
     so they render wherever this is pasted. Not published yet: see GUIDE.zh-CN.md. -->

![15 seconds: status bar → picker → jump to the session waiting on you → switch account from the sidebar](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/demo.gif)

**TL;DR** — [claude-tmux-sessions](https://github.com/threedaycat/claude-tmux-sessions) is a small set of tmux tools
for running many Claude Code sessions in parallel. It answers three questions I kept asking myself: *which session is
waiting on me, what is everyone doing, and what do I do when one account runs out of quota?* MIT, tmux + bash +
python3, driven by Claude Code hooks.

## How I use Claude Code

Some context first, or the design will look odd.

I use two Claude accounts to push several projects forward at the same time:

- **One account per project.** Each account has a 5-hour and a 7-day usage window. If one busy project shares an
  account with the others, it stalls them too. With an account per project, each has its own quota.
- **One session per role inside an account.** Algorithms, backend, frontend, testing: one session each, so each
  context holds only its own part of the work and doesn't drift.
- **I only make decisions.** I don't watch any one terminal. I go over when a session needs a yes/no, or when it has
  finished and I should look at the result.
- **Memory lives in files.** Project rules, hand-off notes and a dev log are in the repository, so switching accounts
  or sessions loses nothing.

That adds up to a lot of sessions open at once, more than one person can watch. tmux only manages windows; it has no
idea what the Claude inside each window is doing. Hence this.

## Four pieces

![The whole screen: Claude on the left, its sidebar on the right, the status bar at the bottom](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/screen.png)

### Status bar: who is waiting on you

![Status bar: four counts on the left, the window list with a state per window, CPU/memory/swap/disk on the right](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/statusbar.png)

Each session reports its state through Claude Code hooks, and the status bar counts them:

- `⏸` waiting for your confirmation (a permission prompt is open and Claude is idle) — the only state that rings and
  sends a notification;
- `✔` finished, and you haven't looked yet;
- `▶` running, nothing to do;
- `✓` finished, and you've seen it.

The four counts never move: a zero turns grey but keeps its place, so you can click from muscle memory. Click `⏸` to
jump to the session that has waited longest; click `✔` to get a list of just the finished-but-unread ones:

![Clicking ✔: a picker with only the done-unread sessions floats up from the bottom](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/click-done.png)

That list is a tmux 3.7 *floating pane*, not `display-popup`, which is why clicking anywhere outside closes it —
`display-popup` swallows clicks outside its box.

### Picker: what is everyone doing

![Picker: grouped by project, numbered rows, live preview of the highlighted session](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/picker.png)

`prefix + g` lists every session, grouped by tmux session (for me, one per project), with a live view of the one
under the cursor. Every row is numbered; type the number to jump. It behaves like an inbox: visiting a session marks
it read, and quiet ones fold away by default, so the list shows only what needs you.

### Sidebar: everything about this one session

Every Claude gets a sidebar on its right that is about *this* session only:

- **Every turn.** What you said and the start of the reply. Click a turn and the main pane scrolls to that turn's
  final answer. Turns before a `/compact` fold into one line. It reads the transcript file, not the screen.
- **Quota.** Context, 5-hour and 7-day bars. Grey until something runs well ahead of an even pace.
- **Buttons.** git (with the count of uncommitted files), files, refresh quota, switch account, close.

The git and files buttons open [lazygit](https://github.com/jesseduffield/lazygit) and
[yazi](https://github.com/sxyazi/yazi) floating over the right 60 % of the window, so the Claude on the left
doesn't re-wrap; click outside and they're gone:

![lazygit floating on the right, showing the diff of what Claude just changed](https://raw.githubusercontent.com/threedaycat/claude-tmux-sessions/main/docs/img/en/git.png)

Rule of thumb: global things go on the status bar, things about the current session go on the sidebar. The status
bar is short on space, and this keeps both uncluttered.

### Switch accounts: keep going when one runs out

Click ⇄ on the sidebar and the session exits, then comes back as `claude --resume` of the same session id under the
other account. Same conversation, same context. There are guard rails: a session that is working is left alone; one
with unsent text in its prompt is left alone (exiting would lose it); a window with agent-team teammates first gets a
line typed to the lead asking it to wind the team down, and switches once they've exited.

## Trade-offs I made on purpose

- **Hooks, not screen scraping, for state.** Sessions report their own state when it changes: accurate, no polling,
  no CPU. The screen is read only where there is no other way ("which turn is where", "is there unsent text").
- **An inbox, not a dashboard.** Things you've seen turn `✓` on their own and come back when that session does
  something new. Nobody maintains the list.
- **Everything is clickable.** I work mostly by voice and trackpad, so every segment and button takes a click.
  Keyboard shortcuts are there too.
- **When in doubt, don't switch accounts.** It skips and tells you why rather than risk losing anything.

## The rest of my setup

The tmux console is one of four pieces. I also dictate almost every prompt with a push-to-talk voice tool (an LLM
proofreads each transcript, and Claude adds the words it sees misheard to the vocabulary itself), and look back at
usage and configuration in a local web dashboard. That's written up in
[docs/workbench.md](https://github.com/threedaycat/claude-tmux-sessions/blob/main/docs/workbench.md).

## Try it

```bash
git clone https://github.com/threedaycat/claude-tmux-sessions.git ~/projects/claude-tmux-sessions
~/projects/claude-tmux-sessions/install.sh --lang en
```

Then add one line to your tmux config, `source-file ~/projects/claude-tmux-sessions/tmux/claude-tmux.conf`, and put
the status-bar segments into your own status line; the README walks through it.

Known limits, up front: I use it daily on macOS only (Linux is untested); you need a Nerd Font for the icons; and a
few features read Claude Code's screen, so a Claude Code UI change may need a follow-up fix. Issues and PRs welcome.
