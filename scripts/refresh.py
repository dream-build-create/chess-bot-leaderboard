"""Refresh every bot in the roster and write the leaderboard.

Runs once a day. Reads data/roster.json, updates data/history.json, and
writes docs/data/leaderboard.json. Both output files are written to temp
names and renamed only after everything succeeds.
"""

import json
import re
import sys
import time
from datetime import date, timedelta

import common

ROSTER_PATH = common.repo_path("data", "roster.json")
HISTORY_PATH = common.repo_path("data", "history.json")
LEADERBOARD_PATH = common.repo_path("docs", "data", "leaderboard.json")

SCHEMA_VERSION = 1
CATEGORIES = ("bullet", "blitz", "rapid", "classical")
RULES = {
    "activity_days": 7,
    "min_games": 50,
    "max_rd_standard": 75,
    "max_rd_variant": 65,
}
CHUNK_SIZE = 300          # /api/users maximum
HISTORY_KEEP_DAYS = 8
ENGINE_TAG = re.compile(r"engine:\s*(\S+)", re.IGNORECASE)


# ---------------------------------------------------------------- fetching

def fetch_users(ids):
    """POST ids to /api/users in chunks, one at a time. Returns {id: record}."""
    users = {}
    chunks = [ids[i:i + CHUNK_SIZE] for i in range(0, len(ids), CHUNK_SIZE)]
    for n, chunk in enumerate(chunks):
        if n:
            time.sleep(common.PAUSE_SECONDS)
        raw = common.request(
            "/api/users",
            method="POST",
            body=",".join(chunk),
            content_type="text/plain",
            accept="application/json",
        )
        records = json.loads(raw)
        if not isinstance(records, list):
            raise ValueError("/api/users did not return a JSON array")
        for rec in records:
            users[rec["id"].lower()] = rec
    return users


# ----------------------------------------------------------------- history

def game_counts(user):
    perfs = user.get("perfs") or {}
    counts = {}
    for cat in CATEGORIES:
        games = (perfs.get(cat) or {}).get("games")
        if isinstance(games, int):
            counts[cat] = games
    return counts


def record_snapshot(history, users, today):
    """Store today's game counts (replacing any earlier run today) and prune."""
    snapshots = history.setdefault("snapshots", {})
    snapshots[today] = {bot_id: c for bot_id, u in users.items() if (c := game_counts(u))}
    cutoff = (date.fromisoformat(today) - timedelta(days=HISTORY_KEEP_DAYS)).isoformat()
    for d in [d for d in snapshots if d < cutoff]:
        del snapshots[d]


def activity(history, bot_id, cat, today, games_today):
    """Return (active, last_active) for one (bot, category).

    Compares today's count with the latest snapshot dated activity_days or
    more ago, or the bot's oldest snapshot if none is that old. last_active
    is the most recent snapshot date on which the count had risen.
    """
    series = [
        (d, snap[bot_id][cat])
        for d, snap in sorted(history.get("snapshots", {}).items())
        if d < today and cat in snap.get(bot_id, {})
    ]
    if not series:
        return False, None

    cutoff = (date.fromisoformat(today) - timedelta(days=RULES["activity_days"])).isoformat()
    old_enough = [s for s in series if s[0] <= cutoff]
    reference = old_enough[-1] if old_enough else series[0]
    if games_today <= reference[1]:
        return False, None

    last_active = None
    full = series + [(today, games_today)]
    for (_, prev), (d, cur) in zip(full, full[1:]):
        if cur > prev:
            last_active = d
    return True, last_active


# ------------------------------------------------------------ leaderboard

def bio_fields(user):
    """Return (bio, stockfish, declared_engine) from the profile bio."""
    raw = (user.get("profile") or {}).get("bio") or ""
    tag = ENGINE_TAG.search(raw)
    bio = " ".join(raw.split())   # collapse newlines and runs of spaces
    return bio, "stockfish" in raw.lower(), tag.group(1) if tag else None


def qualifies(perf):
    return (
        perf is not None
        and not perf.get("prov", False)
        and perf.get("games", 0) >= RULES["min_games"]
        and isinstance(perf.get("rd"), int)
        and perf["rd"] < RULES["max_rd_standard"]
        and isinstance(perf.get("rating"), int)
    )


def build_leaderboard(users, history, today, generated_at):
    categories = {cat: [] for cat in CATEGORIES}
    for bot_id, user in users.items():
        if user.get("disabled") or user.get("tosViolation"):
            continue
        bio, stockfish, declared_engine = bio_fields(user)
        perfs = user.get("perfs") or {}
        for cat in CATEGORIES:
            perf = perfs.get(cat)
            if not qualifies(perf):
                continue
            active, last_active = activity(history, bot_id, cat, today, perf["games"])
            if not active:
                continue
            categories[cat].append({
                "rank": 0,
                "username": user["username"],
                "rating": perf["rating"],
                "rd": perf["rd"],
                "games": perf["games"],
                "last_active": last_active,
                "online": False,
                "stockfish": stockfish,
                "declared_engine": declared_engine,
                "bio": bio,
                "updated_at": generated_at,
            })

    for rows in categories.values():
        # Ties on rating: lower RD first, then name, so order is stable.
        rows.sort(key=lambda r: (-r["rating"], r["rd"], r["username"].lower()))
        for i, row in enumerate(rows, 1):
            row["rank"] = i

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "online_checked_at": None,
        "source": "Lichess public API",
        "rules": dict(RULES),
        "categories": categories,
    }


# -------------------------------------------------------------------- main

def run(now):
    today = now.date().isoformat()
    generated_at = common.iso_utc(now)

    roster = common.load_json(ROSTER_PATH, {"bots": {}})
    ids = sorted(roster.get("bots", {}))
    history = common.load_json(HISTORY_PATH, {"schema_version": 1, "snapshots": {}})

    try:
        users = fetch_users(ids)
    except (common.FetchError, ValueError, KeyError) as e:
        common.log(f"refresh failed, no files changed: {e}")
        return 1
    if ids and not users:
        common.log("refresh failed: roster is non-empty but no users came back")
        return 1

    record_snapshot(history, users, today)
    board = build_leaderboard(users, history, today, generated_at)
    online = roster.get("online") or {}
    common.apply_online(board, set(online.get("ids", [])), online.get("checked_at"))

    pairs = []
    try:
        pairs.append((common.write_json_temp(HISTORY_PATH, history, sort_keys=True), HISTORY_PATH))
        pairs.append((common.write_json_temp(LEADERBOARD_PATH, board), LEADERBOARD_PATH))
    except BaseException:
        common.discard_temps(pairs)
        raise
    common.commit_files(pairs)

    missing = len(ids) - len(users)
    counts = ", ".join(f"{c} {len(board['categories'][c])}" for c in CATEGORIES)
    common.log(f"{len(users)} of {len(ids)} bots returned ({missing} missing/closed); {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(run(common.utc_now()))
