"""
Module 2 - Deduplication.

Flags exact duplicates (identical MD5 hash) and groups near-duplicates
(similar but not identical body text) inside forensic.db.

Both exact and near-duplicate detection run at the custodian level: if two
different custodians each hold a copy of the same email, both copies
survive, because who had a document is itself relevant in a real case.
Only duplicates within the SAME custodian's own collection get suppressed.

Run from the repository root with:
    python -m analysis.deduplication
"""

from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

from ingestion.metadata_store import get_connection

NEAR_DUPE_THRESHOLD = 85       # similarity score (0-100) to count as a near-duplicate
BODY_COMPARISON_CHARS = 500    # compare only the lead of each body - keeps the O(n^2)
                                # comparison fast, and the distinguishing content is
                                # front-loaded anyway (quoted reply chains pile up below)


def flag_exact_duplicates(conn) -> int:
    """Group documents by (custodian, file_hash_md5). Within each group, the
    first-ingested document (lowest id) is treated as the original; every
    other document in the group is flagged is_duplicate = 1."""
    rows = conn.execute(
        "SELECT id, custodian, file_hash_md5 FROM documents ORDER BY custodian, file_hash_md5, id"
    ).fetchall()

    groups = defaultdict(list)
    for row in rows:
        groups[(row["custodian"], row["file_hash_md5"])].append(row["id"])

    duplicate_ids = []
    for doc_ids in groups.values():
        duplicate_ids.extend(doc_ids[1:])  # keep the first (lowest id), flag the rest

    if duplicate_ids:
        conn.executemany(
            "UPDATE documents SET is_duplicate = 1 WHERE id = ?",
            [(doc_id,) for doc_id in duplicate_ids],
        )
        conn.commit()

    return len(duplicate_ids)


def _union_find_groups(pairs: list, all_ids: list) -> dict:
    """Any two documents linked by a similarity pair end up in the same
    group, even if they were only connected via a third document (A similar
    to B, B similar to C => A, B and C all grouped together)."""
    parent = {doc_id: doc_id for doc_id in all_ids}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(x, y):
        root_x, root_y = find(x), find(y)
        if root_x != root_y:
            parent[root_x] = root_y

    for a, b in pairs:
        union(a, b)

    return {doc_id: find(doc_id) for doc_id in all_ids}


def flag_near_duplicates(conn, threshold: int = NEAR_DUPE_THRESHOLD) -> tuple:
    """Compare body text within each custodian's non-duplicate documents.
    Returns (number of groups found, number of documents grouped)."""
    rows = conn.execute(
        "SELECT id, custodian, body_text FROM documents WHERE is_duplicate = 0"
    ).fetchall()

    by_custodian = defaultdict(list)
    for row in rows:
        snippet = (row["body_text"] or "")[:BODY_COMPARISON_CHARS]
        by_custodian[row["custodian"]].append((row["id"], snippet))

    total_groups = 0
    total_grouped_docs = 0

    for custodian, docs in by_custodian.items():
        ids = [d[0] for d in docs]
        texts = [d[1] for d in docs]
        if len(ids) < 2:
            continue

        similarity_matrix = process.cdist(
            texts, texts, scorer=fuzz.ratio, score_cutoff=threshold, workers=-1
        )

        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if similarity_matrix[i][j] >= threshold:
                    pairs.append((ids[i], ids[j]))

        if not pairs:
            continue

        roots = _union_find_groups(pairs, ids)
        members_by_root = defaultdict(list)
        for doc_id, root in roots.items():
            members_by_root[root].append(doc_id)

        group_number = 1
        updates = []
        for members in members_by_root.values():
            if len(members) < 2:
                continue
            group_id = f"ND-{custodian}-{group_number}"
            group_number += 1
            total_groups += 1
            total_grouped_docs += len(members)
            updates.extend((group_id, doc_id) for doc_id in members)

        if updates:
            conn.executemany(
                "UPDATE documents SET near_dupe_group_id = ? WHERE id = ?", updates
            )

    conn.commit()
    return total_groups, total_grouped_docs


def print_summary(total_docs: int, exact_count: int, near_dupe_groups: int, near_dupe_docs: int) -> None:
    print("=" * 60)
    print("DEDUPLICATION SUMMARY")
    print("=" * 60)
    print(f"Total documents:                {total_docs}")
    print(f"Exact duplicates flagged:       {exact_count} ({exact_count / total_docs:.1%})")
    print(f"Near-duplicate groups found:    {near_dupe_groups}")
    print(f"Documents in a near-dupe group: {near_dupe_docs} ({near_dupe_docs / total_docs:.1%})")
    print("=" * 60)


def run_deduplication(db_path: Path = None) -> None:
    conn = get_connection(db_path) if db_path else get_connection()
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    exact_count = flag_exact_duplicates(conn)
    near_groups, near_docs = flag_near_duplicates(conn)

    print_summary(total_docs, exact_count, near_groups, near_docs)
    conn.close()


if __name__ == "__main__":
    run_deduplication()
