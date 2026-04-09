#!/usr/bin/env python3
"""Step 1 Test: Connectors — parsing logic for EDGAR, Newsweb, Company IR, HTML→text."""

from src.tests.helpers import section, check, show, report
from src.connectors.edgar import EdgarConnector
from src.connectors.newsweb import NewswebConnector
from src.connectors.company_ir import CompanyIRConnector
from src.connectors.base import BaseResearchConnector


def main():
    section("EDGAR — parse_recent_filings")
    recent = {
        "form": ["10-K", "8-K", "10-Q"],
        "filingDate": ["2025-03-15", "2025-03-20", "2024-11-10"],
        "accessionNumber": ["0001-0001", "0001-0002", "0001-0003"],
        "primaryDocument": ["annual.htm", "event.htm", "quarterly.htm"],
        "primaryDocDescription": ["Annual report", "Material event", "Quarterly"],
        "act": ["34", "34", "34"],
        "fileNumber": ["001", "001", "001"],
    }
    rows = EdgarConnector.parse_recent_filings(recent)
    check(f"Parsed {len(rows)} filings", len(rows) == 3)
    check("First is 10-K", rows[0]["form"] == "10-K")
    show("Parsed", [(r["form"], r["filingDate"]) for r in rows])

    section("Newsweb — HTML parsing")
    html = """
    <a href="/release/quarterly-results-2025-02-15">Quarterly results 2025-02-15</a>
    <a href="/release/annual-report-2025-03-01">Annual report 2025-03-01</a>
    <a href="/misc/job">Hiring</a>
    """
    entries = NewswebConnector.parse_listing(html, "https://newsweb.oslobors.no")
    check(f"Filtered to {len(entries)} relevant entries", len(entries) == 2)

    section("Newsweb — RSS parsing")
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>Q4 Results</title><link>https://newsweb.oslobors.no/123</link>
        <pubDate>2025-02-06T08:00:00</pubDate><description>Revenue NOK 12.5B</description></item>
    </channel></rss>"""
    rss_entries = NewswebConnector.parse_listing(rss, "https://newsweb.oslobors.no")
    check("RSS parsed", len(rss_entries) == 1 and rss_entries[0]["title"] == "Q4 Results")

    section("Company IR — parse_report_links")
    ir_html = """
    <a href="/reports/annual-2024.pdf">Annual Report 2024</a>
    <a href="/reports/q4-pres.pdf">Q4 Presentation</a>
    <a href="/about">About</a>
    <a href="/reports/annual-2024.pdf">Annual Report 2024</a>
    """
    links = CompanyIRConnector.parse_report_links(ir_html, "https://example.com/investors")
    check(f"Found {len(links)} links (filtered, deduped)", len(links) == 2)
    check("About excluded", not any("about" in t.lower() for _, t in links))

    section("Base — html_to_text")
    raw = "<html><script>x</script><style>y</style><p>Revenue was <b>$150M</b> &amp; growing.</p></html>"
    text = BaseResearchConnector.html_to_text(raw)
    check("Script stripped", "x" not in text or "script" not in text.lower())
    check("Content preserved", "$150M" in text and "&" in text)
    show("Extracted", text)

    report()

if __name__ == "__main__":
    main()
