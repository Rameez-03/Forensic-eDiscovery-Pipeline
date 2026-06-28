"""
Module 1 - Ingestion & Metadata Extraction.

Walks data/raw/ recursively, parses every .eml file (RFC 822 / MIME format),
extracts the metadata fields required by the `documents` table, hashes each
raw file with MD5 *before* any parsing touches it, writes the record to
forensic.db, and appends a chain-of-custody entry for every file processed.

Custodian is inferred from the first path segment under data/raw/, mirroring
how a real collection is organised on disk:
    data/raw/<custodian>/<mail folder>/<file>.eml

Run from the repository root with:
    python -m ingestion.email_parser
"""

import email
import hashlib
from datetime import datetime, timezone
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from ingestion.metadata_store import get_connection, init_db, insert_document

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
CHAIN_OF_CUSTODY_PATH = REPO_ROOT / "CHAIN_OF_CUSTODY.md"


def compute_md5(file_path: Path) -> str:
    """Hash the raw file bytes. This MUST happen before the file is parsed,
    so the hash represents the file exactly as collected - not as
    interpreted (and potentially normalised) by the email parser. This hash
    becomes the baseline every later integrity check is compared against."""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def extract_custodian(file_path: Path) -> str:
    relative = file_path.relative_to(RAW_DATA_DIR)
    return relative.parts[0]


def parse_addresses(header_value: str | None) -> list[str]:
    """Parse a To/Cc/Bcc header into a list of bare email addresses.
    getaddresses (not a plain ','.split) is required because display names
    like '"Belden, Tim" <tim.belden@enron.com>' contain commas of their own."""
    if not header_value:
        return []
    return [addr for _, addr in getaddresses([header_value]) if addr]


def parse_date(header_value: str | None) -> str | None:
    """Normalise inconsistent sender-supplied timezones to ISO 8601 UTC so
    every date in the database can be sorted and range-filtered correctly."""
    if not header_value:
        return None
    try:
        dt = parsedate_to_datetime(header_value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def parse_received_date(msg: Message) -> str | None:
    """The 'Date' header is set by the sender's own mail client and is
    self-reported. The 'Received' header is stamped by the recipient's mail
    server on arrival, making it a useful independent cross-check. We take
    the first 'Received' header, which is the most recent hop."""
    received_headers = msg.get_all("Received") or []
    if not received_headers:
        return None
    last_hop = received_headers[0]
    if ";" not in last_hop:
        return None
    timestamp_part = last_hop.rsplit(";", 1)[-1].strip()
    return parse_date(timestamp_part)


def extract_attachments(msg: Message) -> list[str]:
    """Walk MIME parts and record attachment filename + size. The Enron
    corpus is almost entirely plain text, but this must still handle
    multipart messages correctly for any real-world PST export."""
    attachments = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(f"{filename} ({len(payload)} bytes)")
    return attachments


def extract_body(msg: Message) -> str:
    """Prefer the plain-text part - review platforms index text for search,
    so that is what we extract here. HTML-only bodies are decoded as-is."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def parse_eml_file(file_path: Path) -> dict:
    """Parse one .eml file into a dict matching the `documents` schema."""
    file_hash = compute_md5(file_path)  # hash BEFORE parsing - see compute_md5()

    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    from_addrs = parse_addresses(msg.get("From"))
    sender = from_addrs[0] if from_addrs else (msg.get("From") or "")

    return {
        "custodian": extract_custodian(file_path),
        "file_path": str(file_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "file_hash_md5": file_hash,
        "date_sent": parse_date(msg.get("Date")),
        "date_received": parse_received_date(msg),
        "sender": sender,
        "recipients_to": ";".join(parse_addresses(msg.get("To"))),
        "recipients_cc": ";".join(parse_addresses(msg.get("Cc"))),
        "recipients_bcc": ";".join(parse_addresses(msg.get("Bcc"))),
        "subject": msg.get("Subject", ""),
        "body_text": extract_body(msg),
        "attachment_names": ";".join(extract_attachments(msg)),
        "message_id": msg.get("Message-ID", ""),
        "in_reply_to": msg.get("In-Reply-To", ""),
    }


def init_chain_of_custody() -> None:
    """Create CHAIN_OF_CUSTODY.md with its table header if it doesn't exist
    yet. Module 9 will add narrative context around this log; for now it
    just needs to be a valid table that append_custody_rows can grow."""
    if CHAIN_OF_CUSTODY_PATH.exists():
        return
    header = (
        "# Chain of Custody Log\n\n"
        "Every row below is appended automatically by "
        "`ingestion/email_parser.py` at the moment a file is hashed and "
        "ingested - before any analysis is performed on it. This is a "
        "simulation of the audit trail a platform like Relativity or Nuix "
        "maintains internally for every item in a case.\n\n"
        "| Item ID | Custodian | File Path | MD5 Hash | Date Ingested (UTC) "
        "| Collected By | Storage Location | Transfers |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    CHAIN_OF_CUSTODY_PATH.write_text(header, encoding="utf-8")


def append_custody_rows(rows: list[str]) -> None:
    init_chain_of_custody()
    with open(CHAIN_OF_CUSTODY_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def run_ingestion(raw_dir: Path = RAW_DATA_DIR, db_path: Path = None) -> None:
    conn = get_connection(db_path) if db_path else get_connection()
    init_db(conn)

    eml_files = sorted(raw_dir.rglob("*.eml"))
    custody_rows = []
    processed = 0
    skipped = 0
    custodians = set()
    dates = []

    for file_path in eml_files:
        record = parse_eml_file(file_path)
        doc_id = insert_document(conn, record)
        if doc_id is None:
            skipped += 1
            continue

        processed += 1
        custodians.add(record["custodian"])
        if record["date_sent"]:
            dates.append(record["date_sent"])

        custody_rows.append(
            f"| {doc_id} | {record['custodian']} | {record['file_path']} | "
            f"{record['file_hash_md5']} | {datetime.now(timezone.utc).isoformat()} | "
            f"Rameez (simulated collection) | data/raw/ | Ingested into forensic.db |"
        )

    conn.commit()
    conn.close()

    if custody_rows:
        append_custody_rows(custody_rows)

    print_summary(len(eml_files), processed, skipped, custodians, dates)


def print_summary(
    total_files: int,
    processed: int,
    skipped: int,
    custodians: set,
    dates: list,
) -> None:
    print("=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)
    print(f"Total .eml files found:        {total_files}")
    print(f"Newly processed:                {processed}")
    print(f"Already ingested (skipped):     {skipped}")
    print(f"Unique custodians:              {len(custodians)}")
    if custodians:
        print(f"  -> {', '.join(sorted(custodians))}")
    if dates:
        print(f"Date range:                      {min(dates)}  to  {max(dates)}")
    else:
        print("Date range:                      N/A")
    print("=" * 60)


if __name__ == "__main__":
    run_ingestion()
