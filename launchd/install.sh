#!/bin/bash
# Install bike_alert as a launchd agent.
# Runs every 5 minutes in the background, even after terminal/Mac restart.

set -e

PLIST_NAME="com.hugo.bikealert.plist"
SOURCE="$(cd "$(dirname "$0")" && pwd)/$PLIST_NAME"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/$PLIST_NAME"

# Make sure LaunchAgents directory exists
mkdir -p "$TARGET_DIR"

# If already loaded, unload first so we can replace it
if launchctl list | grep -q "com.hugo.bikealert"; then
    echo "Already loaded — unloading first..."
    launchctl unload "$TARGET" 2>/dev/null || true
fi

# Copy plist into LaunchAgents
cp "$SOURCE" "$TARGET"

# Load (the -w flag also enables it permanently)
launchctl load -w "$TARGET"

echo ""
echo "Installed: $TARGET"
echo ""
echo "Status check:"
launchctl list | grep com.hugo.bikealert || echo "  (not yet visible — wait a few seconds)"
echo ""
echo "Live logs:"
echo "  tail -f ~/PycharmProjects/PythonProject/bike_alert/bike_alert.log"
echo "  tail -f ~/PycharmProjects/PythonProject/bike_alert/launchd.out.log"
echo ""
echo "To stop and remove:"
echo "  bash $(dirname "$0")/uninstall.sh"
