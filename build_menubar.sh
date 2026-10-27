#!/bin/bash
# Builds "AgentSecrets Menu.app" — a menu bar app that bundles the GUI server.
# Usage: ./build_menubar.sh [destination-folder]   (default: ~/Applications)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/AgentSecrets Menu.app"

mkdir -p "$DEST"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -o "$APP/Contents/MacOS/AgentSecretsMenu" "$HERE/menubar/main.swift" \
  -framework AppKit -framework WebKit
cp "$HERE/agentsecrets_gui.py" "$HERE/ui.html" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>AgentSecrets Menu</string>
  <key>CFBundleDisplayName</key><string>AgentSecrets Menu</string>
  <key>CFBundleIdentifier</key><string>local.agentsecrets.menu</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>AgentSecretsMenu</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "Built: $APP"
echo "Start it with: open \"$APP\""
