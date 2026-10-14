#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.tmux-productivity"

echo "╔══════════════════════════════════════════════╗"
echo "║   tmux-productivity installer                ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# 1. Create install directory
mkdir -p "$INSTALL_DIR"

# 2. Copy files from script's directory into install dir
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    cp "$SCRIPT_DIR/tui.py" "$INSTALL_DIR/tui.py"
    cp "$SCRIPT_DIR/log_event.sh" "$INSTALL_DIR/log_event.sh"
    cp "$SCRIPT_DIR/setup.sh" "$INSTALL_DIR/setup.sh"
fi

# 3. Make executable
chmod +x "$INSTALL_DIR/tui.py"
chmod +x "$INSTALL_DIR/log_event.sh"

echo "✓ Files installed to $INSTALL_DIR"

# 4. Create symlink
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/tui.py" "$HOME/.local/bin/tmux-prod"
echo "✓ Symlink created: ~/.local/bin/tmux-prod → ~/.tmux-productivity/tui.py"

# 5. Install textual
echo "  Installing textual..."
if pip install --quiet textual --break-system-packages 2>/dev/null; then
    echo "✓ textual installed (--break-system-packages)"
elif pip install --quiet textual --user 2>/dev/null; then
    echo "✓ textual installed (--user)"
elif pip3 install --quiet textual --break-system-packages 2>/dev/null; then
    echo "✓ textual installed via pip3 (--break-system-packages)"
elif pip3 install --quiet textual --user 2>/dev/null; then
    echo "✓ textual installed via pip3 (--user)"
else
    echo "⚠ Could not install textual automatically. Please run: pip install textual"
fi

# 6. Append zsh hook to ~/.zshrc (guarded)
ZSHRC="$HOME/.zshrc"
if ! grep -q "tmux-productivity shell hook" "$ZSHRC" 2>/dev/null; then
    cat >> "$ZSHRC" << 'ZSH_HOOK'

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
ZSH_HOOK
    echo "✓ zsh preexec hook added to ~/.zshrc"
else
    echo "✓ zsh hook already in ~/.zshrc (skipped)"
fi

# 7. Append tmux hooks to ~/.tmux.conf (guarded)
TMUX_CONF="$HOME/.tmux.conf"
touch "$TMUX_CONF"
if ! grep -q "tmux-productivity hooks" "$TMUX_CONF" 2>/dev/null; then
    cat >> "$TMUX_CONF" << 'TMUX_HOOK'

# tmux-productivity hooks
set-hook -g after-new-window  "run-shell '~/.tmux-productivity/log_event.sh new-window'"
set-hook -g after-new-session "run-shell '~/.tmux-productivity/log_event.sh new-session'"
TMUX_HOOK
    echo "✓ tmux hooks added to ~/.tmux.conf"
else
    echo "✓ tmux hooks already in ~/.tmux.conf (skipped)"
fi

# 8. Apply hooks to live tmux session if inside tmux
if [ -n "${TMUX:-}" ]; then
    tmux set-hook -g after-new-window  "run-shell '~/.tmux-productivity/log_event.sh new-window'"
    tmux set-hook -g after-new-session "run-shell '~/.tmux-productivity/log_event.sh new-session'"
    echo "✓ tmux hooks applied to live session"
else
    echo "  ℹ Not inside tmux — hooks will activate next tmux session"
fi

# 9. Print summary
echo ""
echo "══════════════════════════════════════════════"
echo "  Installation complete!"
echo ""
echo "  Usage:  tmux-prod"
echo ""
echo "  Keys:   q=quit  r=refresh  ←/→ or h/l=change day"
echo ""
echo "  Scoring weights:"
printf "    %-16s %s\n" "Activity" "Points"
printf "    %-16s %s\n" "────────────────" "──────"
printf "    %-16s %s\n" "Git commit" "+25"
printf "    %-16s %s\n" "Git push" "+15"
printf "    %-16s %s\n" "Lazygit" "+15"
printf "    %-16s %s\n" "Claude" "+12"
printf "    %-16s %s\n" "Test cmd" "+12"
printf "    %-16s %s\n" "Nvim open" "+10"
printf "    %-16s %s\n" "Build cmd" "+10"
printf "    %-16s %s\n" "Active hour" "+8"
printf "    %-16s %s\n" "Docker cmd" "+8"
printf "    %-16s %s\n" "New window" "+5"
printf "    %-16s %s\n" "SSH cmd" "+5"
printf "    %-16s %s\n" "Nvim file" "+3"
printf "    %-16s %s\n" "New session" "+3"
echo ""
echo "  Grades: S≥500  A≥350  B≥200  C≥100  D≥50  F<50"
echo "══════════════════════════════════════════════"
