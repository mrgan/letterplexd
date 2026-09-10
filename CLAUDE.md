# Letterplexd

One-way sync from a Letterboxd watchlist to a Plex account watchlist (the
cross-server "Discover" watchlist tied to your Plex account, not a specific
server's library).

The name is **Letterplexd** throughout — the notifications, the app bundle,
the directory, the git repo, the launchd label `com.neven.letterplexd`, and
the notifier's bundle identifier `com.neven.letterplexd.notifier`. It was
briefly `letterboxd-plex-sync`; if you find that slug anywhere, it's a leftover.

## Why / approach

- **Source**: scraped from the public per-user watchlist pages
  (`https://letterboxd.com/<username>/watchlist/page/<n>/`). No login or API
  key needed — the pages are public. Letterboxd removed the watchlist RSS feed
  that this project originally relied on (it now 404s), and its Cloudflare
  front actively challenges non-browser requests to most other routes
  (including the per-film `/film/<slug>/json/` endpoint) — but the watchlist
  page itself loads fine for a plain `requests` call as long as a normal
  browser `User-Agent` is sent. Title and year are read directly from each
  poster's `data-item-full-display-name="Title (Year)"` attribute, so no
  per-film requests are needed. Pagination continues until a page comes back
  with no poster entries.
- **Destination**: Plex account watchlist, via `plexapi`'s `MyPlexAccount`.
  Matching happens against Plex's Discover search (`account.searchDiscover(...)`),
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
letterplexd/
  sync.py                  # main script
  requirements.txt
  .env.example             # copy to .env and fill in
  notifier/
    notifier.applescript   # source for the notification app bundle
    build.sh               # compiles it; the built .app is gitignored
    Letterplexd.icon       # Icon Composer source
    Assets.car             # that source compiled by Xcode (a build input)
    icon-1024.png          # flat export, for the .icns fallback
  state/synced.json        # created at runtime, gitignored
  state/meta.json          # notification timing, created at runtime
  logs/sync.log            # created at runtime, gitignored
  com.neven.letterplexd.plist   # launchd template
```

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `LETTERBOXD_USERNAME` | Your Letterboxd username (from the profile URL) |
| `PLEX_TOKEN` | Your Plex account auth token (see below) |
| `STATE_FILE` | Path to the JSON state file (default `state/synced.json`) |
| `LOG_FILE` | Path to the log file (default `logs/sync.log`) |
| `UNMATCHED_RETRY_DAYS` | Days before re-attempting a previously unmatched film (default `14`) |
| `MATCH_SCORE_THRESHOLD` | Minimum title-similarity score (0-1) to accept a fuzzy match when titles don't line up exactly (default `0.9`) |
| `YEAR_TOLERANCE` | How many years a Plex result may differ from the Letterboxd year and still be considered the same film (default `1`) |
| `WATCHLIST_LIMIT` | Sync only the N most recently added films; `0` or unset syncs the whole list (default `0`) |
| `META_FILE` | Path to the run-metadata JSON used for notification timing (default `state/meta.json`) |
| `NOTIFY` | `0` to silence macOS notifications (default `1`) |
| `HEARTBEAT_DAYS` | Days of silence before posting a "still running" notification (default `30`) |

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

For each Letterboxd entry (title + year parsed from the scraped watchlist page):

1. Skip if already present (and marked matched, or unmatched within the
   retry cooldown) in the state file.
2. Query `account.searchDiscover(title, libtype="movie")` against Plex Discover.
3. Discard any result whose year differs from the Letterboxd year by more than
   `YEAR_TOLERANCE`. This filter comes first and applies to every later step —
   a title match alone is not enough, because same-titled remakes are common
   (`The Uninvited` 1944 vs. 2009, `Boy` 1969 vs. 2010). A Plex result with no
   year is discarded too when the Letterboxd entry has one — a title match
   alone isn't evidence. If Letterboxd has no year, there's nothing to filter
   on and everything passes.
4. Among the surviving candidates, prefer an exact case-insensitive title match
   with an exactly matching year.
5. Otherwise take an exact case-insensitive title match within the tolerance.
6. Otherwise accept the best candidate if its title similarity score is above
   `MATCH_SCORE_THRESHOLD`.
7. Otherwise mark as unmatched and log it — including, when everything was
   rejected on year, the closest result Plex did return, so near-misses can be
   eyeballed in the log.
8. On a match, call `account.addToWatchlist(...)`; on failure (already on
   watchlist, network error, etc.) log and continue.

The tolerance exists because Letterboxd and TMDb (which backs Plex Discover)
routinely disagree by a year on release dates — festival premiere vs. general
release. Widening it past 1 starts letting remakes back in.

## Limiting to recent additions

A long-lived watchlist accumulates films you no longer care about, so
`WATCHLIST_LIMIT` syncs only the N most recently added and ignores the rest.

The watchlist page carries no date-added attribute — the posters expose only
name, slug, link, and a `data-postered-identifier` blob — and the explicit sort
routes (`/watchlist/by/added/`) return 403 to a plain `requests` call, so
ordering can't be requested or read directly. It's inferred instead: mean
Letterboxd film ID falls steadily from ~284k in the first fifth of the list to
~82k in the last, while mean release year barely moves (1989 → 1984). Since
the list is sorted by neither film ID nor release year (about half the steps
descend in each, i.e. no better than chance), that gradient is best explained
by date-added ordering, newest first — recent additions skew toward
recently-catalogued films.

This is inference, not a documented contract. If Letterboxd changes its default
sort, `WATCHLIST_LIMIT` would silently start keeping the wrong end of the list.
Re-run the check in `logs/watchlist-order.md` if results look off.

The limit is a *window on the newest N*, not a one-time "drop the bottom
780" — that distinction matters as the list grows. Because the state file
records everything already synced, films sliding out of the bottom of the
window have been synced already, and new additions always enter at the top and
get picked up. The window only fails if more than N films are added between two
runs, which at the default 4-hour schedule is not a realistic concern.

The limit also stops pagination early, so a small limit makes runs much faster
(5 pages instead of 33 at `WATCHLIST_LIMIT=132`).

## Staying aware of it

This runs unattended every 4 hours for months at a time, which creates two
problems that look nothing alike but have the same fix.

The obvious one is forgetting it exists. The dangerous one is **silent
breakage**: the sync depends on scraping Letterboxd's HTML, and if that markup
changes, `fetch_letterboxd_watchlist` matches no posters and returns an empty
list. Without a guard that run does nothing, reports `matched=0`, and exits
`0` — indistinguishable from a healthy run with nothing new to do. The
watchlist would quietly stop syncing while every available signal said it was
fine. So an empty scrape is treated as a failure, not as an empty watchlist.
The cost is that a genuinely emptied watchlist reports an error, which is the
right trade at the frequency these two things actually happen.

Both problems are answered by having the job speak for itself. It notifies:

- **when films are added** — the useful case, and proof it's alive;
- **on failure** — unreachable Letterboxd, an empty scrape, a rejected Plex
  token, or any unhandled crash, each rate-limited to one notification per day
  so a persistent break doesn't train you to ignore them;
- **on a heartbeat** — after `HEARTBEAT_DAYS` of silence it says "still
  running", names the film count, and prints its own directory.

`state/meta.json` tracks `last_spoke_at` for this. *Any* notification resets
that clock, so the heartbeat only fires when the job has genuinely had nothing
to say — you hear from it roughly monthly at worst, not monthly on top of
everything else.

The heartbeat naming the project directory is deliberate: months from now the
notification itself should be enough to find and stop this thing, without
remembering it was launchd or hunting through `~/Library/LaunchAgents`.

Notifications are suppressed under `--dry-run`.

### Why there's an app bundle

macOS attributes a notification to the bundle of the process that posts it, so
a plain `osascript` call shows up as **Script Editor** with Script Editor's
icon — an app you didn't run, named above an alert meant to explain itself
months later. `notifier/` solves that: a tiny AppleScript applet compiled into
`Letterplexd.app`, whose `Info.plist` carries our own bundle name and
identifier. `sync.py` runs its `Contents/MacOS/applet` directly, so the
notification inherits *that* identity.

Build (or rebuild, after editing the script) with:

```bash
./notifier/build.sh
```

The built `.app` is gitignored — only the AppleScript source and build script
are checked in, the same way `venv/` is rebuilt rather than committed. If it's
missing, `notify()` logs a warning and falls back to plain `osascript`, so an
unbuilt bundle costs you the nice name, not the notification.

The text passes through `LPS_TITLE` / `LPS_SUBTITLE` / `LPS_MESSAGE`
environment variables rather than being interpolated into an AppleScript
string, which sidesteps quoting bugs on film titles containing apostrophes or
quotes.

That introduces an encoding trap worth knowing about, because it is silent and
looks like a font problem. AppleScript's `system attribute` decodes the
environment as **Mac OS Roman**, not UTF-8, so anything non-ASCII arrives
mangled: `→` renders as `,Üí`, `·` as `¬∑`, and *La cérémonie* as
*La c√©r√©monie*. The applet therefore reads each variable back through
`do shell script "printf '%s' \"$VAR\""`, which returns real UTF-8. Since the
shell does not re-expand an expanded value, titles containing `$` or backticks
(*Ca$h*, or the 1971 film literally titled *$*) stay literal.

If notifications ever start showing `√` and `Ü` sequences, this is the cause —
check `utf8Env` in `notifier.applescript` rather than the sending side.

### The app icon

The bundle carries **two** icons, which is what Apple's own apps do — Calculator,
Notes and Music on macOS 27 all ship both:

| File | Info.plist key | Used by |
|---|---|---|
| `Assets.car` | `CFBundleIconName` | macOS 26 (Tahoe) and later — the layered Icon Composer icon, rendered live with its glass, refraction and dark variant |
| `AppIcon.icns` | `CFBundleIconFile` | Anything older, which ignores `CFBundleIconName` entirely |

`build.sh` installs both, and `notifier/` holds all three inputs:

- `Letterplexd.icon` — the Icon Composer source, for editing the design.
- `Assets.car` — that source *compiled*. Committed deliberately, see below.
- `icon-1024.png` — a flat 1024×1024 export, from which `build.sh` generates the
  ten iconset sizes with `sips` and packs them via `iconutil`.

**Why the compiled `Assets.car` is committed rather than built here.** A `.icon`
can only be compiled by Xcode's asset pipeline. `actool` refuses it from the
command line — passed directly it reports `Could not open` and then throws an
internal exception; placed inside an `.xcassets` it compiles silently and emits
nothing. The working route is a throwaway Xcode macOS App project with the
`.icon` assigned as the target's app icon; `Assets.car` is then lifted out of
the build product. That's a manual step nobody should have to repeat, so its
output is committed as a build input (1.9 MB).

To change the icon: edit `Letterplexd.icon` in Icon Composer, rebuild it through
an Xcode project, replace `Assets.car`, export a fresh flat PNG over
`icon-1024.png`, and run `./notifier/build.sh`.

One ordering detail that bit once: `osacompile` leaves its own generic
`applet.icns` behind as `CFBundleIconFile`. `build.sh` deletes that key and file
*before* installing either icon, otherwise the stale applet icon competes for
what macOS actually displays.

## Known limitations

- One-way only: removing a film from Letterboxd does not remove it from
  the Plex watchlist.
- Matching is title/year based (Plex Discover search), not TMDb-ID based,
  since the scraped watchlist page doesn't expose TMDb IDs.
- The watchlist page only exposes what's currently on the watchlist, so
  there's no way to distinguish "removed" from "never added" — the state
  file only ever grows.
- Relies on scraping Letterboxd's HTML (specifically the
  `data-item-full-display-name` attribute on each poster), which could break
  if Letterboxd changes its markup or tightens Cloudflare's bot rules further.

## Running manually

```bash
cd /Users/neven/Developer/letterplexd
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env
python sync.py --dry-run   # preview without touching your Plex watchlist
python sync.py             # actually sync
```

## Scheduling with launchd

See `com.neven.letterplexd.plist`. Install with:

```bash
cp com.neven.letterplexd.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.neven.letterplexd.plist
```

`RunAtLoad` means bootstrapping runs the script once immediately, which
doubles as a check that it works under launchd's much thinner environment.

Note that `logs/launchd.err.log` collects the script's *normal* output, not
just errors: Python's `StreamHandler` defaults to stderr, so every `INFO` line
lands there and `launchd.out.log` stays empty. A non-empty `.err.log` is not a
problem on its own — read it before worrying.

Check on it with:

```bash
launchctl print gui/$(id -u)/com.neven.letterplexd | grep -E "state =|last exit code|runs ="
```

## TODO

Most of these are code changes an assistant can make on request — they're
listed here because each needs a decision, a design eye, or a machine that
isn't this one.

### Needs you specifically

- [x] ~~**Design an app icon.**~~ Done — see "The app icon" above.
- [ ] **Check Notification Center settings** for "Letterplexd" (System
  Settings → Notifications). Worth doing once, deliberately: if alerts are set
  to "none", or Focus filters them out, the monthly heartbeat is silently
  swallowed — and a heartbeat you never see is worse than none, because
  silence then reads as "still fine".
- [ ] **Decide about `.nova/`.** Editor task configs are committed. Harmless,
  but they're personal tooling, not part of the project.

### Before sharing it

- [ ] **Write a README.** This file is maintainer notes — it explains *why*
  things are the way they are and assumes you already own the project. A
  friend needs the short version: what it does, how to install it, what to put
  in `.env`. This is the biggest gap between "works" and "shareable".
- [ ] **Make the launchd plist portable.** It hardcodes five
  `/Users/neven/...` paths and a `com.neven.letterplexd` label, so
  nobody else can use it as-is. Either generate it from a template at install
  time, or ship an `install.sh` that substitutes `$PWD` and `$USER`. This is
  the one thing that actually blocks a friend from running this.
- [ ] **De-personalize the docs.** "Running manually" above still opens with
  `cd /Users/neven/Developer/letterplexd`.
- [ ] **Note that `WATCHLIST_LIMIT` is a personal setting.** `.env` has `132`
  because this particular watchlist had gone stale; a new user almost
  certainly wants `0`. `.env.example` already defaults correctly, but the
  README should say why the knob exists.
- [ ] **Add a LICENSE** if the repo goes public.
- [ ] **Test from a clean clone** — fresh directory, new venv, empty state
  file, `.env` written from scratch. It's the only way to find the setup step
  that only works here because of something already on this machine.
- [ ] **Flip the repo to public** when ready:
  `gh repo edit mrgan/letterplexd --visibility public`.

Uninstall / stop with:

```bash
launchctl bootout gui/$(id -u)/com.neven.letterplexd
rm ~/Library/LaunchAgents/com.neven.letterplexd.plist
```
