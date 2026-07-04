#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.tracker.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "tracker is not running"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  echo "tracker is not running"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "tracker stopped: pid $pid"
else
  echo "tracker process was not running"
fi

rm -f "$PID_FILE"
