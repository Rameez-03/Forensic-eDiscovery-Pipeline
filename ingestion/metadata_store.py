"""
Module 1 - Forensic metadata store.

This is the only code path that writes to forensic.db. Every other module
(deduplication, threading, search, privilege, production) reads from or
updates the same `documents` table defined here, so the schema only lives
in one place.
"""

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "forensic.db"

# Schema fields match the spec in HANDOFF.md Module 1. Fields like
# is_duplicate, thread_id, is_privileged and review_tag start out at their
# neutral default and are only ever updated by later modules (2-5) - this
# table is the single shared record the whole pipeline operates on.
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    custodian TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_hash_md5 TEXT NOT NULL,
    file_size_bytes INTEGER,
    date_sent TEXT,
    date_received TEXT,
    sender TEXT,
    recipients_to TEXT,
    recipients_cc TEXT,
    recipients_bcc TEXT,
    subject TEXT,
    body_text TEXT,
    attachment_names TEXT,
    message_id TEXT,
    in_reply_to TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    near_dupe_group_id TEXT,
    thread_id TEXT,
    is_privileged INTEGER NOT NULL DEFAULT 0,
    review_tag TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DOCUMENT_COLUMNS = [
    "custodian",
    "file_path",
    "file_hash_md5",
    "file_size_bytes",
    "date_sent",
    "date_received",
    "sender",
    "recipients_to",
    "recipients_cc",
    "recipients_bcc",
    "subject",
    "body_text",
    "attachment_names",
    "message_id",
    "in_reply_to",
]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def insert_document(conn: sqlite3.Connection, record: dict) -> int | None:
    """Insert one parsed email. file_path is UNIQUE, so re-running ingestion
    over the same raw data is idempotent - already-ingested files are
    silently skipped (rowcount == 0) rather than duplicated, which matters
    because every insert also produces a chain-of-custody entry."""
    placeholders = ", ".join("?" for _ in DOCUMENT_COLUMNS)
    columns_sql = ", ".join(DOCUMENT_COLUMNS)
    values = [record.get(col) for col in DOCUMENT_COLUMNS]

    cursor = conn.execute(
        f"INSERT OR IGNORE INTO documents ({columns_sql}) VALUES ({placeholders})",
        values,
    )
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid
