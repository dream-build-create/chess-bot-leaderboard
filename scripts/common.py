"""Shared helpers for discover.py and refresh.py. Standard library only."""

import http.client
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://lichess.org"

# Lichess asks API clients for a descriptive User-Agent with a way to reach
# the operator. Set LEADERBOARD_CONTACT in the workflow, or edit the default.
CONTACT = os.environ.get("LEADERBOARD_CONTACT", "CHANGE-ME@example.com")
USER_AGENT = f"bot-leaderboard/1.0 (contact: {CONTACT})"

PAUSE_SECONDS = 3        # between consecutive requests
RATE_LIMIT_WAIT = 60     # after an HTTP 429
MAX_ATTEMPTS = 4         # total tries per request when rate-limited
TIMEOUT_SECONDS = 60

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FetchError(Exception):
    """A request failed in a way the script should stop on."""


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def request(path, *, accept, method="GET", body=None, content_type=None):
    """Make one request and return the full body as bytes.

    Retries only on HTTP 429, after a full RATE_LIMIT_WAIT. Anything else
    that isn't a complete 200 response raises FetchError. A stream cut off
    mid-body raises too (http.client.IncompleteRead), so callers never see
    a partial body.
    """
    url = API_BASE + path
    data = body.encode("utf-8") if body is not None else None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", accept)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status} from {method} {path}")
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_ATTEMPTS:
                log(f"HTTP 429 from {method} {path}; waiting {RATE_LIMIT_WAIT}s")
                time.sleep(RATE_LIMIT_WAIT)
                continue
            raise FetchError(f"HTTP {e.code} from {method} {path}") from e
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise FetchError(f"{type(e).__name__} on {method} {path}: {e}") from e
    raise FetchError(f"gave up on {method} {path} after {MAX_ATTEMPTS} attempts")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json_temp(path, obj, *, sort_keys=False):
    """Write obj to a temp file next to path and return the temp name.

    Call commit_files() afterwards to rename temps into place, so a failed
    run never leaves a half-written file.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, sort_keys=sort_keys, ensure_ascii=False)
            f.write("\n")
    except BaseException:
        os.unlink(tmp)
        raise
    os.chmod(tmp, 0o644)
    return tmp


def commit_files(pairs):
    """Rename each (temp, final) pair into place."""
    for tmp, final in pairs:
        os.replace(tmp, final)


def discard_temps(pairs):
    for tmp, _ in pairs:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_online(board, online_ids, checked_at):
    """Set each row's online flag from the latest discover run."""
    board["online_checked_at"] = checked_at
    for rows in board.get("categories", {}).values():
        for row in rows:
            row["online"] = row["username"].lower() in online_ids
