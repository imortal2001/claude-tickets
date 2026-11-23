#!/bin/sh
# Builds ~/Applications/Claude Tickets.app: a native macOS window (AppKit + WKWebView) around the dashboard at
# http://127.0.0.1:8790, from main.swift. Run by ../install.sh; run it alone to rebuild after changing main.swift.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
APP="$HOME/Applications/Claude Tickets.app"
WORK="$(mktemp -d)"
ICON_SRC="$HERE/AppIcon-1024.png"   # committed; redraw it with: python3 make_icon.py AppIcon-1024.png (needs Pillow)
command -v swiftc >/dev/null || { echo "swiftc not found: install Xcode's command line tools (xcode-select --install)"; exit 1; }

swiftc -O "$HERE/main.swift" -o "$WORK/Claude Tickets" -framework Cocoa -framework WebKit

mkdir -p "$WORK/icon.iconset"
for s in 16 32 64 128 256 512; do
  sips -z $s $s "$ICON_SRC" --out "$WORK/icon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$ICON_SRC" --out "$WORK/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$WORK/icon.iconset" -o "$WORK/AppIcon.icns"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$WORK/Claude Tickets" "$APP/Contents/MacOS/Claude Tickets"
cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Claude Tickets</string>
  <key>CFBundleDisplayName</key><string>Claude Tickets</string>
  <key>CFBundleExecutable</key><string>Claude Tickets</string>
  <key>CFBundleIdentifier</key><string>local.claude-tickets</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST
codesign --force --deep -s - "$APP" >/dev/null 2>&1
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
rm -rf "$WORK"
echo "built: $APP"
