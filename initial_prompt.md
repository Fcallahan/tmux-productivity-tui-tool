# tmux-productivity — Claude Code Implementation Spec

## Goal

Build a terminal productivity tracker called `tmux-productivity`. It hooks into the user's zsh shell and tmux session to silently log every command and structural event (new windows, sessions) to an append-only TSV log file. A Textual-based TUI reads that log and displays a scored productivity dashboard.

---

## Project Layout

Create the following file structure at `~/.tmux-productivity/`:

```
~/.tmux-productivity/
├── tui.py            # Textual TUI — main entry point
├── log_event.sh      # Called by tmux hooks to log window/session events
└── setup.sh          # One-shot installer
```

Also create a convenience symlink: `~/.local/bin/tmux-prod → ~/.tmux-productivity/tui.py`

---

## File 1: `log_event.sh`

Bash script called by tmux `set-hook` rules. Appends one TSV line per event.

```bash
#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="$HOME/.tmux-productivity"
LOG_FILE="$LOG_DIR/events.log"
mkdir -p "$LOG_DIR"

TS=$(date +%s)
EVENT="${1:-unknown}"
WIN=$(tmux display-message -p '#S:#W' 2>/dev/null || echo "unknown")

printf '%s\tevent\t%s\t%s\n' "$TS" "$EVENT" "$WIN" >> "$LOG_FILE"
```

---

## File 2: `tui.py`

Single-file Python TUI using the `textual` library. No external dependencies beyond `textual`.

### Log format

Each line in `~/.tmux-productivity/events.log` is tab-separated:

```
<unix_timestamp>\t<kind>\t<data>\t<window>
```

- `kind` is either `cmd` (shell command) or `event` (tmux structural event)
- For `cmd`: `data` is the raw command string, `window` is `SESSION:WINDOW_NAME`
- For `event`: `data` is `new-window` or `new-session`

### Data model: `DayMetrics` dataclass

Fields:
- `windows_opened: int`
- `sessions_opened: int`
- `nvim_opens: int`
- `nvim_files: set` — unique file paths opened in nvim
- `lazygit_opens: int`
- `claude_opens: int`
- `git_commits: int`
- `git_pushes: int`
- `build_cmds: int`
- `test_cmds: int`
- `docker_cmds: int`
- `ssh_cmds: int`
- `total_commands: int`
- `commands_by_hour: Dict[int, int]` — defaultdict, keyed by 0–23
- `timeline: List[Tuple[int, str, str]]` — list of `(timestamp, label, detail)`

Properties:
- `active_hours` → count of hours where `commands_by_hour[h] >= 5`
- `score()` → int, sum of weighted metrics (see scoring table below)
- `grade()` → str, one of `S A B C D F`

### Scoring weights

```python
WEIGHTS = {
    "new_window":  5,
    "new_session": 3,
    "nvim_open":   10,
    "nvim_file":   3,
    "lazygit":     15,
    "claude":      12,
    "git_commit":  25,
    "git_push":    15,
    "build_cmd":   10,
    "test_cmd":    12,
    "docker_cmd":  8,
    "ssh_cmd":     5,
    "active_hour": 8,
}

GRADE_THRESHOLDS = [(500, "S"), (350, "A"), (200, "B"), (100, "C"), (50, "D")]
MAX_SCORE = 600  # used for progress bar percentage
```

### Log parsing: `parse_log(date: datetime.date) -> DayMetrics`

- Filter log lines to only those within `[start_of_day, end_of_day]` unix timestamps for `date`
- For `event` lines: increment `windows_opened` or `sessions_opened`, append to timeline
- For `cmd` lines: increment `total_commands`, increment `commands_by_hour[hour]`, then classify:

Command classification (use `re.match` on the stripped command string):

