"""Tamper-evident audit log: append-only JSONL where each entry stores the hash of the previous one.
Editing or deleting an old line breaks the chain, and verify() reports where."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "runtime" / "audit_log.jsonl"
GENESIS = "0" * 64


def _digest(prev, body):
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()


def read_all(path=None):
    path = Path(path or LOG)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def append(event, path=None):
    path = Path(path or LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = read_all(path)
    prev = entries[-1]["hash"] if entries else GENESIS
    body = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    entry = {**body, "prev_hash": prev, "hash": _digest(prev, body)}
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def verify(path=None):
    """Returns (ok, index_of_first_bad_entry_or_None)."""
    prev = GENESIS
    for i, e in enumerate(read_all(path)):
        body = {k: v for k, v in e.items() if k not in ("prev_hash", "hash")}
        if e.get("prev_hash") != prev or e.get("hash") != _digest(prev, body):
            return False, i
        prev = e["hash"]
    return True, None


def pending_reviews(path=None):
    """Escalated decisions that have no human_review event yet (the audit log is the source of truth)."""
    entries = read_all(path)
    reviewed = {e["request_id"] for e in entries if e.get("type") == "human_review"}
    return [e for e in entries
            if e.get("type") == "decision" and e.get("action") == "escalate" and e["request_id"] not in reviewed]
