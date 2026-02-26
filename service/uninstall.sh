#!/bin/bash
# Uninstall Auto-Apply LaunchAgent

PLIST_NAME="com.autoapply.dashboard"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [ -f "$PLIST_PATH" ]; then
    echo "Stopping Auto-Apply..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Auto-Apply LaunchAgent removed."
else
    echo "Auto-Apply LaunchAgent not found."
fi
