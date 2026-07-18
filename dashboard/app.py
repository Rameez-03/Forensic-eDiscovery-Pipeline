"""
Module 8 - Streamlit Review Dashboard.

Interactive review interface for the forensic e-discovery pipeline.
Queries forensic.db and presents the corpus data across five views:
  Overview    — headline metrics and pipeline status
  Documents   — filterable and searchable document table
  Threads     — conversation explorer, reads emails in thread context
  Keywords    — keyword hit analysis with frequency chart
  Privilege   — privilege log viewer with basis breakdown

Run from the repository root with:
    streamlit run dashboard/app.py
"""

from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "forensic.db"

st.set_page_config(
    page_title="Forensic E-Discovery Pipeline",
    page_icon="⚖",
    layout="wide",
)

# ── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data
def load_documents() -> pd.DataFrame:
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("SELECT * FROM documents", conn)


if not DB_PATH.exists():
    st.error("forensic.db not found. Run `python -m ingestion.email_parser` first.")
    st.stop()

df = load_documents()

# ── Navigation ────────────────────────────────────────────────────────────────

st.sidebar.title("⚖ E-Discovery Pipeline")
st.sidebar.caption("Enron Email Corpus — FERC Investigation")
page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Documents", "Threads", "Keywords", "Privilege"],
    label_visibility="collapsed",
)

# ── Overview ──────────────────────────────────────────────────────────────────

