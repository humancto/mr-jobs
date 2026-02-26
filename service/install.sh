#!/bin/bash
# Install MR.Jobs as a macOS LaunchAgent (runs on login, restarts on crash)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.mrjobs.dashboard"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="$SCRIPT_DIR/logs"
PYTHON="python3.11"

# Check Python
if ! command -v $PYTHON &>/dev/null; then
    PYTHON="python3"
fi

mkdir -p "$LOG_DIR"

echo "Installing MR.Jobs LaunchAgent..."
echo "  Project: $SCRIPT_DIR"
echo "  Python: $PYTHON"
echo "  Logs: $LOG_DIR"

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SCRIPT_DIR}/main.py</string>
        <string>server</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/server.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/server.error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

# Unload if already loaded
launchctl unload "$PLIST_PATH" 2>/dev/null || true

# Load the agent
launchctl load "$PLIST_PATH"

echo ""
echo "MR.Jobs installed and started!"
echo "  Dashboard: http://localhost:8080"
echo "  Logs: $LOG_DIR/server.log"
echo "  To stop: bash $SCRIPT_DIR/service/uninstall.sh"
