# Letterplexd

Letterplexd is a small Mac utility that adds films from your Letterboxd
watchlist to your Plex watchlist, on a schedule.

It syncs to your **Plex account** watchlist—the cross-server "Discover"
list—so films don't need to exist in any of your libraries.

## Requirements

macOS and Python 3. No Letterboxd account access or API key needed—it reads
your public watchlist page. A Plex API token is needed (it's easy to get;
instructions are included below).

## Setup

```bash
git clone https://github.com/mrgan/letterplexd.git
cd letterplexd
./install.sh
```

The first run creates `.env` and stops so you can fill in two values:

| Variable | Where to find it |
|---|---|
| `LETTERBOXD_USERNAME` | Your Letterboxd profile URL: `letterboxd.com/<username>/` |
| `PLEX_TOKEN` | See below |

With those filled in, preview what would happen:

```bash
./venv/bin/python sync.py --dry-run
```

That prints what it would add without touching your Plex watchlist or saving
any state. Do this before installing, because **there's no bulk undo**—Plex
has no "remove all" in its UI, so unwinding a large sync means scripting it. If
the list looks too long, see `WATCHLIST_LIMIT` below.

When you're happy with it:

```bash
./install.sh
```

This time it installs a launchd agent that syncs every 4 hours, and syncs once
immediately.

### Getting a Plex token

Sign in at `app.plex.tv`, open any library item's `⋯` menu → **Get Info** →
**View XML**, and copy the `X-Plex-Token=...` value out of the URL.

Treat it like a password—it grants full account access. `.env` is gitignored.

## Settings

All optional, in `.env`. Defaults are in `.env.example`.

| Variable | Default | What it does |
|---|---|---|
| `WATCHLIST_LIMIT` | `0` (all) | Sync only the N most recently added films |
| `YEAR_TOLERANCE` | `1` | How far a Plex result's year may differ and still count as the same film |
| `UNMATCHED_RETRY_DAYS` | `14` | Days before retrying a film Plex couldn't match |
| `HEARTBEAT_DAYS` | `30` | Days of silence before it posts a "still running" notification |
| `NOTIFY` | `1` | Set to `0` to silence notifications |

`WATCHLIST_LIMIT` exists for old/stale watchlists. If yours has hundreds of films you
added years ago and no longer care about, set it to the number of recent
additions you actually want; everything older is ignored. New films always enter
at the top of the list, so they still get synced.

## Notifications

It tells you when films are added, when a run fails, and once a month
regardless—that way you're less likely to forget this is running.

## Stopping it

```bash
./uninstall.sh
```

Nothing is scheduled after that. Films already added to Plex stay there, and
your sync history is kept, so reinstalling resumes rather than starting over.

## Caveats

- One-way. Adding a film on Letterboxd adds it to Plex; removing it doesn't
  remove it.
- Matching is by title and year against Plex's search, not by TMDb ID, so some
  films won't match—usually ones Plex lists under a different title. They're
  logged and retried periodically.
- Letterboxd no longer publishes an RSS feed of your watchlist, so Letterplexd
  parses Letterboxd's HTML, which could break if Letterboxd changes their
  markup. A run that scrapes nothing is treated as a failure and notifies you,
  rather than silently doing nothing.
- I use Nova as the code editor, so Nova tasks are included.

`CLAUDE.md` has the design notes and the reasoning behind all of the above.

## License

[0BSD](LICENSE). Do whatever you like with it—no attribution needed.
