# Forensic E-Discovery Simulation Pipeline

A self-built simulation of an end-to-end **e-discovery and digital forensics investigation pipeline** — the kind of workflow a Forensic Technology analyst runs when a client receives a litigation hold or regulatory investigation. Built to learn the discipline properly: every module maps to a real stage of the **EDRM (Electronic Discovery Reference Model)** and to functionality found in commercial platforms like **Relativity** and **Nuix**.

This is a personal learning project, not commercial software. The dataset, tooling choices, and write-ups below are intended to demonstrate hands-on understanding of forensic methodology, not to replace a real review platform.

---

## Dataset

This project uses the **Enron Email Corpus** — ~600,000 real emails seized from Enron Corporation's mail servers during the FERC investigation into the company's 2001 collapse, later released publicly and now a standard reference dataset for e-discovery and forensic tooling research. Source: [Carnegie Mellon University](https://www.cs.cmu.edu/~enron/).

For this build, two custodians were extracted from the full corpus:

| Custodian | Role | Emails |
|---|---|---|
| `lay-k` | Kenneth Lay, Chairman & CEO | 5,937 |
| `skilling-j` | Jeffrey Skilling, CEO | 4,139 |

**10,076 emails** in total. Raw data is not committed to this repository (see `.gitignore`) — it is regenerated locally by following the steps below.

---

## Build Status

| Module | Description | Status |
|---|---|---|
| 0 | Concepts & glossary | Complete |
| **1** | **Ingestion & metadata extraction** | **Complete** |
| 2 | Deduplication | Planned |
| 3 | Email threading | Planned |
| 4 | Keyword search | Planned |
| 5 | Privilege detection | Planned |
| 6 | Production export | Planned |
| 6B | DSAR response generator | Planned |
| 6C | AI-assisted review summary | Planned |
| 7 | Windows artefact / DFIR parser | Planned |
| 8 | Streamlit review dashboard | Planned |

---

## How to Run

```powershell
# 1. Install dependencies
pip install rapidfuzz python-evtx streamlit pandas jinja2 reportlab

# 2. Download the Enron corpus and extract one or more custodian
#    folders into data/raw/<custodian>/... , renaming each file to end in .eml
#    (the original archive ships files with no extension, e.g. "5.")

# 3. Run ingestion
python -m ingestion.email_parser
```

This creates `forensic.db` (SQLite) and appends to `CHAIN_OF_CUSTODY.md`.

---

## Module 1 — Ingestion & Metadata Extraction

### What it does

Module 1 is the foundation of the whole pipeline. It takes a folder of raw `.eml` files — exactly as they would arrive after a forensic collection — and turns them into a structured, searchable, integrity-verified database. Concretely, for every file under `data/raw/`, it:

1. **Hashes the raw file (MD5)** before any parsing touches it, so the hash represents the file exactly as collected.
2. **Parses the RFC 822 / MIME structure** to extract sender, recipients (To/Cc/Bcc), subject, date, body text, attachments, `Message-ID`, and `In-Reply-To`.
3. **Identifies the custodian** from the collection folder structure (`data/raw/<custodian>/...`).
4. **Writes one row per email** into a `documents` table in `forensic.db` (SQLite).
5. **Appends an entry to `CHAIN_OF_CUSTODY.md`** for every newly ingested file — item ID, custodian, file path, MD5 hash, ingestion timestamp.

Code: [`ingestion/email_parser.py`](ingestion/email_parser.py) (parsing logic) and [`ingestion/metadata_store.py`](ingestion/metadata_store.py) (database schema and writes) — deliberately split so the email-format logic and the persistence logic don't depend on each other.

### Why it matters

This mirrors the **Processing** stage of the EDRM model, and is the same first step any commercial e-discovery platform performs on collected data. Two principles drove the design, both load-bearing in real forensic work:

- **Hash before you touch the file.** If you parse first and hash second, the hash only proves your own derived copy hasn't changed — not the original evidence. Getting this order wrong breaks the evidentiary value of the hash entirely.
- **The custody log is append-only.** Every ingested file gets a permanent line in `CHAIN_OF_CUSTODY.md`; nothing is ever rewritten. A custody log that can be edited retroactively isn't a custody log.

### Result

```
============================================================
INGESTION SUMMARY
============================================================
Total .eml files found:        10076
Newly processed:                10076
Already ingested (skipped):     0
Unique custodians:              2
  -> lay-k, skilling-j
Date range:                      1980-01-01T00:00:00+00:00  to  2002-01-30T19:48:06+00:00
============================================================
```

`forensic.db` — 10,076 rows, ~26.4 MB:

![forensic.db created on disk](docs/screenshots/DB.png)

A single parsed record, queried directly from the database:

![A document record queried from forensic.db](docs/screenshots/Document5.png)

### Real data-quality findings

Validating the pipeline against real data — rather than trusting parsed output blindly — surfaced two genuine forensic findings:

1. **A malformed date header.** One email's raw `Date` header reads `Mon, 31 Dec 1979 16:00:00 -0800` — almost certainly a data-entry error from 1999. This is a concrete illustration of why date-range filtering in e-discovery can't be applied without spot-checking source data; a single bad timestamp could pull a document outside an agreed relevance window, or wrongly exclude one that belongs inside it.

2. **A Bcc/Cc duplication artefact.** 2,533 of the 10,076 emails carry a non-empty `Bcc` header — unusually high, since blind copies are meant to be hidden. Cross-checking the raw header (visible in the screenshot above, document ID 5) showed the `Bcc` value is identical to the `Cc` value on these records — not a genuine blind copy, but an artefact of how this corpus was originally exported from the custodians' Lotus Notes mailboxes. This is the kind of metadata quality issue a forensic analyst has to catch before relying on a field for a privilege or relevance decision.

### Interview talking points

- *"I built an ingestion pipeline that parses raw RFC 822/MIME email files, extracts forensically relevant metadata, hashes every file before any processing touches it, and writes an automatic chain-of-custody log — the same hash-then-process discipline platforms like Relativity and Nuix enforce during collection."*
- *"While validating the pipeline against real data, I found that roughly a quarter of the Bcc fields in this corpus were actually duplicating the Cc field due to how the data was originally exported — which reinforced for me that extracted metadata has to be spot-checked against the raw source, not trusted just because a parser produced it."*

---

## Tech Stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.12 | Industry standard for forensic scripting |
| Email parsing | `email` (stdlib) | Native RFC 822 / MIME parsing, no black-box dependency |
| Database | SQLite (`sqlite3`) | Lightweight, single-file, no server required |
| Fuzzy matching | `rapidfuzz` | Near-duplicate detection (Module 2) |
| EVTX parsing | `python-evtx` | Windows Event Log parsing (Module 7) |
| Dashboard | `streamlit` | Review UI (Module 8) |
| Data handling | `pandas` | Metadata analysis and export |
