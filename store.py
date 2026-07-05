"""Submission store for Provenance Guard.

The append-only audit log records *events*; this store holds the *current state*
of each submission keyed by content_id, so the appeal endpoint can look up the
original classification and update its status. Persisted as a single JSON file
so a content_id survives a server restart between /submit and /appeal.
"""

import json
import os
import threading

STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "submissions.json")

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_submission(content_id: str, record: dict) -> None:
    """Persist a new submission's classification record."""
    with _lock:
        data = _load()
        data[content_id] = record
        _save(data)


def get_submission(content_id: str) -> dict | None:
    """Return the stored record for content_id, or None if unknown."""
    with _lock:
        return _load().get(content_id)


def update_submission(content_id: str, **changes) -> dict | None:
    """Apply changes to a stored submission. Returns the updated record, or
    None if the content_id is unknown."""
    with _lock:
        data = _load()
        record = data.get(content_id)
        if record is None:
            return None
        record.update(changes)
        data[content_id] = record
        _save(data)
        return record
