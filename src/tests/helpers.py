"""Shared helpers for functional test scripts. No pytest."""

from __future__ import annotations

import sys
from pathlib import Path

_pass_count = 0
_fail_count = 0


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def check(label: str, condition: bool, detail: str = "") -> None:
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
        print(f"  \u2713 {label}")
    else:
        _fail_count += 1
        print(f"  \u2717 {label}")
        if detail:
            print(f"    \u2192 {detail}")


def show(label: str, value, max_lines: int = 12) -> None:
    text = str(value)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"  ... ({len(lines) - max_lines} more lines)"]
    indented = "\n".join(f"    {l}" for l in lines)
    print(f"  {label}:\n{indented}")


def report() -> None:
    print(f"\n{'='*70}")
    print(f"  RESULTS: {_pass_count} passed, {_fail_count} failed")
    print(f"{'='*70}\n")
    if _fail_count > 0:
        sys.exit(1)


# ── fixture data builders ────────────────────────────────────────────────────

def make_doc(profile, source_key, source_name, doc_type, title, text, filing_date="2025-01-15"):
    from src.models.documents import Document
    return Document.create(
        ticker=profile.ticker,
        source_key=source_key,
        source_name=source_name,
        source_url=f"https://example.com/{doc_type}/{title.replace(' ', '_')}",
        doc_type=doc_type,
        title=title,
        raw_text=text,
        filing_date=filing_date,
    )


def build_soc_docs(profile):
    return [
        make_doc(profile, "edgar", "SEC EDGAR", "10-K", "Annual Report 2024",
                 "Revenue was $150 million for fiscal year 2024. Operating expenses increased "
                 "to $120 million. Net income declined to $12 million due to higher exploration costs. "
                 "The company holds 646 million barrels of proved reserves in the Santa Ynez Unit. "
                 "Management expects first oil production in late 2025 pending regulatory approvals. "
                 "Risk factors include environmental liability, pipeline integrity, and permitting delays.",
                 "2025-03-15"),
        make_doc(profile, "edgar", "SEC EDGAR", "10-Q", "Q3 2024 Quarterly",
                 "Third quarter revenue was $38 million. Cash and equivalents were $95 million. "
                 "Capital expenditures were $45 million for pipeline restart activities. "
                 "The company reiterated its guidance for first oil in late 2025.",
                 "2024-11-10"),
        make_doc(profile, "edgar", "SEC EDGAR", "8-K", "Material Event: Pipeline Permit",
                 "Sable Offshore received final pipeline permits from PHMSA. "
                 "This removes the last major regulatory hurdle for production restart. "
                 "Management issued updated production guidance of 20,000-25,000 barrels per day.",
                 "2024-09-05"),
        make_doc(profile, "yfinance", "Yahoo Finance", "price_history", "SOC Price History",
                 "SOC stock price: 2024-01-02 $15.20, 2024-06-15 $22.10, 2024-12-31 $18.50. "
                 "52-week high $24.80, 52-week low $12.30. Market cap $1.2B.",
                 "2025-01-01"),
        make_doc(profile, "web_search", "Web Search", "news", "Analyst Coverage",
                 "Multiple analysts initiated coverage on Sable Offshore with mixed ratings. "
                 "Bears cite execution risk on aging infrastructure and environmental liability. "
                 "Bulls highlight deep value in proved reserves at current valuation.",
                 "2024-12-20"),
    ]


def build_akso_docs(profile):
    return [
        make_doc(profile, "newsweb", "Oslo Bors Newsweb", "regulatory_announcement",
                 "Q4 2024 Results",
                 "Aker Solutions reported Q4 2024 revenue of NOK 12.5 billion. EBITDA margin "
                 "improved to 8.2%. Order backlog reached NOK 65 billion, up 15% year-over-year. "
                 "The subsea segment contributed 60% of revenue.",
                 "2025-02-06"),
        make_doc(profile, "company_ir", "Company IR", "annual_report",
                 "Annual Report 2023",
                 "Aker Solutions is a global provider of products, systems and services to the "
                 "energy industry. Full year 2023 revenue was NOK 44.8 billion. The company "
                 "has operations in more than 20 countries. Key risks include project execution "
                 "delays and fluctuations in oil prices.",
                 "2024-03-20"),
        make_doc(profile, "yfinance", "Yahoo Finance", "price_history", "AKSO.OL Price History",
                 "AKSO.OL stock price: 2024-01-02 NOK 38.50, 2024-06-15 NOK 45.20, "
                 "2024-12-31 NOK 42.10. 52-week high NOK 52.30, 52-week low NOK 35.10.",
                 "2025-01-01"),
    ]


def populate_store(tmp_dir, profile, documents):
    """Create a populated corpus store from a list of documents."""
    from src.services.corpus_store import CorpusStore, chunk_document
    from src.models.documents import CorpusManifest, SourceStatus, utc_now_iso

    store = CorpusStore(root_dir=Path(tmp_dir) / "data", output_dir=Path(tmp_dir) / "output")
    all_chunks = []
    for doc in documents:
        store.store_documents(profile, [doc])
        chunks = chunk_document(doc)
        store.store_chunks(profile, chunks)
        all_chunks.extend(chunks)
    doc_map = {d.doc_id: d for d in documents}
    store.index_chunks(profile, all_chunks, doc_map)
    now = utc_now_iso()
    seen_keys = {}
    for doc in documents:
        if doc.source_key not in seen_keys:
            seen_keys[doc.source_key] = 0
        seen_keys[doc.source_key] += 1
    sources = [
        SourceStatus(connector=k, last_fetched=now, doc_count=v, status="fresh")
        for k, v in seen_keys.items()
    ]
    manifest = CorpusManifest(
        ticker=profile.ticker, slug=profile.slug,
        created_at=now, last_refreshed_at=now,
        sources=sources, total_documents=len(documents), total_chunks=len(all_chunks),
    )
    store.save_manifest(profile, manifest)
    return store, all_chunks
