import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    name          TEXT PRIMARY KEY,
    resolved_name TEXT,
    found         INTEGER NOT NULL DEFAULT 0,
    response_json TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id   INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    name         TEXT NOT NULL,
    release      TEXT NOT NULL,
    size         INTEGER NOT NULL,
    mtime_ns     INTEGER NOT NULL,
    crc32        TEXT,
    expected_crc TEXT,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    scanned_at   TEXT,
    UNIQUE(library_id, path)
);
CREATE INDEX IF NOT EXISTS idx_files_lib ON files(library_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(library_id, status);

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id  INTEGER NOT NULL,
    state       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    files_total INTEGER DEFAULT 0,
    files_done  INTEGER DEFAULT 0,
    message     TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
