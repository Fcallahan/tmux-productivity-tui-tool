#!/usr/bin/env python3
"""tmux-productivity — Terminal productivity dashboard."""

import datetime
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Footer, Header, Static

from rich.table import Table
from rich.text import Text
from rich import box

# ── Scoring ──────────────────────────────────────────────────────────────────

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
    "gh_cmd":      5,
    "cd_cmd":      1,
}

GRADE_THRESHOLDS = [(500, "S"), (350, "A"), (200, "B"), (100, "C"), (50, "D")]
MAX_SCORE = 600

GRADE_COLORS = {
    "S": "gold1",
    "A": "green",
    "B": "cyan",
    "C": "yellow",
    "D": "orange1",
    "F": "red",
}

LOG_FILE = Path.home() / ".tmux-productivity" / "events.log"

# ── Alias Resolution ────────────────────────────────────────────────────────
# We parse ~/.zshrc for alias definitions and expand them before classifying
# commands. This is critical because zsh preexec receives the command AS TYPED,
# not the expanded alias.

def _parse_zshrc_aliases() -> Dict[str, str]:
    """Parse alias definitions from ~/.zshrc."""
    aliases: Dict[str, str] = {}
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return aliases
    try:
        with open(zshrc, "r") as f:
            for line in f:
                line = line.strip()
                # Match: alias name="value" or alias name='value'
                m = re.match(r'^alias\s+([a-zA-Z0-9_.-]+)=(["\'])(.*?)\2\s*$', line)
                if m:
                    aliases[m.group(1)] = m.group(3)
    except OSError:
        pass
    return aliases


def _resolve_alias(cmd: str, aliases: Dict[str, str]) -> str:
    """Expand the first word of a command if it matches a known alias.

    Only does one level of expansion (no recursive alias chains).
    For compound alias values (containing && or ||), only the first
    sub-command is returned for classification purposes.
    """
    parts = cmd.split(None, 1)
    if not parts:
        return cmd
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if first in aliases:
        expanded = aliases[first]
        # For compound commands (e.g., "source ~/.zshrc && echo done"),
        # only keep the first sub-command for classification
        for sep in (" && ", " || ", " ; "):
            if sep in expanded:
                expanded = expanded.split(sep)[0].strip()
                break
        if rest:
            return f"{expanded} {rest}"
        return expanded
    return cmd

# ── Zsh History Import ──────────────────────────────────────────────────────
# The preexec hook only captures commands in shells that sourced the updated
# .zshrc.  For comprehensive coverage, we also import from ~/.zsh_history,
# which captures EVERY command across ALL shells (zsh extended history format).

_history_import_cache: Dict[str, float] = {}  # date_str -> mtime of last import


def _import_zsh_history(date: datetime.date) -> int:
    """Import commands from ~/.zsh_history into events.log for the given date.

    Returns the number of new entries added.  Deduplicates by checking existing
    (timestamp, command) pairs in events.log.  Rate-limited: only re-imports if
    the history file has been modified since last check for this specific date.
    """
    global _history_import_cache

    hist_file = Path.home() / ".zsh_history"
    if not hist_file.exists():
        return 0

    try:
        mtime = hist_file.stat().st_mtime
    except OSError:
        return 0

    date_key = date.isoformat()
    if date_key in _history_import_cache and mtime == _history_import_cache[date_key]:
        return 0  # history file hasn't changed for this date
    _history_import_cache[date_key] = mtime

    start_ts = int(time.mktime(datetime.datetime.combine(date, datetime.time.min).timetuple()))
    end_ts = int(time.mktime(datetime.datetime.combine(date, datetime.time.max).timetuple()))

    # Read existing events.log entries to avoid duplicates
    existing: Set[Tuple[int, str]] = set()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 3 and parts[1] == "cmd":
                        try:
                            existing.add((int(parts[0]), parts[2]))
                        except ValueError:
                            pass
        except OSError:
            pass

    # Parse ~/.zsh_history (extended format: ": timestamp:0;command")
    new_entries: List[Tuple[int, str]] = []
    hist_re = re.compile(r'^: (\d+):\d+;(.+)$')

    try:
        with open(hist_file, "r", errors="replace") as f:
            for line in f:
                m = hist_re.match(line.rstrip("\n"))
                if not m:
                    continue
                ts = int(m.group(1))
                cmd = m.group(2).strip()
                if ts < start_ts or ts > end_ts:
                    continue
                if not cmd:
                    continue
                if (ts, cmd) not in existing:
                    new_entries.append((ts, cmd))
    except OSError:
        return 0

    if not new_entries:
        return 0

    # Append to events.log
    try:
        with open(LOG_FILE, "a") as f:
            for ts, cmd in new_entries:
                f.write(f"{ts}\tcmd\t{cmd}\thistory\n")
    except OSError:
        return 0

    return len(new_entries)


