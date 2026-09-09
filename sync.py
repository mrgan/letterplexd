#!/usr/bin/env python3
"""Sync a Letterboxd watchlist (public RSS feed) to a Plex account watchlist.

See CLAUDE.md for the full design notes.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import feedparser
import requests
from dotenv import load_dotenv
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount

BASE_DIR = Path(__file__).resolve().parent

log = logging.getLogger("letterboxd_plex_sync")


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


def fetch_letterboxd_watchlist(username: str) -> list[WatchlistEntry]:
    url = f"https://letterboxd.com/{username}/watchlist/rss/"
    resp = requests.get(
        url,
        headers={"User-Agent": "letterboxd-plex-sync/1.0"},
        timeout=30,
    )
    resp.raise_for_status()

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Failed to parse Letterboxd RSS feed: {feed.bozo_exception}")

    entries = []
    for item in feed.entries:
        title = (
            item.get("letterboxd_filmtitle")
            or item.get("letterboxd_filmTitle")
            or item.get("title")
        )
        year_raw = item.get("letterboxd_filmyear") or item.get("letterboxd_filmYear")
        year = None
        if year_raw:
            try:
                year = int(year_raw)
            except ValueError:
                year = None
        if not year:
            # Fall back to parsing "Title (Year)" out of the plain title.
            match = re.match(r"^(.*)\((\d{4})\)$", title.strip())
            if match:
                title = match.group(1).strip()
                year = int(match.group(2))

        entries.append(
            WatchlistEntry(title=title.strip(), year=year, letterboxd_url=item.get("link", ""))
        )

    return entries


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    with state_file.open("r") as f:
        return json.load(f)


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(state_file)


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


def find_best_match(account: MyPlexAccount, entry: WatchlistEntry, score_threshold: float):
    try:
        results = account.search(entry.title, mediatype="movie", limit=10)
    except Exception as exc:  # noqa: BLE001 - Discover search can fail in many ways
        log.warning("Plex search failed for %r: %s", entry.title, exc)
        return None

    if not results:
        return None

    # Prefer an exact (case-insensitive) title match with matching year.
    for r in results:
        if r.title.lower() == entry.title.lower() and getattr(r, "year", None) == entry.year:
            return r

    # Otherwise, any exact title match regardless of year.
    for r in results:
        if r.title.lower() == entry.title.lower():
            return r

    # Otherwise, best fuzzy match above threshold.
    best = max(results, key=lambda r: title_similarity(r.title, entry.title))
    if title_similarity(best.title, entry.title) >= score_threshold:
        return best

    return None


def sync(dry_run: bool = False) -> int:
    config = load_config()
    setup_logging(config["log_file"])

    log.info("Starting sync (dry_run=%s)", dry_run)

    try:
        watchlist = fetch_letterboxd_watchlist(config["username"])
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to fetch Letterboxd watchlist: %s", exc)
        return 1

    log.info("Fetched %d films from Letterboxd watchlist", len(watchlist))

    account = MyPlexAccount(token=config["token"])
    state = load_state(config["state_file"])

    matched = skipped = unmatched = failed = 0

    for entry in watchlist:
        key = entry.key
        if should_skip(state.get(key), config["unmatched_retry_days"]):
            skipped += 1
            continue

        match = find_best_match(account, entry, config["match_score_threshold"])

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
        save_state(config["state_file"], state)

    log.info(
        "Sync complete: matched=%d skipped=%d unmatched=%d failed=%d",
        matched, skipped, unmatched, failed,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without modifying the Plex watchlist or state file",
    )
    args = parser.parse_args()
    sys.exit(sync(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
