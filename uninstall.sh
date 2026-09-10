#!/bin/bash
# Stops and removes the launchd agent. Leaves the checkout, state/ and logs/
# alone, so reinstalling later picks up where it left off.
set -euo pipefail

LABEL="${LETTERPLEXD_LABEL:-com.letterplexd}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2> /dev/null || true
rm -f "$PLIST"

echo "Removed $LABEL. Nothing is scheduled any more."
echo
echo "state/ and logs/ are untouched, so a later install.sh resumes rather than"
echo "re-syncing everything. Films already added to your Plex watchlist stay"
echo "there — this only stops the syncing."
