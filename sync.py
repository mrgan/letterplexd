#!/usr/bin/env python3
"""Sync a Letterboxd watchlist (scraped from the public watchlist pages) to a
Plex account watchlist.

See CLAUDE.md for the full design notes.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount

BASE_DIR = Path(__file__).resolve().parent

log = logging.getLogger("letterplexd")


@dataclass
class WatchlistEntry:
    title: str
    year: Optional[int]
    letterboxd_url: str

    @property
    def key(self) -> str:
        return normalize_key(self.title, self.year)


def normalize_key(title: str, year: Optional[int]) -> str:
    slug = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return f"{slug} ({year})" if year else slug


def load_config() -> dict:
    load_dotenv(BASE_DIR / ".env")

    username = os.environ.get("LETTERBOXD_USERNAME")
    token = os.environ.get("PLEX_TOKEN")
    missing = [
        name
        for name, val in (("LETTERBOXD_USERNAME", username), ("PLEX_TOKEN", token))
        if not val
    ]
    if missing:
        sys.exit(
            f"Missing required config: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill it in."
        )

    return {
        "username": username,
        "token": token,
        "state_file": BASE_DIR / os.environ.get("STATE_FILE", "state/synced.json"),
        "log_file": BASE_DIR / os.environ.get("LOG_FILE", "logs/sync.log"),
        "unmatched_retry_days": float(os.environ.get("UNMATCHED_RETRY_DAYS", "14")),
        "match_score_threshold": float(os.environ.get("MATCH_SCORE_THRESHOLD", "0.9")),
        "year_tolerance": int(os.environ.get("YEAR_TOLERANCE", "1")),
        "watchlist_limit": int(os.environ.get("WATCHLIST_LIMIT", "0")),
        "meta_file": BASE_DIR / os.environ.get("META_FILE", "state/meta.json"),
        "notify": os.environ.get("NOTIFY", "1") not in ("0", "false", "no"),
        "heartbeat_days": float(os.environ.get("HEARTBEAT_DAYS", "30")),
    }


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    log.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.addHandler(console_handler)


LETTERBOXD_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def fetch_letterboxd_watchlist(username: str, limit: int = 0) -> list[WatchlistEntry]:
    # Letterboxd dropped the public watchlist RSS feed; the watchlist page itself
    # is still public, so we scrape it (paginated) instead. Its Cloudflare front
    # only challenges non-browser clients on other routes (e.g. /film/*/json/),
    # not this page, as long as a normal browser User-Agent is sent.
    #
    # The page lists films newest-added first, so `limit` keeps the N most
    # recently added and stops paging early. See CLAUDE.md "Limiting to recent
    # additions" for why that ordering is inferred rather than given.
    session = requests.Session()
    session.headers.update({"User-Agent": LETTERBOXD_USER_AGENT})

    entries = []
    page = 1
    while True:
        url = f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        resp = session.get(url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content, "html.parser")
        posters = soup.select('[data-component-class="LazyPoster"]')
        if not posters:
            break

        for poster in posters:
            display_name = poster.get("data-item-full-display-name", "").strip()
            slug = poster.get("data-item-slug", "")

            match = re.match(r"^(.*)\s\((\d{4})\)$", display_name)
            if match:
                title, year = match.group(1).strip(), int(match.group(2))
            else:
                title, year = display_name, None

            entries.append(
                WatchlistEntry(
                    title=title,
                    year=year,
                    letterboxd_url=f"https://letterboxd.com/film/{slug}/" if slug else "",
                )
            )

            if limit and len(entries) >= limit:
                return entries

        page += 1
        time.sleep(1)  # be polite between page requests

    return entries


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r") as f:
            return json.load(f)
    except ValueError as exc:
        log.warning("Ignoring unreadable %s: %s", path.name, exc)
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


NOTIFY_TITLE = "Letterplexd"
NOTIFIER_APP = BASE_DIR / "notifier" / "Letterplexd.app" / "Contents" / "MacOS" / "applet"
notifications_enabled = True


def notify(headline: str, message: str) -> None:
    # This runs unattended for months, so it has to be able to speak up: on new
    # films, on failure, and on a slow heartbeat. See CLAUDE.md "Staying aware
    # of it". A notification failing must never take a sync down with it.
    if not notifications_enabled:
        return

    try:
        if NOTIFIER_APP.exists():
            # Notifications inherit the posting process's bundle identity, so
            # going through our own app makes them read as "Letterplexd" rather
            # than "Script Editor". macOS already prints that name above the
            # alert, so the headline goes in the title and the subtitle is left
            # empty rather than repeating the name back.
            env = {
                **os.environ,
                "LPS_TITLE": headline,
                "LPS_SUBTITLE": "",
                "LPS_MESSAGE": message,
            }
            subprocess.run([str(NOTIFIER_APP)], env=env, capture_output=True, timeout=10)
        else:
            # Unbuilt bundle shouldn't cost us the notification entirely. Here
            # the alert wears Script Editor's name, so the title has to carry
            # ours and the headline drops to the subtitle.
            log.warning("Notifier app not built; falling back to osascript")
            script = (
                f'display notification {applescript_string(message)} '
                f'with title {applescript_string(NOTIFY_TITLE)} '
                f'subtitle {applescript_string(headline)}'
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not post notification: %s", exc)


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def short_reason(exc: Exception) -> str:
    # Notifications are three lines at most, so they get the gist; the full
    # error is already in the log for whoever goes looking.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        return f"HTTP {status}"
    return type(exc).__name__


def home_relative(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def should_skip(entry_state: Optional[dict], unmatched_retry_days: float) -> bool:
    if entry_state is None:
        return False
    if entry_state.get("status") == "matched":
        return True
    if entry_state.get("status") == "unmatched":
        last_tried = entry_state.get("last_tried_at", 0)
        return (time.time() - last_tried) < unmatched_retry_days * 86400
    return False


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def years_compatible(plex_year: Optional[int], entry_year: Optional[int], tolerance: int) -> bool:
    # A year we know needs a year to confirm it against: a title match alone is
    # not evidence, since same-titled films are exactly the case being guarded.
    if entry_year is None:
        return True
    if plex_year is None:
        return False
    return abs(plex_year - entry_year) <= tolerance


def find_best_match(
    account: MyPlexAccount,
    entry: WatchlistEntry,
    score_threshold: float,
    year_tolerance: int,
):
    try:
        results = account.searchDiscover(entry.title, libtype="movie", limit=10)
    except Exception as exc:  # noqa: BLE001 - Discover search can fail in many ways
        log.warning("Plex search failed for %r: %s", entry.title, exc)
        return None

    if not results:
        return None

    # Same-titled remakes are common, so a title match alone isn't enough: discard
    # anything whose year is too far off before considering it at all. Letterboxd
    # and Plex routinely disagree by a year on release dates, hence the tolerance.
    candidates = [
        r for r in results
        if years_compatible(getattr(r, "year", None), entry.year, year_tolerance)
    ]

    if not candidates:
        closest = min(
            (r for r in results if getattr(r, "year", None) is not None),
            key=lambda r: abs(r.year - entry.year) if entry.year else 0,
            default=None,
        )
        if closest is not None:
            log.info(
                "Rejected %r (%s) on year: closest Plex result was %r (%s)",
                entry.title, entry.year, closest.title, closest.year,
            )
        return None

    # Prefer an exact (case-insensitive) title match with matching year.
    for r in candidates:
        if r.title.lower() == entry.title.lower() and getattr(r, "year", None) == entry.year:
            return r

    # Otherwise, an exact title match within the year tolerance.
    for r in candidates:
        if r.title.lower() == entry.title.lower():
            return r

    # Otherwise, best fuzzy match above threshold.
    best = max(candidates, key=lambda r: title_similarity(r.title, entry.title))
    if title_similarity(best.title, entry.title) >= score_threshold:
        return best

    return None


def sync(dry_run: bool = False) -> int:
    global notifications_enabled

    config = load_config()
    setup_logging(config["log_file"])
    notifications_enabled = config["notify"] and not dry_run

    log.info("Starting sync (dry_run=%s)", dry_run)

    meta = load_json(config["meta_file"])

    try:
        watchlist = fetch_letterboxd_watchlist(
            config["username"], config["watchlist_limit"]
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to fetch Letterboxd watchlist: %s", exc)
        report_failure(config, meta, f"Couldn't reach Letterboxd ({short_reason(exc)})", dry_run)
        return 1

    # An empty scrape is the shape a Letterboxd markup change takes: no posters
    # match, so the fetch returns nothing and the run would otherwise "succeed"
    # having done nothing at all. Treat it as the failure it almost certainly is.
    if not watchlist:
        log.error(
            "Letterboxd returned no films — the page markup may have changed, "
            "or the watchlist is genuinely empty"
        )
        report_failure(
            config, meta, "Letterboxd returned no films; the scraper may need updating", dry_run
        )
        return 1

    if config["watchlist_limit"]:
        log.info(
            "Fetched %d films from Letterboxd watchlist (newest %d only)",
            len(watchlist), config["watchlist_limit"],
        )
    else:
        log.info("Fetched %d films from Letterboxd watchlist", len(watchlist))

    try:
        account = MyPlexAccount(token=config["token"])
    except Exception as exc:  # noqa: BLE001 - an expired token surfaces here
        log.error("Could not sign in to Plex: %s", exc)
        report_failure(
            config, meta, f"Plex sign-in failed ({short_reason(exc)}); check PLEX_TOKEN", dry_run
        )
        return 1

    state = load_json(config["state_file"])

    matched = skipped = unmatched = failed = 0
    added_titles = []

    for entry in watchlist:
        key = entry.key
        if should_skip(state.get(key), config["unmatched_retry_days"]):
            skipped += 1
            continue

        match = find_best_match(
            account,
            entry,
            config["match_score_threshold"],
            config["year_tolerance"],
        )

        if match is None:
            log.info("No confident match for %r (%s)", entry.title, entry.year)
            state[key] = {
                "status": "unmatched",
                "title": entry.title,
                "year": entry.year,
                "last_tried_at": time.time(),
            }
            unmatched += 1
            continue

        if dry_run:
            log.info(
                "[dry-run] Would add %r (%s) -> Plex %r (%s)",
                entry.title, entry.year, match.title, getattr(match, "year", None),
            )
            matched += 1
            continue

        try:
            account.addToWatchlist(match)
            log.info(
                "Added %r (%s) -> Plex %r (%s)",
                entry.title, entry.year, match.title, getattr(match, "year", None),
            )
            state[key] = {
                "status": "matched",
                "title": entry.title,
                "year": entry.year,
                "plex_title": match.title,
                "plex_year": getattr(match, "year", None),
                "plex_rating_key": getattr(match, "ratingKey", None),
                "synced_at": time.time(),
            }
            matched += 1
            added_titles.append(entry.title)
        except (BadRequest, NotFound) as exc:
            # e.g. already on the watchlist
            log.info("Could not add %r (already on watchlist? %s)", entry.title, exc)
            state[key] = {
                "status": "matched",
                "title": entry.title,
                "year": entry.year,
                "plex_title": match.title,
                "note": str(exc),
                "synced_at": time.time(),
            }
            matched += 1
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to add %r to watchlist: %s", entry.title, exc)
            failed += 1

    if not dry_run:
        save_json(config["state_file"], state)

    log.info(
        "Sync complete: matched=%d skipped=%d unmatched=%d failed=%d",
        matched, skipped, unmatched, failed,
    )

    if not dry_run:
        total_synced = sum(1 for v in state.values() if v.get("status") == "matched")
        report_success(config, meta, added_titles, total_synced)

    return 0


def report_success(config: dict, meta: dict, added_titles: list, total_synced: int) -> None:
    now = time.time()
    meta["last_run_at"] = now
    meta.pop("failing_since", None)
    meta.setdefault("last_spoke_at", now)

    if added_titles:
        preview = ", ".join(added_titles[:3])
        if len(added_titles) > 3:
            preview += f", and {len(added_titles) - 3} more"
        plural = "s" if len(added_titles) != 1 else ""
        notify(f"Added {len(added_titles)} film{plural} to your watchlist", preview)
        meta["last_spoke_at"] = now
        meta["last_added_at"] = now

    elif now - meta["last_spoke_at"] >= config["heartbeat_days"] * 86400:
        # The point of the heartbeat: a healthy, quiet job is indistinguishable
        # from a dead one unless it says something on its own schedule.
        quiet_days = int((now - meta["last_spoke_at"]) / 86400)
        notify(
            f"Still running · {total_synced} films synced",
            f"Nothing new in {quiet_days} days. {home_relative(BASE_DIR)}",
        )
        meta["last_spoke_at"] = now

    save_json(config["meta_file"], meta)


def report_failure(config: dict, meta: dict, reason: str, dry_run: bool) -> None:
    if dry_run:
        return

    now = time.time()
    meta["last_run_at"] = now
    meta.setdefault("failing_since", now)

    # Broken at a 4-hour cadence would mean six notifications a day, which
    # teaches you to ignore them. One a day is enough to stay noticed.
    last_told = meta.get("last_failure_notified_at", 0)
    if now - last_told >= 86400:
        down_days = int((now - meta["failing_since"]) / 86400)
        detail = f"Failing for {down_days} days. " if down_days else ""
        notify("Sync is failing", f"{detail}{reason}")
        meta["last_failure_notified_at"] = now
        meta["last_spoke_at"] = now

    save_json(config["meta_file"], meta)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without modifying the Plex watchlist or state file",
    )
    args = parser.parse_args()

    try:
        sys.exit(sync(dry_run=args.dry_run))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a crash must not be a silent crash
        log.exception("Sync crashed")
        notify("Sync crashed", f"{type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
