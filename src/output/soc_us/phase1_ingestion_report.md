# Phase 1 Ingestion Report — Sable Offshore (SOC US)

## Source Coverage Matrix


| Source                        | Document                       | What The Analyst Can Expect To Find                                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SEC EDGAR - Annual Reports    | 10-K FY2025 (filed 2026-02-27) | Comprehensive operational overview focused on Santa Ynez Unit offshore assets including 76,000 acres across 16 federal leases, three platforms (Hondo, Harmony, Heritage) servicing 112 wells with 102 undrilled opportunities, detailed pipeline transportation challenges through Segments 324 and 325, extensive regulatory compliance requirements, and forward-looking production restart plans. [1] [1] |
| SEC EDGAR - Quarterly Reports | 10-Q Q1-Q3 2025 (3 filings)    | Pre-revenue operational period with zero oil and gas sales across all quarters, substantial cash burn through operations and maintenance expenses ($164.3 million nine-month total), going concern qualifications, detailed litigation updates, and progression toward production restart milestones including regulatory approvals. [2] [2]                                                                  |
| SEC EDGAR - Current Reports   | 8-K Filings (26 reports, 2025) | Material corporate events including quarterly earnings releases, significant private equity financing ($250 million placement), and other operational updates. Recent filings cover earnings announcements and capital raising activities essential for funding restart operations. [3] [4]                                                                                                                   |
| Web Search News               | News Articles (36 documents)   | Market coverage including stock performance analysis, earnings expectations, and business development updates. Coverage includes references to significant price volatility and analyst projections for transition from losses to profitability. [5] [6]                                                                                                                                                      |


## Corpus Freshness

Last refreshed: 2026-04-09T02:00:07+00:00


| Connector  | Status | Last Fetched              | Documents | Refresh Reason         |
| ---------- | ------ | ------------------------- | --------- | ---------------------- |
| edgar      | fresh  | 2026-04-09T02:00:07+00:00 | 30        | ingested_from_data/raw |
| web_search | fresh  | 2026-04-09T02:00:07+00:00 | 36        | ingested_from_data/raw |
| yfinance   | fresh  | 2026-04-09T02:00:07+00:00 | 3         | ingested_from_data/raw |


## Quality Assessment

The corpus provides adequate recent regulatory coverage but has notable historical and qualitative gaps. Core SEC filings are present for 2025 operations with comprehensive current events (26 8-K filings), though only one annual report limits historical context. The 69 total documents span regulatory filings (30), news coverage (36), and market data (3), offering breadth across information types. However, absence of management transcripts eliminates access to tone, guidance, and Q&A insights critical for investment analysis. [UNVERIFIED: Market data document content could not be retrieved despite presence in corpus] [1] [2]

## Missing Context Report


| Expected Material                | Criticality  | Severity  | Why It Matters                                                                                                                                                                                                                                                         | Status  |
| -------------------------------- | ------------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| Historical Annual Reports (10-K) | Important    | Important | Only one annual report (FY2025) limits historical trend analysis, peer comparisons, and ability to assess management's track record over multiple cycles. Additional 2-3 year historical context would strengthen conviction in operational and financial projections. | Missing |
| Management Call Transcripts      | Nice-to-have | Minor     | No earnings call transcripts eliminate access to management tone, forward guidance nuances, analyst Q&A insights, and unscripted commentary that often reveals strategic priorities and operational challenges beyond prepared statements. [7]                         | Missing |
| Competitor Benchmark Filings     | Nice-to-have | Minor     | Absence of peer company filings (other offshore oil operators) limits ability to benchmark operational metrics, regulatory approaches, cost structures, and valuation multiples against industry standards.                                                            | Missing |


## Citation Verification

- Verified citations: 9/11 (corpus-checked)
- Verification rate: 82%

## Citations

[1] SEC EDGAR (10-K) dated 2026-02-27 - 10-K Annual Report FY2025 (filed 2026-02-27) :: "76,000 acres and includes 100% working interest with an average 83.6% net revenue interest"
[2] SEC EDGAR (10-Q) dated 2025-11-13 - 10-Q Quarterly Report Q3 2025 (filed 2025-11-13) :: "Operations and maintenance expenses 79,405 25,629 164,246"
[3] SEC EDGAR (8-K) dated 2025-11-10 - 8-K Current Report 2025-11-10 (filed 2025-11-10) :: "private placement of $250 million of the Company's common stock"
[4] SEC EDGAR (8-K) dated 2025-05-09 - 8-K Current Report 2025-05-09 (filed 2025-05-09) :: "announcing results for the period ended March 31, 2025"
[5] DuckDuckGo Search (news) - Sable Offshore (SOC) Crashed This Week. Here is Why. - Insider :: "After gaining over 76% in May,SableOffshoreCorp."
[6] DuckDuckGo Search (news) - Sable Offshore (SOC) Stock Price, News & Analysis :: "expected to grow in the coming year, from ($6.39) to $1.97 per share"
[7] DuckDuckGo Search (news) - All Transcripts on Sable Offshore Corp. (SOC) - MarketScreener :: "SABLE OFFSHORE CORP. PDF Report. Transcripts"