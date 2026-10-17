"""SQLite storage: requests, refunds, human reviews, settings, and a hash-chained audit table."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

GENESIS = "0" * 64
SCHEMA = """
CREATE TABLE IF NOT EXISTS requests(
  request_id TEXT PRIMARY KEY, created_at TEXT, order_id TEXT, customer_id TEXT, message TEXT,
  photos INTEGER, status TEXT, action TEXT, refund_amount REAL, reason TEXT, clauses TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS refunds(
  refund_id TEXT PRIMARY KEY, request_id TEXT, order_id TEXT, customer_id TEXT, amount REAL,
  created_at TEXT, idempotency_key TEXT UNIQUE, source TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  request_id TEXT PRIMARY KEY, reviewer TEXT, decision TEXT, amount REAL, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, body TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def _digest(prev, ts, body):
    return hashlib.sha256((prev + ts + body).encode()).hexdigest()


class Database:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    # ---- settings (kill switch)
    def get_setting(self, key, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self.conn.commit()

    # ---- audit chain
    def audit_append(self, event, ts):
        with self.lock:
            last = self.conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else GENESIS
            body = json.dumps(event, sort_keys=True, ensure_ascii=False)
            h = _digest(prev, ts, body)
            self.conn.execute("INSERT INTO audit(ts,body,prev_hash,hash) VALUES(?,?,?,?)", (ts, body, prev, h))
            self.conn.commit()
            return h

    def audit_verify(self):
        prev = GENESIS
        for r in self.conn.execute("SELECT * FROM audit ORDER BY id"):
            if r["prev_hash"] != prev or r["hash"] != _digest(prev, r["ts"], r["body"]):
                return False, r["id"]
            prev = r["hash"]
        return True, None

    def audit_recent(self, limit=50):
        rows = self.conn.execute("SELECT id, ts, body, hash FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "event": json.loads(r["body"]), "hash": r["hash"][:12]} for r in rows]

    # ---- refund history (facts come from our own records, never from the customer)
    def customer_history(self, customer_id, now):
        count90, value30 = 0, 0.0
        for r in self.conn.execute("SELECT amount, created_at FROM refunds WHERE customer_id=?", (customer_id,)):
            age = now - datetime.fromisoformat(r["created_at"])
            if age <= timedelta(days=90):
                count90 += 1
            if age <= timedelta(days=30):
                value30 += r["amount"]
        return count90, value30

    def refunded_total(self, order_id):
        r = self.conn.execute("SELECT COALESCE(SUM(amount),0) AS t FROM refunds WHERE order_id=?", (order_id,)).fetchone()
        return float(r["t"])

    def insert_refund(self, refund_id, request_id, order_id, customer_id, amount, ts, key, source):
        self.conn.execute("INSERT INTO refunds VALUES(?,?,?,?,?,?,?,?)",
                          (refund_id, request_id, order_id, customer_id, amount, ts, key, source))
        self.conn.commit()


def utcnow():
    return datetime.now(timezone.utc)
