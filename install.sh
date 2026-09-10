#!/bin/bash
# Sets Letterplexd up to sync on a schedule: creates the virtualenv, builds the
# notifier app, generates a launchd plist for wherever this checkout happens to
# live, and loads it. Safe to re-run — it replaces any agent it already installed.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# Overridable so two checkouts can coexist, and so the interval is tunable
# without editing a generated file.
LABEL="${LETTERPLEXD_LABEL:-com.letterplexd}"
INTERVAL="${LETTERPLEXD_INTERVAL:-14400}"

PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$DIR/venv/bin/python3"

if [ ! -x "$PYTHON" ]; then
	echo "Creating virtualenv..."
	python3 -m venv "$DIR/venv"
	"$DIR/venv/bin/pip" install --quiet --requirement "$DIR/requirements.txt"
fi

# Stop before installing a scheduled job that can only fail: without
# credentials every run would error, and the failure notification would be the
# first you heard of it.
if [ ! -f "$DIR/.env" ]; then
	cp "$DIR/.env.example" "$DIR/.env"
	echo
	echo "Created .env — fill in LETTERBOXD_USERNAME and PLEX_TOKEN, then re-run." >&2
	exit 1
fi

for required in LETTERBOXD_USERNAME PLEX_TOKEN; do
	if ! grep -qE "^$required=.+" "$DIR/.env"; then
		echo "$required is empty in .env. Fill it in, then re-run." >&2
		exit 1
	fi
done

# Without this the notifications fall back to osascript and wear Script
# Editor's name and icon instead of ours.
if [ ! -d "$DIR/notifier/Letterplexd.app" ]; then
	echo "Building the notifier app..."
	"$DIR/notifier/build.sh"
fi

mkdir -p "$DIR/logs" "$HOME/Library/LaunchAgents"

sed -e "s|__LABEL__|$LABEL|g" \
	-e "s|__DIR__|$DIR|g" \
	-e "s|__INTERVAL__|$INTERVAL|g" \
	"$DIR/letterplexd.plist.template" > "$PLIST"
plutil -lint "$PLIST" > /dev/null

# bootout first so re-running upgrades an existing install rather than failing.
launchctl bootout "gui/$(id -u)/$LABEL" 2> /dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Installed $LABEL — syncing every $((INTERVAL / 60)) minutes."
echo "RunAtLoad means it is running once now; watch it with:"
echo "  tail -f \"$DIR/logs/sync.log\""
echo
echo "Check on it later with:"
echo "  launchctl print gui/\$(id -u)/$LABEL | grep -E 'state =|last exit code|runs ='"
echo "Remove it with:"
echo "  $DIR/uninstall.sh"
