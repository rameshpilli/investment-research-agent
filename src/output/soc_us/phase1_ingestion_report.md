# Phase 1 Ingestion Report — Sable Offshore (SOC US)

## Source Coverage Matrix
| Source | Document | What The Analyst Can Expect To Find |
| --- | --- | --- |
| SEC EDGAR | 10-K Annual Report FY2025 (filed 2026-02-27) | Business description and forward-looking operations plan focused on Santa Ynez Unit offshore production restart. Risk factors and regulatory compliance discussions including pipeline transportation challenges. Complex warrant and financial instrument fair value disclosures. Limited traditional revenue metrics due to suspended operations. [1] |
| SEC EDGAR | 10-Q Quarterly Reports Q1-Q3 2025 | Quarterly updates on operational restoration progress and financial position during the production suspension period. Limited financial performance metrics as operations remain suspended. [UNVERIFIED] |
| SEC EDGAR | 26 × 8-K Current Reports (2025-2026) | Material event disclosures covering the period from April 2025 through March 2026. Likely includes operational updates, regulatory developments, and corporate actions during the production restart process. [UNVERIFIED] |
| DuckDuckGo Search | 36 news articles and web sources | Mix of earnings coverage, analyst commentary, and operational updates. Includes third-party analysis of financial distress and operational challenges. [2] |
| Yahoo Finance | Price history and market summary | Stock price movements and market data for valuation context and market sentiment analysis. [UNVERIFIED] |

## Corpus Freshness
Last refreshed: 2026-04-08T20:45:30+00:00

| Connector | Status | Last Fetched | Documents | Refresh Reason |
| --- | --- | --- | --- | --- |
| edgar | fresh | 2026-04-08T20:45:30+00:00 | 0 | ingested_from_data/raw |
| web_search | fresh | 2026-04-08T20:45:30+00:00 | 0 | ingested_from_data/raw |
| yfinance | fresh | 2026-04-08T20:45:30+00:00 | 0 | ingested_from_data/raw |

## Quality Assessment
Corpus provides comprehensive SEC filing coverage with fresh data as of April 8, 2026. Total 69 documents span regulatory filings (30), news/web sources (36), and market data (3). However, content quality is mixed due to Sable's suspended operations status. Major SEC filings contain extensive regulatory and operational restart discussions but limited traditional financial performance metrics. The high volume of 8-K filings (26 reports) suggests significant material event activity during the operational suspension period. [1]

## Missing Context Report
| Expected Material | Criticality | Severity | Why It Matters | Status |
| --- | --- | --- | --- | --- |
| 10-K | Critical | Important | Only 1 annual filing present vs expected 2 over 3-year lookback. Missing prior year 10-K limits historical trend analysis and baseline establishment for operational turnaround assessment. | Present |
| 10-Q | Important | Minor | All 3 expected quarterly filings present covering full year operational transition period through Q3 2025. | Present |
| 8-K | Important | Minor | Comprehensive material event coverage with 26 current reports providing detailed operational and regulatory updates during critical restart phase. | Present |
| transcript | Nice-to-have | Minor | No earnings call transcripts available. Management commentary and Q&A would provide valuable insights into restart timeline, cash burn rate, and operational challenges not captured in SEC filings. [3] | Missing |
| price_history | Important | Minor | Stock price data present for market sentiment and valuation context analysis. | Present |
| competitor_filings | Nice-to-have | Minor | No peer company filings for offshore oil operators available. Comparative operational metrics and regulatory approaches would strengthen competitive positioning analysis. | Missing |

## Citation Verification
- Verified citations: 2/4 (corpus-checked)
- Verification rate: 50%

## Citations
[1] SEC EDGAR (10-K) dated 2026-02-27 :: "invested significant capital to safely restore production operations to SYU"
[2] DuckDuckGo Search (news) - (Public) Sable Offshore Will Run Out Of Money By January :: "Sable Offshore Will Run Out Of Money By January"
[3] DuckDuckGo Search (news) - All Transcripts on Sable Offshore Corp. (SOC) :: "All Transcripts on Sable Offshore Corp."
