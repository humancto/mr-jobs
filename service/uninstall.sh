#!/bin/bash
# Uninstall MR.Jobs LaunchAgent

PLIST_NAME="com.mrjobs.dashboard"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [ -f "$PLIST_PATH" ]; then
    echo "Stopping MR.Jobs..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "MR.Jobs LaunchAgent removed."
else
    echo "MR.Jobs LaunchAgent not found."
fi
