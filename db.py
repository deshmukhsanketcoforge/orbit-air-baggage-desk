"""SQLite connection and schema."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger TEXT NOT NULL,
    flight TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('delayed', 'lost', 'damaged')),
    tier TEXT NOT NULL CHECK (tier IN ('standard', 'silver', 'gold')),
    status TEXT NOT NULL CHECK (status IN ('open', 'assigned', 'escalated', 'resolved', 'closed')),
    note TEXT NOT NULL DEFAULT '',
    assigned_agent TEXT,
    filed_at TEXT NOT NULL,
    deadline TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
