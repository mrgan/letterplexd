#!/bin/bash
# Builds the notifier app bundle. Run again after editing notifier.applescript,
# or after replacing Assets.car / icon-1024.png.
set -euo pipefail

cd "$(dirname "$0")"

APP="Letterplexd.app"
PLIST="$APP/Contents/Info.plist"
RES="$APP/Contents/Resources"

rm -rf "$APP"
osacompile -o "$APP" notifier.applescript

# The bundle name is what macOS prints above the notification, and the
# identifier is what Notification Center keys its settings off, so both need to
# be ours rather than osacompile's defaults.
defaults write "$(pwd)/$PLIST" CFBundleName "Letterplexd"
defaults write "$(pwd)/$PLIST" CFBundleDisplayName "Letterplexd"
defaults write "$(pwd)/$PLIST" CFBundleIdentifier "com.letterplexd.notifier"
defaults write "$(pwd)/$PLIST" LSUIElement -bool true

# osacompile ships its own generic applet icon. Drop it before installing ours,
# so there's never a stale icon competing for what macOS displays.
defaults delete "$(pwd)/$PLIST" CFBundleIconFile 2>/dev/null || true
rm -f "$RES/applet.icns"

# The Icon Composer icon, compiled by Xcode's asset pipeline into Assets.car.
# CFBundleIconName names the icon inside it. macOS 26+ reads this and renders
# the layered glass version. See CLAUDE.md "The app icon".
if [ -f Assets.car ]; then
	cp Assets.car "$RES/Assets.car"
	defaults write "$(pwd)/$PLIST" CFBundleIconName "Letterplexd App Icon"
else
	echo "warning: Assets.car missing; no Icon Composer icon" >&2
fi

# Pre-Tahoe systems don't read CFBundleIconName at all, so ship a classic .icns
# built from the flat render as well. Apple's own apps carry both.
if [ -f icon-1024.png ]; then
	ICONSET="AppIcon.iconset"
	rm -rf "$ICONSET"
	mkdir -p "$ICONSET"
	for spec in 16:16x16 32:16x16@2x 32:32x32 64:32x32@2x \
		128:128x128 256:128x128@2x 256:256x256 512:256x256@2x \
		512:512x512 1024:512x512@2x; do
		px="${spec%%:*}"
		name="${spec##*:}"
		sips -z "$px" "$px" icon-1024.png --out "$ICONSET/icon_$name.png" >/dev/null
	done
	iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"
	rm -rf "$ICONSET"
	defaults write "$(pwd)/$PLIST" CFBundleIconFile "AppIcon"
else
	echo "warning: icon-1024.png missing; no .icns fallback for pre-Tahoe" >&2
fi

plutil -convert xml1 "$PLIST"

# Re-sign after editing the plist, or macOS treats the bundle as damaged.
codesign --force --sign - "$APP"

echo "Built $APP"
