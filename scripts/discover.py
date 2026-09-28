"""Find bots currently online, add new ones to data/roster.json, and mark
who is online in docs/data/leaderboard.json.

One request per run. Exits non-zero and leaves both files untouched on any
failure (non-200, broken stream, unparseable line).
"""

import json
import sys

import common

ROSTER_PATH = common.repo_path("data", "roster.json")
LEADERBOARD_PATH = common.repo_path("docs", "data", "leaderboard.json")
ONLINE_PATH = "/api/bot/online?nb=512"   # API default is 100; 512 is the max


def parse_online(raw):
    """Parse the ndjson body into a list of (id, username)."""
    bots = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            bots.append((rec["id"].lower(), rec["username"]))
        except (ValueError, KeyError, AttributeError) as e:
            raise ValueError(f"bad line {n} in bot/online stream: {e}") from e
    return bots


def update_roster(roster, bots, today):
    """Add new bots and stamp last_online. Returns the number added."""
    entries = roster.setdefault("bots", {})
    added = 0
    for bot_id, username in bots:
        entry = entries.get(bot_id)
        if entry is None:
            entries[bot_id] = {
                "username": username,
                "first_seen": today,
                "last_online": today,
            }
            added += 1
        else:
            entry["username"] = username
            entry["last_online"] = today
    return added


def run(now):
    today = now.date().isoformat()
    checked_at = common.iso_utc(now)
    roster = common.load_json(ROSTER_PATH, {"schema_version": 1, "bots": {}})
    try:
        raw = common.request(ONLINE_PATH, accept="application/x-ndjson")
        bots = parse_online(raw)
    except (common.FetchError, ValueError) as e:
        common.log(f"discover failed, roster unchanged: {e}")
        return 1

    added = update_roster(roster, bots, today)
    online_ids = {bot_id for bot_id, _ in bots}
    roster["online"] = {"checked_at": checked_at, "ids": sorted(online_ids)}

    # The board doesn't exist until the first refresh run.
    board = common.load_json(LEADERBOARD_PATH, None)
    if board is not None:
        common.apply_online(board, online_ids, checked_at)

    pairs = []
    try:
        pairs.append((common.write_json_temp(ROSTER_PATH, roster, sort_keys=True), ROSTER_PATH))
        if board is not None:
            pairs.append((common.write_json_temp(LEADERBOARD_PATH, board), LEADERBOARD_PATH))
    except BaseException:
        common.discard_temps(pairs)
        raise
    common.commit_files(pairs)
    common.log(f"{len(bots)} bots online, {added} new, {len(roster['bots'])} in roster")
    return 0


if __name__ == "__main__":
    sys.exit(run(common.utc_now()))
