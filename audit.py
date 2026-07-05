"""Append-only audit log for Provenance Guard.

Every classification and appeal event is written here as one JSON object per
line (JSONL). This keeps the log human-inspectable, append-cheap, and trivial to
tail — while still being structured (not print statements). Milestone 4 extends
the entry shape; Milestone 5 adds appeal events.
"""

import json
import os
import threading
from datetime import datetime, timezone

# Log file lives next to this module so the app can be launched from anywhere.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_log.jsonl")

# Writes are serialized so concurrent requests can't interleave partial lines.
_write_lock = threading.Lock()


def utc_now() -> str:
    """ISO-8601 timestamp in UTC with a trailing Z, e.g. 2025-04-01T14:32:10.123Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def append_entry(entry: dict) -> dict:
    """Append one structured entry to the audit log.

    A ``timestamp`` is added if the caller didn't provide one. Returns the entry
    that was actually written (with the timestamp filled in).
    """
    entry.setdefault("timestamp", utc_now())
    line = json.dumps(entry, ensure_ascii=False)
    with _write_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return entry


def get_log(limit: int = 50) -> list[dict]:
    """Return the most recent ``limit`` entries, newest first.

    Malformed lines are skipped rather than crashing the /log endpoint.
    """
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()  # newest first
    return entries[:limit]
