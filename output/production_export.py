"""
Module 6 - Production Export.

Exports the production set to a standard load file format. The production
set contains every document that is:
  - Not a duplicate (is_duplicate = 0)
  - Not privileged (is_privileged = 0)
  - Responsive — has at least one keyword hit (keyword_hits IS NOT NULL)

Each produced document receives a sequential Bates number (ENRON-000001,
ENRON-000002, ...). Bates numbering is the standard document identification
scheme used in litigation; the number is permanently attached to the document
and referenced in any later proceedings.

Output is written to production/VOL001/:
  METADATA.csv     — load file mapping Bates numbers to document metadata,
                     importable by review platforms like Relativity and Nuix
  VOLUME_SUMMARY.md — formal record of what was produced and what was excluded

Run from the repository root with:
    python -m output.production_export
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from ingestion.metadata_store import get_connection

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_DIR = REPO_ROOT / "production" / "VOL001"
BATES_PREFIX = "ENRON"

LOAD_FILE_COLUMNS = [
    "BATES_NUMBER", "ITEM_ID", "CUSTODIAN",
    "DATE_SENT", "DATE_RECEIVED",
    "SENDER", "RECIPIENTS_TO", "RECIPIENTS_CC", "RECIPIENTS_BCC",
    "SUBJECT", "THREAD_ID", "KEYWORD_HITS",
    "FILE_PATH", "FILE_HASH_MD5", "FILE_SIZE_BYTES",
]


def query_production_set(conn) -> list[dict]:
    """Return documents that are non-duplicate, non-privileged, and responsive."""
    rows = conn.execute(
        "SELECT id, custodian, date_sent, date_received, sender, "
        "recipients_to, recipients_cc, recipients_bcc, subject, "
        "thread_id, keyword_hits, file_path, file_hash_md5, file_size_bytes "
        "FROM documents "
        "WHERE is_duplicate = 0 AND is_privileged = 0 AND keyword_hits IS NOT NULL "
        "ORDER BY custodian, date_sent"
    ).fetchall()
    return [dict(r) for r in rows]


def assign_bates(docs: list[dict]) -> list[dict]:
    for i, doc in enumerate(docs, start=1):
        doc["bates_number"] = f"{BATES_PREFIX}-{i:06d}"
    return docs


def write_load_file(docs: list[dict]) -> None:
    PRODUCTION_DIR.mkdir(parents=True, exist_ok=True)
    path = PRODUCTION_DIR / "METADATA.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOAD_FILE_COLUMNS)
        writer.writeheader()
        for doc in docs:
            writer.writerow({
                "BATES_NUMBER": doc["bates_number"],
                "ITEM_ID": doc["id"],
                "CUSTODIAN": doc["custodian"],
                "DATE_SENT": doc.get("date_sent") or "",
                "DATE_RECEIVED": doc.get("date_received") or "",
                "SENDER": doc.get("sender") or "",
                "RECIPIENTS_TO": doc.get("recipients_to") or "",
                "RECIPIENTS_CC": doc.get("recipients_cc") or "",
                "RECIPIENTS_BCC": doc.get("recipients_bcc") or "",
                "SUBJECT": doc.get("subject") or "",
                "THREAD_ID": doc.get("thread_id") or "",
                "KEYWORD_HITS": doc.get("keyword_hits") or "",
                "FILE_PATH": doc.get("file_path") or "",
                "FILE_HASH_MD5": doc.get("file_hash_md5") or "",
                "FILE_SIZE_BYTES": doc.get("file_size_bytes") or "",
            })


def write_volume_summary(docs: list[dict], conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    excl_dupes = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE is_duplicate = 1"
    ).fetchone()[0]
    excl_priv = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE is_privileged = 1 AND is_duplicate = 0"
    ).fetchone()[0]
    excl_no_hits = conn.execute(
        "SELECT COUNT(*) FROM documents "
        "WHERE keyword_hits IS NULL AND is_duplicate = 0 AND is_privileged = 0"
    ).fetchone()[0]

    custodian_counts: dict[str, int] = {}
    for doc in docs:
        c = doc["custodian"]
        custodian_counts[c] = custodian_counts.get(c, 0) + 1

    bates_start = docs[0]["bates_number"] if docs else "N/A"
    bates_end = docs[-1]["bates_number"] if docs else "N/A"

    lines = [
        "# Production Volume Summary\n",
        f"**Production Date:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Volume:** VOL001  ",
        f"**Bates Range:** {bates_start} to {bates_end}\n",
        "## Document Counts\n",
        "| Stage | Count |",
        "|---|---|",
        f"| Total corpus | {total} |",
        f"| Excluded: duplicates | {excl_dupes} |",
        f"| Excluded: privileged | {excl_priv} |",
        f"| Excluded: no keyword hits (not responsive) | {excl_no_hits} |",
        f"| **Produced** | **{len(docs)}** |\n",
        "## By Custodian\n",
        "| Custodian | Documents Produced |",
        "|---|---|",
    ]
    for custodian, count in sorted(custodian_counts.items()):
        lines.append(f"| {custodian} | {count} |")

    (PRODUCTION_DIR / "VOLUME_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_summary(docs: list[dict], conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    n = len(docs)

    custodian_counts: dict[str, int] = {}
    for doc in docs:
        c = doc["custodian"]
        custodian_counts[c] = custodian_counts.get(c, 0) + 1

    print("=" * 60)
    print("PRODUCTION EXPORT SUMMARY")
    print("=" * 60)
    print(f"Total corpus:                   {total}")
    print(f"Documents produced:             {n} ({n / total:.1%})")
    if docs:
        print(f"Bates range:                    {docs[0]['bates_number']} to {docs[-1]['bates_number']}")
    print()
    print("By custodian:")
    for custodian, count in sorted(custodian_counts.items()):
        print(f"  {custodian}: {count} documents")
    print()
    print("Output:")
    print("  production/VOL001/METADATA.csv")
    print("  production/VOL001/VOLUME_SUMMARY.md")
    print("=" * 60)


def run_production_export(db_path: Path = None) -> None:
    conn = get_connection(db_path) if db_path else get_connection()
    docs = query_production_set(conn)

    if not docs:
        print("No documents qualify for production.")
        print("Run keyword search and privilege detection first.")
        conn.close()
        return

    docs = assign_bates(docs)
    write_load_file(docs)
    write_volume_summary(docs, conn)
    print_summary(docs, conn)
    conn.close()


if __name__ == "__main__":
    run_production_export()
