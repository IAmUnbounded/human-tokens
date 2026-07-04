#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.human-token-tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_VALUE="$(id -u)"

launchctl bootout "gui/$UID_VALUE" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"

echo "uninstalled LaunchAgent: $LABEL"