if page == "Overview":
    st.title("Pipeline Overview")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Documents", f"{len(df):,}")
    c2.metric("Unique Threads", f"{df['thread_id'].nunique():,}")
    c3.metric("Keyword Hits", f"{df['keyword_hits'].notna().sum():,}")
    c4.metric("Privileged", f"{int(df['is_privileged'].sum()):,}")
    c5.metric("Duplicates", f"{int(df['is_duplicate'].sum()):,}")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("By Custodian")
        cust_df = (
            df.groupby("custodian")
            .agg(
                Documents=("id", "count"),
                Keyword_Hits=("keyword_hits", lambda x: x.notna().sum()),
                Privileged=("is_privileged", "sum"),
                Threads=("thread_id", "nunique"),
            )
            .reset_index()
            .rename(columns={"custodian": "Custodian", "Keyword_Hits": "Keyword Hits"})
        )
        st.dataframe(cust_df, use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("Pipeline Modules")
        modules = pd.DataFrame([
            ("1", "Ingestion & Metadata Extraction", "Complete"),
            ("2", "Deduplication", "Complete"),
            ("3", "Email Threading", "Complete"),
            ("4", "Keyword Search", "Complete"),
            ("5", "Privilege Detection", "Complete"),
            ("6", "Production Export", "Complete"),
            ("7", "Windows Artefact Parser", "Complete"),
            ("8", "Streamlit Review Dashboard", "Complete"),
        ], columns=["#", "Module", "Status"])
        st.dataframe(modules, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Date Distribution")
    date_df = df[df["date_sent"].notna()].copy()
    date_df["year"] = date_df["date_sent"].str[:4]
    year_counts = date_df["year"].value_counts().sort_index()
    if not year_counts.empty:
        st.bar_chart(year_counts)

# ── Documents ─────────────────────────────────────────────────────────────────

elif page == "Documents":
    st.title("Document Browser")

    col1, col2, col3 = st.columns(3)
    with col1:
        custodians = list(df["custodian"].unique())
        selected_custodians = st.multiselect("Custodian", custodians, default=custodians)
    with col2:
        priv_opt = st.selectbox("Privilege status", ["All", "Privileged only", "Cleared only"])
    with col3:
        hits_opt = st.selectbox("Keyword hits", ["All", "Hits only", "No hits"])

    filtered = df[df["custodian"].isin(selected_custodians)].copy()
    if priv_opt == "Privileged only":
        filtered = filtered[filtered["is_privileged"] == 1]
    elif priv_opt == "Cleared only":
        filtered = filtered[filtered["is_privileged"] == 0]
    if hits_opt == "Hits only":
        filtered = filtered[filtered["keyword_hits"].notna()]
    elif hits_opt == "No hits":
        filtered = filtered[filtered["keyword_hits"].isna()]

    search = st.text_input("Search subject or sender", "")
    if search:
        mask = (
            filtered["subject"].fillna("").str.contains(search, case=False)
            | filtered["sender"].fillna("").str.contains(search, case=False)
        )
        filtered = filtered[mask]

    st.caption(f"{len(filtered):,} documents")
    display_cols = {
        "id": "ID", "custodian": "Custodian", "date_sent": "Date Sent",
        "sender": "Sender", "subject": "Subject", "thread_id": "Thread",
        "keyword_hits": "Keywords", "is_privileged": "Privileged", "review_tag": "Tag",
    }
    st.dataframe(
        filtered[list(display_cols)].rename(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )

# ── Threads ───────────────────────────────────────────────────────────────────

elif page == "Threads":
    st.title("Thread Explorer")

    thread_sizes = df.groupby("thread_id")["id"].count()
    multi = thread_sizes[thread_sizes > 1]

    if multi.empty:
        st.info("No multi-email threads. Run `python -m analysis.email_threading` first.")
    else:
        top_threads = multi.sort_values(ascending=False).head(200)
        selected = st.selectbox(
            f"{len(multi):,} multi-email threads (showing top 200 by size)",
            options=top_threads.index.tolist(),
            format_func=lambda tid: f"{tid}  ({top_threads[tid]} emails)",
        )

        thread_df = df[df["thread_id"] == selected].sort_values("date_sent")
        st.caption(f"{len(thread_df)} emails in this thread")

        for _, row in thread_df.iterrows():
            privileged_label = "PRIVILEGED — " if row["is_privileged"] else ""
            header = (
                f"{privileged_label}"
                f"{str(row['date_sent'] or 'No date')[:10]}  ·  "
                f"{row['sender'] or 'Unknown'}  ·  "
                f"{row['subject'] or '(no subject)'}"
            )
            with st.expander(header):
                m1, m2, m3 = st.columns(3)
                m1.write(f"**Custodian:** {row['custodian']}")
                m2.write(f"**To:** {str(row['recipients_to'] or '').split(';')[0]}")
                m3.write(f"**Keywords:** {row['keyword_hits'] or 'None'}")
                if row["is_privileged"]:
                    st.warning(f"Privilege basis: {row['review_tag']}")
                body = (row["body_text"] or "").strip()[:3000]
                st.text_area("", value=body, height=200, disabled=True, label_visibility="collapsed")

# ── Keywords ──────────────────────────────────────────────────────────────────

elif page == "Keywords":
    st.title("Keyword Analysis")

    hits_df = df[df["keyword_hits"].notna()].copy()

    if hits_df.empty:
        st.info("No keyword hits. Run `python -m analysis.keyword_search` first.")
    else:
        kw_counter: Counter = Counter()
        for kw_str in hits_df["keyword_hits"]:
            for kw in kw_str.split(", "):
                kw_counter[kw.strip()] += 1

        kw_df = pd.DataFrame(
            kw_counter.most_common(), columns=["Keyword", "Documents Hit"]
        ).set_index("Keyword")

        st.subheader("Documents matched per keyword")
        st.bar_chart(kw_df)

        st.divider()

        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("Hit rate by custodian")
            cust_hits = (
                df.groupby("custodian")
                .apply(lambda x: pd.Series({
                    "Total": len(x),
                    "Hits": int(x["keyword_hits"].notna().sum()),
                }))
                .reset_index()
            )
            cust_hits["Hit Rate"] = (
                cust_hits["Hits"] / cust_hits["Total"] * 100
            ).round(1).astype(str) + "%"
            st.dataframe(cust_hits, use_container_width=True, hide_index=True)

        with col_right:
            st.subheader("Keyword table")
            st.dataframe(
                kw_df.reset_index(),
                use_container_width=True,
                hide_index=True,
            )

# ── Privilege ─────────────────────────────────────────────────────────────────

elif page == "Privilege":
    st.title("Privilege Review")

    priv_df = df[df["is_privileged"] == 1].copy()

    if priv_df.empty:
        st.info("No privileged documents. Run `python -m output.privilege_log` first.")
    else:
        basis_counts = priv_df["review_tag"].value_counts()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Privileged", len(priv_df))
        c2.metric("Attorney-Client", int(basis_counts.get("Attorney-Client Privilege", 0)))
        c3.metric("Work Product", int(basis_counts.get("Work Product", 0)))
        c4.metric("Potentially Privileged", int(basis_counts.get("Potentially Privileged", 0)))

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            basis_filter = st.selectbox(
                "Filter by basis",
                ["All"] + list(priv_df["review_tag"].dropna().unique()),
            )
        with col2:
            cust_filter_p = st.multiselect(
                "Custodian",
                list(priv_df["custodian"].unique()),
                default=list(priv_df["custodian"].unique()),
            )

        filtered_priv = priv_df[priv_df["custodian"].isin(cust_filter_p)]
        if basis_filter != "All":
            filtered_priv = filtered_priv[filtered_priv["review_tag"] == basis_filter]

        st.caption(f"{len(filtered_priv):,} documents")
        st.dataframe(
            filtered_priv[["id", "custodian", "date_sent", "sender", "subject", "review_tag"]].rename(
                columns={
                    "id": "ID", "custodian": "Custodian", "date_sent": "Date",
                    "sender": "Sender", "subject": "Subject", "review_tag": "Basis",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
