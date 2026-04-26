#!/bin/bash
# Uninstall the bike_alert launchd agent.

set -e

PLIST="$HOME/Library/LaunchAgents/com.hugo.bikealert.plist"

if [[ -f "$PLIST" ]]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled."
else
    echo "Nothing to uninstall (plist not found at $PLIST)."
fi
