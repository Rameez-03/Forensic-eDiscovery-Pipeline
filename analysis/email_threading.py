"""
Module 3 - Email Threading.

Assigns a thread_id to every document in forensic.db by reconstructing
conversation threads from Message-ID and In-Reply-To headers extracted
during ingestion.

Two passes run in sequence:

  Pass 1 — In-Reply-To: follows the standard RFC 822 chain. Each document's
  In-Reply-To header is looked up against the corpus's Message-ID index; the
  chain is walked to its root with path compression.

  Pass 2 — Subject-line fallback: runs on documents that are still solo
  threads after pass 1 (i.e. their In-Reply-To was missing or unresolvable).
  Groups them by normalized base subject (Re:/Fwd: stripped, lowercased).
  Required because the Enron corpus was exported from Lotus Notes, which does
  not write RFC 822 In-Reply-To headers on export.

Threading is cross-custodian by design: a conversation between lay-k and
skilling-j belongs in one thread, not two.

Run from the repository root with:
    python -m analysis.email_threading
"""

import re
from collections import defaultdict
from pathlib import Path

from ingestion.metadata_store import get_connection

_REPLY_PREFIX = re.compile(r'^(re|fwd?|fw)(\[\d+\])?[:\s]+', flags=re.IGNORECASE)
_MIN_SUBJECT_LEN = 10  # normalized subjects shorter than this are too generic to thread


def build_lookup_tables(rows: list) -> tuple[dict, dict]:
    """Returns:
      mid_to_id  — message_id string → doc id
      rows_by_id — doc id → row dict
    """
    mid_to_id: dict[str, int] = {}
    rows_by_id: dict[int, dict] = {}
    for row in rows:
        rows_by_id[row["id"]] = row
        mid = (row["message_id"] or "").strip()
        if mid:
            mid_to_id[mid] = row["id"]
    return mid_to_id, rows_by_id


def normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes repeatedly until none remain, return lowercase."""
    s = (subject or "").strip()
    while True:
        new_s = _REPLY_PREFIX.sub("", s).strip()
        if new_s == s:
            return s.lower()
        s = new_s


def resolve_roots(rows_by_id: dict, mid_to_id: dict) -> dict:
    """Pass 1: doc_id → root_doc_id via In-Reply-To chain walking.

    Path compression ensures each document is traced at most once: when a
    chain A→B→C→root is resolved, all three nodes are cached so a later
    lookup starting at B costs O(1).
    """
    root_cache: dict[int, int] = {}

    def get_root(start_id: int) -> int:
        path: list[int] = []
        current = start_id
        visited: set[int] = set()

        while current not in root_cache:
            if current in visited:
                break  # cycle guard
            visited.add(current)
            path.append(current)

            in_reply_to = (rows_by_id[current]["in_reply_to"] or "").strip()
            if not in_reply_to:
                break  # no parent header — this is a root
            parent_id = mid_to_id.get(in_reply_to)
            if parent_id is None:
                break  # broken chain — parent not in corpus, treat as root
            current = parent_id

        root = root_cache.get(current, current)
        for node in path:
            root_cache[node] = root
        return root

    for doc_id in rows_by_id:
        if doc_id not in root_cache:
            get_root(doc_id)

    return root_cache


def subject_line_fallback(rows_by_id: dict, root_map: dict) -> tuple[dict, int]:
    """Pass 2: group solo-thread documents by normalized subject line.

    Only subjects of 10+ characters are considered — short subjects are
    too generic to produce meaningful thread groups. The document with the
    lowest id in each group becomes the thread root.

    Returns (updated root_map, number of new groups formed).
    """
    solo_ids = {doc_id for doc_id, root_id in root_map.items() if doc_id == root_id}

    by_subject: dict[str, list[int]] = defaultdict(list)
    for doc_id in solo_ids:
        base = normalize_subject(rows_by_id[doc_id].get("subject") or "")
        if len(base) >= _MIN_SUBJECT_LEN:
            by_subject[base].append(doc_id)

    groups_formed = 0
    for doc_ids in by_subject.values():
        if len(doc_ids) < 2:
            continue
        root_id = min(doc_ids)
        groups_formed += 1
        for doc_id in doc_ids:
            root_map[doc_id] = root_id

    return root_map, groups_formed


def assign_thread_ids(conn) -> tuple[dict, dict, dict, int]:
    """Run both threading passes and write thread_id = 'TH-{root_id}' for every document.

    Returns (root_map, mid_to_id, rows_by_id, subject_groups_formed).
    """
    raw_rows = conn.execute(
        "SELECT id, message_id, in_reply_to, subject FROM documents"
    ).fetchall()
    rows = [dict(r) for r in raw_rows]

    mid_to_id, rows_by_id = build_lookup_tables(rows)
    root_map = resolve_roots(rows_by_id, mid_to_id)
    root_map, subject_groups = subject_line_fallback(rows_by_id, root_map)

    updates = [(f"TH-{root_id}", doc_id) for doc_id, root_id in root_map.items()]
    conn.executemany("UPDATE documents SET thread_id = ? WHERE id = ?", updates)
    conn.commit()

    return root_map, mid_to_id, rows_by_id, subject_groups


def compute_stats(conn, root_map: dict, mid_to_id: dict, rows_by_id: dict, subject_groups: int) -> dict:
    thread_sizes: dict[int, int] = defaultdict(int)
    for root_id in root_map.values():
        thread_sizes[root_id] += 1

    largest_root = max(thread_sizes, key=thread_sizes.get)

    custodians_per_thread: dict[str, set] = defaultdict(set)
    for row in conn.execute("SELECT thread_id, custodian FROM documents").fetchall():
        custodians_per_thread[row["thread_id"]].add(row["custodian"])

    orphaned = sum(
        1 for row in rows_by_id.values()
        if (row["in_reply_to"] or "").strip()
        and mid_to_id.get((row["in_reply_to"] or "").strip()) is None
    )

    subject_row = conn.execute(
        "SELECT subject FROM documents WHERE id = ?", (largest_root,)
    ).fetchone()

    multi_email = sum(1 for s in thread_sizes.values() if s > 1)

    return {
        "total_docs": len(root_map),
        "total_threads": len(thread_sizes),
        "single_email": sum(1 for s in thread_sizes.values() if s == 1),
        "multi_email": multi_email,
        "inreplyto_groups": multi_email - subject_groups,
        "subject_line_groups": subject_groups,
        "cross_custodian": sum(1 for c in custodians_per_thread.values() if len(c) > 1),
        "orphaned_replies": orphaned,
        "largest_thread_size": thread_sizes[largest_root],
        "largest_thread_root_id": largest_root,
        "largest_thread_subject": (subject_row["subject"] if subject_row else "") or "(no subject)",
    }


def print_summary(stats: dict, conn) -> None:
    print("=" * 60)
    print("THREADING SUMMARY")
    print("=" * 60)
    print(f"Total documents:                {stats['total_docs']}")
    print(f"Total threads:                  {stats['total_threads']}")
    print(f"  Multi-email threads:          {stats['multi_email']}")
    print(f"    via In-Reply-To:            {stats['inreplyto_groups']}")
    print(f"    via subject-line fallback:  {stats['subject_line_groups']}")
    print(f"  Single-email threads:         {stats['single_email']}")
    print(f"Cross-custodian threads:        {stats['cross_custodian']}")
    print(f"Orphaned replies:               {stats['orphaned_replies']}")
    print(f"Largest thread:                 {stats['largest_thread_size']} emails")
    print(f"  Root subject: {stats['largest_thread_subject'][:55]}")
    print("=" * 60)

    rows = conn.execute(
        "SELECT custodian, COUNT(*) AS total, COUNT(DISTINCT thread_id) AS threads "
        "FROM documents GROUP BY custodian"
    ).fetchall()
    for row in rows:
        print(f"{row['custodian']}: {row['total']} docs, {row['threads']} threads")


def run_threading(db_path: Path = None) -> None:
    conn = get_connection(db_path) if db_path else get_connection()
    root_map, mid_to_id, rows_by_id, subject_groups = assign_thread_ids(conn)
    stats = compute_stats(conn, root_map, mid_to_id, rows_by_id, subject_groups)
    print_summary(stats, conn)
    conn.close()


if __name__ == "__main__":
    run_threading()
