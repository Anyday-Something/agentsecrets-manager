#!/bin/bash
# Builds "AgentSecrets Manager.app" — a double-clickable launcher for agentsecrets_gui.py.
# Usage: ./build_app.sh [destination-folder]   (default: ~/Applications)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/AgentSecrets Manager.app"

mkdir -p "$DEST"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>AgentSecrets Manager</string>
  <key>CFBundleDisplayName</key><string>AgentSecrets Manager</string>
  <key>CFBundleIdentifier</key><string>local.agentsecrets.manager</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# Apps launched from Finder get a minimal PATH; make Homebrew's agentsecrets findable.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
LOG="\$HOME/Library/Logs/AgentSecretsManager.log"
exec /usr/bin/python3 "$HERE/agentsecrets_gui.py" >>"\$LOG" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

echo "Built: $APP"
echo "Open it from Finder/Spotlight, or run: open \"$APP\""
