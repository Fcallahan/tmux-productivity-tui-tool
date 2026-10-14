# tmux-productivity

A terminal productivity tracker that hooks into your zsh shell and tmux session. It silently logs every command and structural event (new windows, sessions) to an append-only TSV log, then displays a scored productivity dashboard via a beautiful Textual TUI.

## Screenshots

```
+-- tmux-productivity ── 2026-04-11  .  auto-refresh --------+
|  +-----------+  +--------------------------------------+   |
|  |   385     |  | Activity      | Count | Points       |   |
|  |  Grade: A |  | Terminals     |   4   |  +20         |   |
|  |           |  | Nvim opens    |   8   |  +80         |   |
|  | ████████░ |  | Lazygit       |   3   |  +45         |   |
|  |  64% /600 |  | Claude        |   2   |  +24         |   |
|  |           |  | Commits       |   4   | +100         |   |
|  | 87 cmds   |  | Builds        |   3   |  +30         |   |
|  | 6 active  |  | Tests         |   2   |  +24         |   |
|  | 12 files  |  | Active hours  |   6   |  +48         |   |
|  +-----------+  +--------------------------------------+   |
|  +--- Hourly Heatmap ------------------------------------+ |
|  | 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22   | |
|  |  .  . ## ## ## .. ##  . ## ## ## ..  .  .  .  .  .    | |
|  +-------------------------------------------------------+ |
|  +--- Timeline ------------------------------------------+ |
|  | 16:42:05  commit        fix auth middleware            | |
|  | 16:38:12  nvim          src/Auth.cs                    | |
|  | 16:30:01  lazygit                                      | |
|  +-------------------------------------------------------+ |
+------------------------------------------------------------+
```

## Features

- **Automatic command logging** via zsh `preexec` hook -- every command you type in tmux is recorded
- **Tmux event tracking** -- logs new windows and sessions via tmux hooks
- **Productivity scoring** -- weighted scoring system that rewards meaningful dev activity
- **Letter grades** -- S/A/B/C/D/F grading based on your daily score
- **Hourly heatmap** -- visualize your active hours at a glance
- **Activity timeline** -- scrollable, reverse-chronological feed of your day
- **Day navigation** -- browse previous days with arrow keys or h/l
- **Auto-refresh** -- dashboard updates every 30 seconds

## Requirements

- Python 3.9+
- [Textual](https://github.com/Textualize/textual)
- tmux
- zsh

## Quick Start

```bash
git clone <this-repo> && cd tmux-productivity
pip install -r requirements.txt
./setup.sh
source ~/.zshrc
tmux-prod
```

## Installation

Install dependencies and run the installer:

```bash
pip install -r requirements.txt
./setup.sh
```

This will:

1. Copy files to `~/.tmux-productivity/`
2. Create a `tmux-prod` symlink in `~/.local/bin/`
3. Install the `textual` Python package
4. Add a zsh `preexec` hook to `~/.zshrc` for command logging
5. Add tmux hooks to `~/.tmux.conf` for window/session tracking
6. Apply hooks to your live tmux session (if running inside one)

The installer is idempotent -- running it twice won't duplicate entries.

After installing, reload your shell:

```bash
source ~/.zshrc
```

### Recommended Shell Aliases

Add these to your `~/.zshrc` for quick access:

```bash
# tmux-productivity — terminal productivity dashboard
alias tp="python3 ~/.tmux-productivity/tui.py"
alias tmux-prod="python3 ~/.tmux-productivity/tui.py"
alias tp-status="python3 ~/.tmux-productivity/tui.py --status"
alias tp-inject="python3 ~/.tmux-productivity/tui.py --inject-only"
alias tp-import="python3 ~/.tmux-productivity/tui.py --import"
alias tp-install="bash ~/.tmux-productivity/setup.sh"
```

## Usage

```bash
# Launch the dashboard
tmux-prod        # or just: tp

# Show today's score on stdout (no TUI)
tp-status

# Inject preexec hooks into all open tmux zsh panes
tp-inject

# Import today's zsh history into events.log
tp-import

# Re-run the installer
tp-install
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--inject` | Inject preexec hooks into all tmux zsh panes, then launch TUI |
| `--inject-only` | Inject hooks and exit (don't launch TUI) |
| `--import` | Import today's zsh history into events.log and exit |
| `--status` | Show today's score and stats on stdout and exit |
| `-h`, `--help` | Show help message |

### Key Bindings (inside the TUI)

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh |
| `Left` / `h` | Previous day |
| `Right` / `l` | Next day |
| `i` | Inject hooks into tmux panes |

## How It Works

### Command Logging

A zsh `preexec` hook captures every command you run inside tmux and writes it as a tab-separated line to `~/.tmux-productivity/events.log`:

```
<unix_timestamp>\tcmd\t<command>\t<session:window>
```

### Event Logging

Tmux hooks (`after-new-window`, `after-new-session`) call `log_event.sh` to record structural events:

```
<unix_timestamp>\tevent\t<event_type>\t<session:window>
```

### Scoring

Your daily score is the sum of weighted activity points:

| Activity | Points Each |
|----------|-------------|
| Git commit | +25 |
| Git push | +15 |
| Lazygit | +15 |
| Claude | +12 |
| Test command | +12 |
| Nvim open | +10 |
| Build command | +10 |
| Active hour (5+ cmds) | +8 |
| Docker command | +8 |
| New window | +5 |
| SSH command | +5 |
| Unique nvim file | +3 |
| New session | +3 |

### Grades

| Grade | Minimum Score |
|-------|---------------|
| S | 500 |
| A | 350 |
| B | 200 |
| C | 100 |
| D | 50 |
| F | < 50 |

### Recognized Commands

The tracker classifies these command patterns:

- **Editor**: `nvim`, `vim`, `vi`, `n`, `v`, `nv` (+ extracts filenames)
- **Git**: `lazygit`, `git commit`, `git push`
- **AI**: `claude`
- **Build**: `dotnet build/run/publish`, `cargo build/run/check`, `npm run/build`, `make`, `go build/run`, `gradle`
- **Test**: `dotnet test`, `cargo test`, `npm test`, `pytest`, `go test`, `jest`
- **Infra**: `docker`, `ssh`

## Project Structure

```
tmux-productivity/
  tui.py            # Textual TUI dashboard (main entry point)
  log_event.sh      # Tmux hook script for event logging
  setup.sh          # One-shot installer
  requirements.txt  # Python dependencies
  LICENSE           # MIT license
```

## Uninstalling

1. Remove the zsh hook block from `~/.zshrc` (search for `tmux-productivity shell hook`)
2. Remove the tmux hooks from `~/.tmux.conf` (search for `tmux-productivity hooks`)
3. Delete `~/.tmux-productivity/`
4. Remove `~/.local/bin/tmux-prod`

## License

MIT
