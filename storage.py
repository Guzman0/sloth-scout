"""Sloth Scout's memory: seen items, saved Gemma rankings and the run log.

MongoDB Atlas (MONGODB_URI) is the main store. JSON files in data/ are always
written too, so if Atlas can't be reached the next run falls back to them with
nothing lost. run.py calls open_store() once and uses whichever comes back.

What's stored:
  items     one record per link: kind, title, first_seen, last_seen, and
            first_shown/last_shown (dates it appeared in a brief). This is
            how a brief knows what's "New today" and what's "Still open".
  rankings  Gemma's latest ranking per link, with the fingerprint (model +
            profile + prompt) it was made with, so it's only reused when
            nothing it depended on has changed.
  runs      one entry per run: timings, counts and retries.
"""

import json
import os
from datetime import datetime, timezone

from common import DATA_DIR

DATABASE_NAME = "sloth_scout"
CONNECT_TIMEOUT_MS = 10_000

RANKED_FILE = DATA_DIR / "ranked.json"
SEEN_FILE = DATA_DIR / "seen.json"
RUN_LOG_FILE = DATA_DIR / "run_log.jsonl"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data):
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class JsonStore:
    """Memory kept in JSON files in data/. Used on its own when Atlas isn't available."""

    name = "JSON files"

    def __init__(self, note=None):
        self.note = note  # why we're using JSON instead of Atlas, if we are

    def previous_rankings(self, fingerprint):
        saved = _read_json(RANKED_FILE, {})
        if saved.get("fingerprint") != fingerprint:
            return {}
        return {item["link"]: item for item in saved.get("ranked", [])}

    def save_rankings(self, fingerprint, model, ranked, skipped, not_eligible):
        _write_json(RANKED_FILE, {
            "ranked_at": _now(),
            "model": model,
            "fingerprint": fingerprint,
            "ranked": ranked,
            "skipped": skipped,
            "not_eligible": [{"title": b["title"], "region": b["region"], "link": b["link"]} for b in not_eligible],
        })

    def seen_items(self):
        """link -> record with first_seen, last_seen, first_shown, last_shown."""
        return _read_json(SEEN_FILE, {})

    def record_items(self, items, shown_links, today):
        seen = self.seen_items()
        today = today.isoformat()
        for item in items:
            record = seen.setdefault(item["link"], {"first_seen": today})
            record.update(kind=item["kind"], title=item["title"], last_seen=today)
            if item["link"] in shown_links:
                record.setdefault("first_shown", today)
                record["last_shown"] = today
        _write_json(SEEN_FILE, seen)

    def log_run(self, entry):
        DATA_DIR.mkdir(exist_ok=True)
        with RUN_LOG_FILE.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")


class MongoStore:
    """Memory kept in MongoDB Atlas, with every write mirrored to the JSON files."""

    name = "MongoDB Atlas"
    note = None

    def __init__(self, client):
        db = client[DATABASE_NAME]
        self.items = db["items"]
        self.rankings = db["rankings"]
        self.runs = db["runs"]
        self.mirror = JsonStore()

    def previous_rankings(self, fingerprint):
        return {doc["_id"]: doc["item"] for doc in self.rankings.find({"fingerprint": fingerprint})}

    def save_rankings(self, fingerprint, model, ranked, skipped, not_eligible):
        for item in ranked:
            self.rankings.replace_one({"_id": item["link"]},
                                      {"fingerprint": fingerprint, "model": model, "ranked_at": _now(), "item": item},
                                      upsert=True)
        self.mirror.save_rankings(fingerprint, model, ranked, skipped, not_eligible)

    def seen_items(self):
        return {doc.pop("_id"): doc for doc in self.items.find({})}

    def record_items(self, items, shown_links, today):
        day = today.isoformat()
        for item in items:
            update = {"$set": {"kind": item["kind"], "title": item["title"], "last_seen": day},
                      "$setOnInsert": {"first_seen": day}}
            if item["link"] in shown_links:
                update["$set"]["last_shown"] = day
                update["$min"] = {"first_shown": day}  # keeps the earliest date
            self.items.update_one({"_id": item["link"]}, update, upsert=True)
        self.mirror.record_items(items, shown_links, today)

    def log_run(self, entry):
        self.runs.insert_one(dict(entry))  # copy: insert_one adds an _id to what it's given
        self.mirror.log_run(entry)


def open_store():
    """Connect to Atlas if MONGODB_URI is set and reachable; otherwise use the JSON files."""
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        return JsonStore(note="MONGODB_URI isn't set")
    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except ImportError:
        return JsonStore(note="the pymongo package isn't installed (run: pip install -r requirements.txt)")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=CONNECT_TIMEOUT_MS, appname="sloth-scout")
        client.admin.command("ping")
    except PyMongoError as error:
        # Never print the URI: it contains the database password.
        return JsonStore(note=f"MongoDB Atlas couldn't be reached ({error.__class__.__name__})")
    except Exception as error:  # e.g. a malformed URI
        return JsonStore(note=f"MONGODB_URI couldn't be used ({error.__class__.__name__})")
    store = MongoStore(client)
    _seed_from_json(store)
    return store


def _seed_from_json(store):
    """The first time Atlas is used, copy in what the JSON files already remember."""
    if store.items.estimated_document_count() or store.rankings.estimated_document_count():
        return
    json_store = JsonStore()
    seen = json_store.seen_items()
    if seen:
        store.items.insert_many([{"_id": link, **record} for link, record in seen.items()])
    saved = _read_json(RANKED_FILE, {})
    if saved.get("ranked"):
        store.rankings.insert_many([
            {"_id": item["link"], "fingerprint": saved.get("fingerprint"), "model": saved.get("model"),
             "ranked_at": saved.get("ranked_at"), "item": item}
            for item in saved["ranked"]
        ])
    print(f"First run with Atlas: copied {len(seen)} seen items and "
          f"{len(saved.get('ranked', []))} rankings from the JSON files.")