| Pattern | Action |
|---|---|
| `^n?vi?m?\b` | nvim: `nvim_opens += 1`, extract non-flag args into `nvim_files`, append timeline |
| `^lazygit\b` | `lazygit_opens += 1`, append timeline |
| `^claude\b` | `claude_opens += 1`, append timeline |
| `^git\s+commit` | `git_commits += 1`, append timeline |
| `^git\s+push` | `git_pushes += 1`, append timeline |
| `^(dotnet test\|cargo test\|npm test\|pytest\b\|go test\|jest\b)` | `test_cmds += 1`, append timeline |
| `^(dotnet (build\|run\|publish)\|cargo (build\|run\|check)\|npm (run\|build)\|make\b\|go (build\|run)\|gradle\b)` | `build_cmds += 1`, append timeline |
| `^docker\b` | `docker_cmds += 1` (no timeline) |
| `^ssh\b` | `ssh_cmds += 1` (no timeline) |

Timeline label format: use emoji prefix — `"📝 nvim"`, `"🔀 lazygit"`, `"🤖 claude"`, `"✅ commit"`, `"⬆  push"`, `"🔨 build"`, `"🧪 test"`, `"🖥  window"`, `"🚀 session"`

Sort `timeline` by timestamp ascending before returning.

### TUI layout (Textual)

```
┌ Header (show_clock=True) ──────────────────────────────────────────┐
│ title: "tmux-productivity"   sub_title: "<date>  •  auto-refresh"  │
├────────────────────────────────────────────────────────────────────┤
│ Horizontal #top-row (height: 16)                                   │
│  ┌─ #score-panel (width: 26) ─┐  ┌─ #metrics-panel (1fr) ────────┐│
│  │  big score number           │  │  Rich Table: activity/count/pts││
│  │  Grade letter (colored)     │  │                                ││
│  │  progress bar (█░)          │  │                                ││
│  │  cmds / hours / files       │  │                                ││
│  └─────────────────────────────┘  └────────────────────────────────┘│
├────────────────────────────────────────────────────────────────────┤
│ #heatmap-panel (height: 5)                                         │
│   06 07 08 09 10 11 ...                                            │
│    ░  ░  ▓  █  █  ▒  ...                                           │
├────────────────────────────────────────────────────────────────────┤
│ #timeline-panel (1fr, ScrollableContainer, overflow-y: scroll)     │
│   HH:MM:SS  📝 nvim          src/Foo.cs                           │
│   HH:MM:SS  🔀 lazygit                                            │
│   (newest first)                                                   │
├────────────────────────────────────────────────────────────────────┤
│ Footer (shows key bindings)                                        │
└────────────────────────────────────────────────────────────────────┘
```

### Textual CSS (embed as `CSS` class attribute)

```css
Screen { background: #0d0d0d; }
Header { background: #1a1a2e; }
#top-row { height: 16; margin: 0 1; }
#score-panel { width: 26; border: round #2a2a3e; padding: 0 1; background: #111118; }
#metrics-panel { width: 1fr; border: round #2a2a3e; padding: 0 1; background: #111118; margin-left: 1; }
#heatmap-panel { height: 5; border: round #2a2a3e; padding: 1 1; background: #111118; margin: 1 1 0 1; }
#timeline-panel { height: 1fr; border: round #2a2a3e; background: #0d0d0d; margin: 1; overflow-y: scroll; }
#tl-content { padding: 1 0; }
```

### Key bindings

```python
BINDINGS = [
    Binding("q",     "quit",     "Quit"),
    Binding("r",     "refresh",  "Refresh"),
    Binding("left",  "prev_day", "Prev Day"),
    Binding("right", "next_day", "Next Day"),
    Binding("h",     "prev_day", show=False),
    Binding("l",     "next_day", show=False),
]
```

### App behavior

- `_date: datetime.date` — starts as `datetime.date.today()`, mutated by prev/next actions
- `on_mount`: call `_load()`, then `self.set_interval(30, self._load)`
- `_load()`: parse log for `_date`, call `.update()` on each Static panel with the appropriate Rich renderable, call `.refresh_timeline(m)` on the TimelinePanel
- `action_next_day`: only advance if `_date < datetime.date.today()`

### Render functions (standalone, not methods)

**`render_score(m) -> rich.text.Text`**
- Center-ish layout with score number, grade (colored by grade), progress bar `█░` scaled to `MAX_SCORE`, then cmds/hours/files counts
- Grade colors: `S=gold1, A=green, B=cyan, C=yellow, D=orange1, F=red`

**`render_metrics(m) -> rich.table.Table`**
- `box=SIMPLE`, 3 columns: Activity | Count | Points
- One row per tracked metric; show `—` for zero counts, `+N` for points
- Rows: Terminals, Nvim opens, Unique files, Lazygit, Claude, Commits, Pushes, Builds, Tests, Docker, SSH, Active hours

**`render_heatmap(m) -> rich.text.Text`**
- Hours 6–22, two rows: hour labels (`06 07 08...`) then block chars
- Block scale: `" ░▒▓█"` indexed by `int(count/peak * 4)` clamped to 0–4
- Colors per level: `dim, dim cyan, cyan, bold cyan, bold white`

**`render_timeline(m) -> rich.text.Text`**
- Reversed timeline (newest first)
- Format per line: `  HH:MM:SS  <emoji label padded to 14>  <detail truncated>`
- Return `"  No events recorded yet."` (dim italic) if empty

**`TimelinePanel(ScrollableContainer)`**
- Composes a single `Static(id="tl-content")` child
- `refresh_timeline(m)` method: updates the inner Static, then calls `self.scroll_home(animate=False)`

---

## File 3: `setup.sh`

Bash installer. Does the following in order:

1. `mkdir -p ~/.tmux-productivity`
2. Copy `tui.py` and `log_event.sh` from the script's own directory into `~/.tmux-productivity/`
3. `chmod +x` both scripts
4. Symlink `~/.local/bin/tmux-prod → ~/.tmux-productivity/tui.py`
5. Run `pip install --quiet textual --break-system-packages` (fallback: `--user`)
6. **Append zsh hook to `~/.zshrc`** (guard with grep to avoid duplicates):

```bash
# ── tmux-productivity shell hook ─────────────────────────────────────────────
_tmuxprod_preexec() {
    [ -z "${TMUX:-}" ] && return
    local log="$HOME/.tmux-productivity/events.log"
    local win
    win=$(tmux display-message -p '#S:#W' 2>/dev/null) || win="unknown"
    printf '%s\tcmd\t%s\t%s\n' "$(date +%s)" "$1" "$win" >> "$log"
}
autoload -Uz add-zsh-hook 2>/dev/null && add-zsh-hook preexec _tmuxprod_preexec
# ─────────────────────────────────────────────────────────────────────────────
```

7. **Append tmux hooks to `~/.tmux.conf`** (guard with grep):

```
# tmux-productivity hooks
set-hook -g after-new-window  "run-shell '~/.tmux-productivity/log_event.sh new-window'"
set-hook -g after-new-session "run-shell '~/.tmux-productivity/log_event.sh new-session'"
```

8. If `$TMUX` is set (running inside tmux), apply the two `set-hook` commands to the live session immediately via `tmux set-hook -g ...`
9. Print install summary with scoring table and grade thresholds

---

## Validation checklist

After generating the files, verify:

- [ ] `python3 -c "import textual"` exits 0
- [ ] `python3 tui.py` launches without import errors (can use `--print-only` flag or just import check)
- [ ] `parse_log()` on an empty/nonexistent log returns a zero `DayMetrics` with `score() == 0`
- [ ] `setup.sh` is idempotent — running twice does not duplicate the zshrc or tmux.conf entries
- [ ] `log_event.sh new-window` appends a valid TSV line to `events.log`

---

## Notes

- Do not use `reactive` fields on widget classes — use `Static.update()` called from the app's `_load()` method instead. It's simpler and avoids Textual version compatibility issues.
- The log file is append-only and never rotated. If it grows large, parsing stays fast because Python file I/O with line iteration is O(n) and the bottleneck is always the filter step, not allocation.
- `preexec` in zsh fires only for interactive user-typed commands, not subshells or background jobs — no filtering needed.
- Do not add bash `DEBUG` trap support. It fires on subshells and causes noise.
- Guard every `tmux display-message` call with `2>/dev/null || echo "unknown"` — the hook may fire in edge cases where tmux context isn't available.:wq
