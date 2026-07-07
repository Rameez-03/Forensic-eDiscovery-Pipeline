"""
Module 4 - Keyword Search.

Searches every document in forensic.db for a configurable list of search
terms and records which terms matched in a `keyword_hits` column.

Search terms are loaded from config/search_terms.txt — one term per line,
lines beginning with # are treated as comments. Terms are matched
case-insensitively against both the subject and body_text of each document.
Matching is inclusive (substring, not whole-word), consistent with the
over-inclusive approach used in real first-pass keyword review.

Results are written to a `keyword_hits` column (comma-separated list of
matching terms, NULL if none matched). The column is added automatically
if it does not exist. Re-running with a different keyword list resets all
previous results.

Run from the repository root with:
    python -m analysis.keyword_search
"""

import sqlite3
from pathlib import Path

from ingestion.metadata_store import get_connection

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYWORDS_PATH = REPO_ROOT / "config" / "search_terms.txt"


def load_keywords(path: Path = DEFAULT_KEYWORDS_PATH) -> list[str]:
    """Read search terms from a text file. One term per line, # for comments."""
    if not path.exists():
        raise FileNotFoundError(f"Keyword file not found: {path}")
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def ensure_keyword_hits_column(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE documents ADD COLUMN keyword_hits TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists


def find_hits(text: str, keywords: list[str]) -> list[str]:
    """Return every keyword found in text (case-insensitive substring match)."""
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def run_search(conn: sqlite3.Connection, keywords: list[str]) -> dict[str, int]:
    """Search all documents, write keyword_hits, return per-keyword hit counts."""
    rows = conn.execute(
        "SELECT id, subject, body_text FROM documents"
    ).fetchall()

    keyword_counts: dict[str, int] = {kw: 0 for kw in keywords}
    updates = []

    for row in rows:
        combined = f"{row['subject'] or ''} {row['body_text'] or ''}"
        hits = find_hits(combined, keywords)
        if hits:
            for kw in hits:
                keyword_counts[kw] += 1
            updates.append((", ".join(hits), row["id"]))
        else:
            updates.append((None, row["id"]))

    conn.executemany(
        "UPDATE documents SET keyword_hits = ? WHERE id = ?", updates
    )
    conn.commit()
    return keyword_counts


def print_summary(conn: sqlite3.Connection, keyword_counts: dict[str, int]) -> None:
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    hits = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE keyword_hits IS NOT NULL"
    ).fetchone()[0]
    not_hit = total - hits

    max_count = max(keyword_counts.values()) if keyword_counts else 1

    print("=" * 60)
    print("KEYWORD SEARCH SUMMARY")
    print("=" * 60)
    print(f"Total documents:                {total}")
    print(f"Documents with keyword hits:    {hits} ({hits / total:.1%})")
    print(f"Documents without hits:         {not_hit} ({not_hit / total:.1%})")
    print()
    print("Hits per keyword (sorted by frequency):")
    for kw, count in sorted(keyword_counts.items(), key=lambda x: -x[1]):
        bar = "#" * int(count / max_count * 40)
        print(f"  {kw:<25} {count:>5}  {bar}")
    print()

    print("By custodian:")
    rows = conn.execute(
        "SELECT custodian, COUNT(*) AS total, "
        "SUM(keyword_hits IS NOT NULL) AS hits "
        "FROM documents GROUP BY custodian"
    ).fetchall()
    for row in rows:
        pct = row["hits"] / row["total"]
        print(f"  {row['custodian']}: {row['hits']}/{row['total']} hits ({pct:.1%})")
    print()

    print("Top 5 documents by keywords matched:")
    top = conn.execute(
        "SELECT subject, sender, keyword_hits FROM documents "
        "WHERE keyword_hits IS NOT NULL "
        "ORDER BY LENGTH(keyword_hits) - LENGTH(REPLACE(keyword_hits, ',', '')) DESC "
        "LIMIT 5"
    ).fetchall()
    for row in top:
        subject = (row["subject"] or "(no subject)")[:45]
        print(f"  [{row['keyword_hits']}]")
        print(f"    {subject}")
    print("=" * 60)


def run_keyword_search(
    keywords_path: Path = None,
    db_path: Path = None,
) -> None:
    keywords = load_keywords(keywords_path or DEFAULT_KEYWORDS_PATH)
    conn = get_connection(db_path) if db_path else get_connection()
    ensure_keyword_hits_column(conn)
    keyword_counts = run_search(conn, keywords)
    print_summary(conn, keyword_counts)
    conn.close()


if __name__ == "__main__":
    run_keyword_search()
