#!/bin/bash
# Builds the notifier app bundle. Run again after editing notifier.applescript.
set -euo pipefail

cd "$(dirname "$0")"

APP="Letterplexd.app"
PLIST="$APP/Contents/Info.plist"

rm -rf "$APP"
osacompile -o "$APP" notifier.applescript

# The bundle name is what macOS prints above the notification, and the
# identifier is what Notification Center keys its settings off, so both need to
# be ours rather than osacompile's defaults.
defaults write "$(pwd)/$PLIST" CFBundleName "Letterplexd"
defaults write "$(pwd)/$PLIST" CFBundleDisplayName "Letterplexd"
defaults write "$(pwd)/$PLIST" CFBundleIdentifier "com.neven.letterplexd.notifier"
defaults write "$(pwd)/$PLIST" LSUIElement -bool true
plutil -convert xml1 "$PLIST"

# Re-sign after editing the plist, or macOS treats the bundle as damaged.
codesign --force --sign - "$APP"

echo "Built $APP"
