#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.tracker.pid"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/tracker.log"

mkdir -p "$LOG_DIR" "$ROOT/data" "$ROOT/reports"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "tracker already running: pid $old_pid"
    exit 0
  fi
fi

cd "$ROOT"
nohup ./.venv/bin/python3 -u tracker/tracker.py >>"$LOG_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"

echo "tracker started: pid $pid"
echo "log: $LOG_FILE"