# ── Hook Injection ──────────────────────────────────────────────────────────

def inject_hooks_to_panes() -> int:
    """Send 'source ~/.zshrc' to all zsh panes so the preexec hook activates.

    Returns the number of panes injected.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return 0
        injected = 0
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            pane_id = parts[0]
            pane_cmd = parts[1] if len(parts) > 1 else ""
            # Only inject into zsh shells (not nvim, lazygit, claude, etc.)
            if pane_cmd.strip() == "zsh":
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_id, "source ~/.zshrc", "Enter"],
                    capture_output=True
                )
                injected += 1
        return injected
    except Exception:
        return 0


# ── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class DayMetrics:
    windows_opened: int = 0
    sessions_opened: int = 0
    nvim_opens: int = 0
    nvim_files: Set[str] = field(default_factory=set)
    lazygit_opens: int = 0
    claude_opens: int = 0
    git_commits: int = 0
    git_pushes: int = 0
    git_other: int = 0
    build_cmds: int = 0
    test_cmds: int = 0
    docker_cmds: int = 0
    ssh_cmds: int = 0
    gh_cmds: int = 0
    cd_cmds: int = 0
    total_commands: int = 0
    commands_by_hour: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    timeline: List[Tuple[int, str, str]] = field(default_factory=list)

    @property
    def active_hours(self) -> int:
        return sum(1 for h in self.commands_by_hour.values() if h >= 5)

    def score(self) -> int:
        return (
            self.windows_opened * WEIGHTS["new_window"]
            + self.sessions_opened * WEIGHTS["new_session"]
            + self.nvim_opens * WEIGHTS["nvim_open"]
            + len(self.nvim_files) * WEIGHTS["nvim_file"]
            + self.lazygit_opens * WEIGHTS["lazygit"]
            + self.claude_opens * WEIGHTS["claude"]
            + self.git_commits * WEIGHTS["git_commit"]
            + self.git_pushes * WEIGHTS["git_push"]
            + self.build_cmds * WEIGHTS["build_cmd"]
            + self.test_cmds * WEIGHTS["test_cmd"]
            + self.docker_cmds * WEIGHTS["docker_cmd"]
            + self.ssh_cmds * WEIGHTS["ssh_cmd"]
            + self.gh_cmds * WEIGHTS["gh_cmd"]
            + self.cd_cmds * WEIGHTS["cd_cmd"]
            + self.active_hours * WEIGHTS["active_hour"]
        )

    def grade(self) -> str:
        s = self.score()
        for threshold, letter in GRADE_THRESHOLDS:
            if s >= threshold:
                return letter
        return "F"


# ── Log Parsing ──────────────────────────────────────────────────────────────

def _classify_cmd(cmd: str, m: DayMetrics, ts: int) -> None:
    """Classify a single (already alias-expanded) command and update metrics."""
    classified = False

    # Editor: nvim, vim, vi, neovim
    if re.match(r'^(nvim|vim|vi|neovim)\b', cmd):
        m.nvim_opens += 1
        args = cmd.split()[1:]
        for arg in args:
            if not arg.startswith("-") and not arg.startswith("+"):
                m.nvim_files.add(arg)
        detail = " ".join(a for a in args if not a.startswith("-") and not a.startswith("+"))
        m.timeline.append((ts, "📝 nvim", detail))
        classified = True

    # Lazygit
    elif re.match(r'^lazygit\b', cmd):
        m.lazygit_opens += 1
        m.timeline.append((ts, "🔀 lazygit", ""))
        classified = True

    # Lazydocker (count as docker)
    elif re.match(r'^lazydocker\b', cmd):
        m.docker_cmds += 1
        m.timeline.append((ts, "🐳 lazydocker", ""))
        classified = True

    # Claude / AI tools
    elif re.match(r'^(claude|opencode)\b', cmd):
        m.claude_opens += 1
        m.timeline.append((ts, "🤖 ai-tool", cmd.split()[0]))
        classified = True

    # Git commit
    elif re.match(r'^git\s+commit', cmd):
        m.git_commits += 1
        m.timeline.append((ts, "✅ commit", ""))
        classified = True

    # Git push
    elif re.match(r'^git\s+push', cmd):
        m.git_pushes += 1
        m.timeline.append((ts, "⬆  push", ""))
        classified = True

    # Git (other subcommands: status, add, diff, log, fetch, pull, etc.)
    elif re.match(r'^git\s+\w', cmd):
        m.git_other += 1
        subcmd = cmd.split()[1] if len(cmd.split()) > 1 else ""
        m.timeline.append((ts, "🔧 git", subcmd))
        classified = True

    # GitHub CLI
    elif re.match(r'^gh\s', cmd):
        m.gh_cmds += 1
        subcmd = " ".join(cmd.split()[1:3])
        m.timeline.append((ts, "🐙 gh", subcmd))
        classified = True

    # Test commands
    elif re.match(r'^(dotnet\s+test|cargo\s+test|npm\s+test|pytest\b|go\s+test|jest\b)', cmd):
        m.test_cmds += 1
        m.timeline.append((ts, "🧪 test", cmd))
        classified = True

    # Build commands
    elif re.match(r'^(dotnet\s+(build|run|publish)|cargo\s+(build|run|check)|npm\s+(run|build)|make\b|go\s+(build|run)|gradle\b)', cmd):
        m.build_cmds += 1
        m.timeline.append((ts, "🔨 build", cmd))
        classified = True

    # Docker
    elif re.match(r'^docker\b', cmd):
        m.docker_cmds += 1
        subcmd = cmd.split()[1] if len(cmd.split()) > 1 else ""
        m.timeline.append((ts, "🐳 docker", subcmd))
        classified = True

    # SSH
    elif re.match(r'^ssh\b', cmd):
        m.ssh_cmds += 1
        target = cmd.split()[1] if len(cmd.split()) > 1 else ""
        m.timeline.append((ts, "🔐 ssh", target))
        classified = True

    # Directory navigation (cd, z, zoxide)
    elif re.match(r'^(cd|z)\s', cmd):
        m.cd_cmds += 1
        dest = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else ""
        # Shorten home directory paths
        home = str(Path.home())
        dest = dest.replace(home, "~")
        m.timeline.append((ts, "📂 cd", dest))
        classified = True

    # Source / reload
    elif re.match(r'^source\b', cmd):
        args = cmd.split()
        src_file = args[1] if len(args) > 1 else ""
        # Shorten home dir
        home = str(Path.home())
        src_file = src_file.replace(home, "~")
        m.timeline.append((ts, "🔄 source", src_file))
        classified = True

    # If not classified, still show in timeline as a generic command
    if not classified:
        # Truncate for display
        m.timeline.append((ts, "⌨  cmd", cmd))


# Cache aliases so we don't re-parse ~/.zshrc on every refresh
_alias_cache: Optional[Dict[str, str]] = None
_alias_cache_mtime: float = 0.0


def _get_aliases() -> Dict[str, str]:
    """Get aliases with caching; re-reads if .zshrc is modified."""
    global _alias_cache, _alias_cache_mtime
    zshrc = Path.home() / ".zshrc"
    try:
        mtime = zshrc.stat().st_mtime
    except OSError:
        return {}
    if _alias_cache is None or mtime != _alias_cache_mtime:
        _alias_cache = _parse_zshrc_aliases()
        _alias_cache_mtime = mtime
    return _alias_cache


def parse_log(date: datetime.date) -> DayMetrics:
    m = DayMetrics()

    start_ts = int(time.mktime(datetime.datetime.combine(date, datetime.time.min).timetuple()))
    end_ts = int(time.mktime(datetime.datetime.combine(date, datetime.time.max).timetuple()))

    if not LOG_FILE.exists():
        return m

    aliases = _get_aliases()

    with open(LOG_FILE, "r") as f:
        for line in f:
            line = line.rstrip("\n")
            parts = line.split("\t")
            if len(parts) < 4:
                continue

            try:
                ts = int(parts[0])
            except ValueError:
                continue

            if ts < start_ts or ts > end_ts:
                continue

            kind = parts[1]
            data = parts[2]
            window = parts[3]

            if kind == "event":
                if data == "new-window":
                    m.windows_opened += 1
                    m.timeline.append((ts, "🖥  window", window))
                elif data == "new-session":
                    m.sessions_opened += 1
                    m.timeline.append((ts, "🚀 session", window))

            elif kind == "cmd":
                m.total_commands += 1
                hour = datetime.datetime.fromtimestamp(ts).hour
                m.commands_by_hour[hour] += 1
                raw_cmd = data.strip()

                # Resolve aliases before classifying
                cmd = _resolve_alias(raw_cmd, aliases)
                _classify_cmd(cmd, m, ts)

    m.timeline.sort(key=lambda x: x[0])
    return m


# ── Render Functions ─────────────────────────────────────────────────────────

def render_score(m: DayMetrics) -> Text:
    s = m.score()
    g = m.grade()
    color = GRADE_COLORS.get(g, "white")

    pct = min(s / MAX_SCORE, 1.0)
    filled = int(pct * 20)
    bar = "█" * filled + "░" * (20 - filled)

    t = Text()
    t.append("\n")
    t.append(f"  {s}\n", style="bold white")
    t.append(f"  Grade: ", style="dim")
    t.append(f"{g}\n", style=f"bold {color}")
    t.append(f"\n  {bar}\n", style="cyan")
    t.append(f"  {int(pct*100)}% of {MAX_SCORE}\n\n", style="dim")
    t.append(f"  ⌨  {m.total_commands} cmds\n", style="white")
    t.append(f"  ⏱  {m.active_hours} active hrs\n", style="white")
    t.append(f"  📂 {len(m.nvim_files)} files\n", style="white")
    return t


def render_metrics(m: DayMetrics) -> Table:
    table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
    table.add_column("Activity", style="bold white", ratio=3)
    table.add_column("Count", justify="right", style="cyan", ratio=1)
    table.add_column("Points", justify="right", style="green", ratio=1)

    rows = [
        ("Terminals",    m.windows_opened + m.sessions_opened,
         m.windows_opened * WEIGHTS["new_window"] + m.sessions_opened * WEIGHTS["new_session"]),
        ("Nvim opens",   m.nvim_opens,     m.nvim_opens * WEIGHTS["nvim_open"]),
        ("Unique files", len(m.nvim_files), len(m.nvim_files) * WEIGHTS["nvim_file"]),
        ("Lazygit",      m.lazygit_opens,  m.lazygit_opens * WEIGHTS["lazygit"]),
        ("Claude/AI",    m.claude_opens,   m.claude_opens * WEIGHTS["claude"]),
        ("Commits",      m.git_commits,    m.git_commits * WEIGHTS["git_commit"]),
        ("Pushes",       m.git_pushes,     m.git_pushes * WEIGHTS["git_push"]),
        ("Git (other)",  m.git_other,      0),
        ("GitHub CLI",   m.gh_cmds,        m.gh_cmds * WEIGHTS["gh_cmd"]),
        ("Builds",       m.build_cmds,     m.build_cmds * WEIGHTS["build_cmd"]),
        ("Tests",        m.test_cmds,      m.test_cmds * WEIGHTS["test_cmd"]),
        ("Docker",       m.docker_cmds,    m.docker_cmds * WEIGHTS["docker_cmd"]),
        ("SSH",          m.ssh_cmds,       m.ssh_cmds * WEIGHTS["ssh_cmd"]),
        ("Navigation",   m.cd_cmds,        m.cd_cmds * WEIGHTS["cd_cmd"]),
        ("Active hours", m.active_hours,   m.active_hours * WEIGHTS["active_hour"]),
    ]

    for name, count, pts in rows:
        c = "—" if count == 0 else str(count)
        p = "—" if pts == 0 else f"+{pts}"
        table.add_row(name, c, p)

    # Total row
    table.add_row("─" * 12, "─" * 4, "─" * 5, style="dim")
    table.add_row("Total cmds", str(m.total_commands), f"+{m.score()}", style="bold")

    return table


def render_heatmap(m: DayMetrics) -> Text:
    blocks = " ░▒▓█"
    colors = ["dim", "dim cyan", "cyan", "bold cyan", "bold white"]

    # Full 24-hour range so late-night/early-morning activity is visible
    hours = list(range(0, 24))
    counts = [m.commands_by_hour.get(h, 0) for h in hours]
    peak = max(counts) if max(counts) > 0 else 1

    t = Text()
    t.append("  ")
    for h in hours:
        t.append(f"{h:02d} ", style="dim")
    t.append("\n  ")
    for c in counts:
        level = min(int(c / peak * 4), 4) if c > 0 else 0
        t.append(f" {blocks[level]} ", style=colors[level])
    return t


def render_timeline(m: DayMetrics) -> Text:
    if not m.timeline:
        return Text("  No events recorded yet.", style="dim italic")

    t = Text()
    for ts, label, detail in reversed(m.timeline):
        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        detail_trunc = (detail[:50] + "…") if len(detail) > 50 else detail
        t.append(f"  {time_str}  ", style="dim")
        t.append(f"{label:<14s}  ", style="white")
        t.append(f"{detail_trunc}\n", style="dim cyan")
    return t


# ── TUI Widgets ──────────────────────────────────────────────────────────────

class TimelinePanel(ScrollableContainer):
    def compose(self) -> ComposeResult:
        yield Static("", id="tl-content")

    def refresh_timeline(self, m: DayMetrics) -> None:
        self.query_one("#tl-content", Static).update(render_timeline(m))
        self.scroll_home(animate=False)


# ── Main App ─────────────────────────────────────────────────────────────────

class ProductivityApp(App):
    CSS = """
    Screen { background: #0d0d0d; }
    Header { background: #1a1a2e; }
    #top-row { height: 16; margin: 0 1; }
    #score-panel { width: 26; border: round #2a2a3e; padding: 0 1; background: #111118; }
    #metrics-panel { width: 1fr; border: round #2a2a3e; padding: 0 1; background: #111118; margin-left: 1; }
    #heatmap-panel { height: 5; border: round #2a2a3e; padding: 1 1; background: #111118; margin: 1 1 0 1; }
    #timeline-panel { height: 1fr; border: round #2a2a3e; background: #0d0d0d; margin: 1; overflow-y: scroll; }
    #tl-content { padding: 1 0; }
    """

    BINDINGS = [
        Binding("q",     "quit",     "Quit"),
        Binding("r",     "refresh",  "Refresh"),
        Binding("left",  "prev_day", "Prev Day"),
        Binding("right", "next_day", "Next Day"),
        Binding("h",     "prev_day", show=False),
        Binding("l",     "next_day", show=False),
        Binding("i",     "inject_hooks", "Inject Hooks"),
    ]

    def __init__(self):
        super().__init__()
        self._date = datetime.date.today()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top-row"):
            yield Static("", id="score-panel")
            yield Static("", id="metrics-panel")
        yield Static("", id="heatmap-panel")
        yield TimelinePanel(id="timeline-panel")
        yield Footer()

    def on_mount(self) -> None:
        # Import history on first load to capture commands from all shells
        _import_zsh_history(self._date)
        self._load()
        self.set_interval(30, self._load)

    def _load(self) -> None:
        # Import any new history entries since last check
        _import_zsh_history(self._date)

        m = parse_log(self._date)

        self.title = "tmux-productivity"
        self.sub_title = f"{self._date.isoformat()}  •  {m.total_commands} cmds  •  auto-refresh"

        self.query_one("#score-panel", Static).update(render_score(m))
        self.query_one("#metrics-panel", Static).update(render_metrics(m))
        self.query_one("#heatmap-panel", Static).update(render_heatmap(m))
        self.query_one("#timeline-panel", TimelinePanel).refresh_timeline(m)

    def action_refresh(self) -> None:
        self._load()

    def action_prev_day(self) -> None:
        self._date -= datetime.timedelta(days=1)
        self._load()

    def action_next_day(self) -> None:
        if self._date < datetime.date.today():
            self._date += datetime.timedelta(days=1)
            self._load()

    def action_inject_hooks(self) -> None:
        """Send 'source ~/.zshrc' to every zsh pane so the preexec hook activates."""
        n = inject_hooks_to_panes()
        self.sub_title = f"{self._date.isoformat()}  •  Hooks injected into {n} zsh panes!"


def main() -> None:
    """Entry point with CLI flag support."""
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print("tmux-productivity — Terminal productivity dashboard")
        print()
        print("Usage: tmux-prod [OPTIONS]")
        print()
        print("Options:")
        print("  --inject    Inject preexec hooks into all tmux zsh panes, then launch TUI")
        print("  --inject-only  Inject hooks and exit (don't launch TUI)")
        print("  --import    Import today's zsh history into events.log and exit")
        print("  --status    Show today's stats on stdout and exit")
        print("  -h, --help  Show this help message")
        return

    if "--inject-only" in args:
        n = inject_hooks_to_panes()
        print(f"Injected preexec hook into {n} zsh panes.")
        return

    if "--import" in args:
        today = datetime.date.today()
        n = _import_zsh_history(today)
        print(f"Imported {n} new commands from zsh history for {today}.")
        return

    if "--status" in args:
        today = datetime.date.today()
        _import_zsh_history(today)
        m = parse_log(today)
        print(f"Date: {today}")
        print(f"Commands: {m.total_commands}")
        print(f"Score: {m.score()} (Grade {m.grade()})")
        print(f"Active hours: {m.active_hours}")
        print(f"Nvim opens: {m.nvim_opens}, Files: {len(m.nvim_files)}")
        print(f"Git commits: {m.git_commits}, Pushes: {m.git_pushes}")
        print(f"Lazygit: {m.lazygit_opens}, Claude/AI: {m.claude_opens}")
        print(f"Builds: {m.build_cmds}, Tests: {m.test_cmds}")
        return

    # Auto-inject hooks on launch (best-effort, only into zsh panes)
    if "--inject" in args:
        n = inject_hooks_to_panes()
        if n > 0:
            print(f"Injected preexec hook into {n} zsh panes.")

    app = ProductivityApp()
    app.run()


if __name__ == "__main__":
    main()
