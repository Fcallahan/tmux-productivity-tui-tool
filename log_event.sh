#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="$HOME/.tmux-productivity"
LOG_FILE="$LOG_DIR/events.log"
mkdir -p "$LOG_DIR"

TS=$(date +%s)
EVENT="${1:-unknown}"
WIN=$(tmux display-message -p '#S:#W' 2>/dev/null || echo "unknown")

printf '%s\tevent\t%s\t%s\n' "$TS" "$EVENT" "$WIN" >> "$LOG_FILE"
