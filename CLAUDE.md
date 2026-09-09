# letterboxd-plex-sync

One-way sync from a Letterboxd watchlist to a Plex account watchlist (the
cross-server "Discover" watchlist tied to your Plex account, not a specific
server's library).

## Why / approach

- **Source**: Letterboxd's public per-user RSS feed
  (`https://letterboxd.com/<username>/watchlist/rss/`). No login or API key
  needed — this is a public, unauthenticated feed.
- **Destination**: Plex account watchlist, via `plexapi`'s `MyPlexAccount`.
  Matching happens against Plex's Discover search (`account.search(...)`),
  which is server-independent — the film doesn't need to exist in any of
  your Plex libraries to be added to the watchlist.
- **State**: a local JSON file (`state/synced.json`) tracks every film
  already processed (matched or not), keyed by a normalized
  `title (year)` string, so re-runs only touch new watchlist entries.
- **Failure mode**: unmatched/ambiguous films are logged and recorded as
  "unmatched" in the state file (with a retry cooldown), never raise —
  one bad match can't crash the run.
- **Scheduling**: a macOS `launchd` agent runs the script periodically
  (default every 4 hours) instead of a long-lived process or Docker
  container, since this is a small, infrequent, local task.

## Layout

```
letterboxd-plex-sync/
  sync.py                  # main script
  requirements.txt
  .env.example             # copy to .env and fill in
  state/synced.json        # created at runtime, gitignored
  logs/sync.log            # created at runtime, gitignored
  com.neven.letterboxd-plex-sync.plist   # launchd template
```

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `LETTERBOXD_USERNAME` | Your Letterboxd username (from the profile URL) |
| `PLEX_TOKEN` | Your Plex account auth token (see below) |
| `STATE_FILE` | Path to the JSON state file (default `state/synced.json`) |
| `LOG_FILE` | Path to the log file (default `logs/sync.log`) |
| `UNMATCHED_RETRY_DAYS` | Days before re-attempting a previously unmatched film (default `14`) |
| `MATCH_SCORE_THRESHOLD` | Minimum title-similarity score (0-1) to accept a fuzzy match when years don't line up (default `0.9`) |

### Getting a Plex token

1. Sign in to `app.plex.tv` in a browser.
2. Open any item's "Get Info" / "..." menu → **View XML** (or **Get Info**
   → the info panel has a link at the bottom), and look at the request URL
   — it contains `X-Plex-Token=...`.
   - Easier: go to a library item in Plex Web, click the `⋯` menu →
     **Get Info** → **View XML**, and copy the token from the URL bar.
3. Alternatively use the officially documented method: sign in at
   `plex.tv`, open browser dev tools → Network tab, reload, and find any
   request to `plex.tv` or your server containing `X-Plex-Token`.
4. Paste the token into `.env` as `PLEX_TOKEN`. Treat it like a password —
   it grants full account access. `.env` is gitignored.

## Matching logic

For each Letterboxd entry (title + year parsed from the RSS feed):

1. Skip if already present (and marked matched, or unmatched within the
   retry cooldown) in the state file.
2. Query `account.search(title, mediatype="movie")` against Plex Discover.
3. Prefer an exact case-insensitive title match with matching year.
4. Otherwise accept the top result if its title similarity score is above
   `MATCH_SCORE_THRESHOLD`.
5. Otherwise mark as unmatched and log it.
6. On a match, call `account.addToWatchlist(...)`; on failure (already on
   watchlist, network error, etc.) log and continue.

## Known limitations

- One-way only: removing a film from Letterboxd does not remove it from
  the Plex watchlist.
- Matching is title/year based (Plex Discover search), not TMDb-ID based,
  since the public RSS feed doesn't reliably expose TMDb IDs.
- Letterboxd's public RSS only exposes what's currently on the watchlist,
  so there's no way to distinguish "removed" from "never added" — the
  state file only ever grows.

## Running manually

```bash
cd /Users/neven/Developer/letterboxd-plex-sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env
python sync.py --dry-run   # preview without touching your Plex watchlist
python sync.py             # actually sync
```

## Scheduling with launchd

See `com.neven.letterboxd-plex-sync.plist`. Install with:

```bash
cp com.neven.letterboxd-plex-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.neven.letterboxd-plex-sync.plist
```

Uninstall / stop with:

```bash
launchctl bootout gui/$(id -u)/com.neven.letterboxd-plex-sync
rm ~/Library/LaunchAgents/com.neven.letterboxd-plex-sync.plist
```
