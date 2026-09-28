"""One-time seed so the board isn't empty during its first week.

For every (bot, category) that already passes the rating rules and has an
RD under BOOTSTRAP_MAX_RD (a sign of recent play), write a snapshot dated
yesterday with the game count one lower than today's. refresh.py then counts
those bots as active until real snapshots are 7 days old, after which the
normal rule takes over and the seed is pruned.

Pairs that already have a snapshot before today are left alone, so this never
overrides real history. Run it once, then run refresh.py the same UTC day.
"""

import sys
from datetime import date, timedelta

import common
import refresh

BOOTSTRAP_MAX_RD = 50


def seed(history, users, today):
    """Add seed entries to history. Returns {category: count seeded}."""
    snapshots = history.setdefault("snapshots", {})
    seed_date = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    earlier = [snap for d, snap in snapshots.items() if d < today]
    seeded = {cat: 0 for cat in refresh.CATEGORIES}

    for bot_id, user in users.items():
        if user.get("disabled") or user.get("tosViolation"):
            continue
        perfs = user.get("perfs") or {}
        for cat in refresh.CATEGORIES:
            perf = perfs.get(cat)
            if not refresh.qualifies(perf) or perf["rd"] >= BOOTSTRAP_MAX_RD:
                continue
            if any(cat in snap.get(bot_id, {}) for snap in earlier):
                continue   # real history exists; don't override it
            snapshots.setdefault(seed_date, {}).setdefault(bot_id, {})[cat] = perf["games"] - 1
            seeded[cat] += 1
    return seeded


def run(now):
    today = now.date().isoformat()
    roster = common.load_json(refresh.ROSTER_PATH, {"bots": {}})
    ids = sorted(roster.get("bots", {}))
    if not ids:
        common.log("bootstrap: roster is empty; run discover.py first")
        return 1
    history = common.load_json(refresh.HISTORY_PATH, {"schema_version": 1, "snapshots": {}})

    try:
        users = refresh.fetch_users(ids)
    except (common.FetchError, ValueError, KeyError) as e:
        common.log(f"bootstrap failed, history unchanged: {e}")
        return 1

    seeded = seed(history, users, today)
    pairs = [(common.write_json_temp(refresh.HISTORY_PATH, history, sort_keys=True), refresh.HISTORY_PATH)]
    common.commit_files(pairs)
    counts = ", ".join(f"{c} {n}" for c, n in seeded.items())
    common.log(f"bootstrap seeded (RD < {BOOTSTRAP_MAX_RD}): {counts}. Now run refresh.py.")
    return 0


if __name__ == "__main__":
    sys.exit(run(common.utc_now()))
