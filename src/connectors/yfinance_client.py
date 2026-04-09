"""
Yahoo Finance Connector
========================

Pulls market data from Yahoo Finance for investment research.

Saves three files per company:
  market_data/
    price_history.csv     ← daily OHLCV, 3 years, ready for Excel/pandas
    summary.json          ← valuation, analyst targets, short interest, risk
    holders.csv           ← institutional holders with % ownership

Key class:
- YFinanceConnector -- fetches market data from Yahoo Finance
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from src.config import settings
from src.connectors.base import BaseResearchConnector
from src.models.companies import CompanyConfig
from src.models.documents import Document


class YFinanceConnector(BaseResearchConnector):
    source_key = "yfinance"
    source_name = "Yahoo Finance"
    refresh_hours = 24

    def __init__(self) -> None:
        super().__init__("https://finance.yahoo.com")

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        try:
            import yfinance as yf
        except Exception:
            return []

        ticker = yf.Ticker(profile.yfinance_ticker)
        history = ticker.history(period=f"{settings.years_of_price_history}y")
        as_of = datetime.now(timezone.utc).date().isoformat()
        url = f"https://finance.yahoo.com/quote/{profile.yfinance_ticker}"

        # Get company info once — used by both CSV and summary
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass

        fast = {}
        try:
            fi = ticker.fast_info
            fast = {k: getattr(fi, k, None) for k in [
                "market_cap", "shares", "last_price", "previous_close",
                "fifty_day_average", "two_hundred_day_average",
                "year_high", "year_low", "year_change",
                "ten_day_average_volume", "three_month_average_volume",
                "exchange", "currency",
            ]}
        except Exception:
            pass

        company_name = info.get("displayName") or info.get("shortName") or profile.name
        exchange = fast.get("exchange") or info.get("exchange") or profile.exchange
        currency = fast.get("currency") or info.get("currency") or profile.currency

        # ── Price history (CSV) ──────────────────────────────────
        price_rows = _extract_price_rows(
            history, profile.yfinance_ticker, company_name, exchange, currency,
        )
        csv_text = _to_csv(price_rows)

        price_doc = Document.create(
            ticker=profile.ticker,
            source_key=self.source_key,
            source_name=self.source_name,
            source_url=url,
            doc_type="price_history",
            title=f"{profile.name} price history",
            raw_text=csv_text,
            as_of_date=as_of,
            metadata={"finance_ticker": profile.yfinance_ticker, "format": "csv", "rows": len(price_rows)},
        )

        # ── Company summary (JSON) ──────────────────────────────
        summary = _extract_summary(info, fast, profile, price_rows, company_name, exchange, currency)
        summary_text = _summary_to_text(profile, summary)

        summary_doc = Document.create(
            ticker=profile.ticker,
            source_key=self.source_key,
            source_name=self.source_name,
            source_url=url,
            doc_type="market_summary",
            title=f"{profile.name} market summary",
            raw_text=summary_text,
            as_of_date=as_of,
            structured_payload=summary,
            metadata={"finance_ticker": profile.yfinance_ticker, "format": "json"},
        )

        docs = [price_doc, summary_doc]

        # ── Institutional holders (CSV) ──────────────────────────
        holders_doc = _extract_holders_doc(ticker, profile, as_of, url)
        if holders_doc:
            docs.append(holders_doc)

        return docs


# ── Price history ────────────────────────────────────────────────────

_PRICE_FIELDS = [
    "ticker", "company_name", "exchange", "currency",
    "date", "open", "high", "low", "close", "volume",
]


def _extract_price_rows(
    history,
    yf_ticker: str,
    company_name: str,
    exchange: str,
    currency: str,
) -> list[dict]:
    rows: list[dict] = []
    if not hasattr(history, "iterrows"):
        return rows
    for date_idx, row in history.iterrows():
        import math
        close = row.get("Close", 0)
        # Skip rows where close price is NaN (e.g. incomplete intraday data)
        if isinstance(close, float) and math.isnan(close):
            continue
        rows.append({
            "ticker": yf_ticker,
            "company_name": company_name,
            "exchange": exchange,
            "currency": currency,
            "date": str(date_idx.date()) if hasattr(date_idx, "date") else str(date_idx),
            "open": round(row.get("Open", 0), 2),
            "high": round(row.get("High", 0), 2),
            "low": round(row.get("Low", 0), 2),
            "close": round(close, 2),
            "volume": int(row.get("Volume", 0)),
        })
    return rows


def _to_csv(rows: list[dict]) -> str:
    if not rows:
        return ",".join(_PRICE_FIELDS) + "\n"
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_PRICE_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


# ── Company summary ──────────────────────────────────────────────────

def _extract_summary(
    info: dict,
    fast: dict,
    profile: CompanyConfig,
    price_rows: list[dict],
    company_name: str,
    exchange: str,
    currency: str,
) -> dict:
    """Pull everything an analyst needs from yfinance into one dict."""

    def _r(v, decimals=2):
        return round(v, decimals) if isinstance(v, (int, float)) and v is not None else v

    summary = {
        "ticker": profile.yfinance_ticker,
        "company_name": company_name,
        "exchange": exchange,
        "currency": currency,
        "as_of": price_rows[-1]["date"] if price_rows else None,

        # ── Profile ──
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "employees": info.get("fullTimeEmployees"),
        "website": info.get("website"),

        # ── Price & Market ──
        "last_close": _r(fast.get("last_price")),
        "market_cap": fast.get("market_cap"),
        "enterprise_value": info.get("enterpriseValue"),
        "shares_outstanding": fast.get("shares"),
        "beta": _r(info.get("beta")),
        "52w_high": _r(fast.get("year_high")),
        "52w_low": _r(fast.get("year_low")),
        "52w_change": _r(fast.get("year_change"), 4),
        "50d_avg": _r(fast.get("fifty_day_average")),
        "200d_avg": _r(fast.get("two_hundred_day_average")),

        # ── Valuation ──
        "trailing_pe": _r(info.get("trailingPE")),
        "forward_pe": _r(info.get("forwardPE")),
        "price_to_book": _r(info.get("priceToBook")),
        "ev_to_revenue": _r(info.get("enterpriseToRevenue")),
        "ev_to_ebitda": _r(info.get("enterpriseToEbitda")),

        # ── Financials ──
        "total_revenue": info.get("totalRevenue"),
        "ebitda": info.get("ebitda"),
        "ebitda_margin": _r(info.get("ebitdaMargins"), 4),
        "operating_cashflow": info.get("operatingCashflow"),
        "free_cashflow": info.get("freeCashflow"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "debt_to_equity": _r(info.get("debtToEquity")),
        "current_ratio": _r(info.get("currentRatio")),
        "return_on_equity": _r(info.get("returnOnEquity"), 4),

        # ── Analyst ──
        "analyst_rating": info.get("averageAnalystRating"),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "target_high": _r(info.get("targetHighPrice")),
        "target_low": _r(info.get("targetLowPrice")),
        "target_mean": _r(info.get("targetMeanPrice")),
        "target_median": _r(info.get("targetMedianPrice")),

        # ── Short Interest ──
        "short_ratio": _r(info.get("shortRatio")),
        "short_pct_float": _r(info.get("shortPercentOfFloat"), 4),
        "shares_short": info.get("sharesShort"),
        "shares_short_prior_month": info.get("sharesShortPriorMonth"),

        # ── Ownership ──
        "insider_pct": _r(info.get("heldPercentInsiders"), 4),
        "institutional_pct": _r(info.get("heldPercentInstitutions"), 4),

        # ── Risk Scores (1-10, higher = more risk) ──
        "audit_risk": info.get("auditRisk"),
        "board_risk": info.get("boardRisk"),
        "compensation_risk": info.get("compensationRisk"),
        "shareholder_rights_risk": info.get("shareHolderRightsRisk"),
        "overall_risk": info.get("overallRisk"),

        # ── Volume ──
        "avg_volume_10d": fast.get("ten_day_average_volume"),
        "avg_volume_3m": fast.get("three_month_average_volume"),
    }

    # Computed performance from price history
    if price_rows:
        import math
        latest = price_rows[-1]
        curr = latest["close"]
        if isinstance(curr, (int, float)) and not math.isnan(curr) and curr > 0:
            for label, days in [("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)]:
                if len(price_rows) > days:
                    old = price_rows[-(days + 1)]["close"]
                    if isinstance(old, (int, float)) and not math.isnan(old) and old > 0:
                        summary[f"return_{label}"] = round((curr - old) / old * 100, 2)

    # Strip None values
    return {k: v for k, v in summary.items() if v is not None}


def _summary_to_text(profile: CompanyConfig, s: dict) -> str:
    """Render summary dict as human-readable text for the AI pipeline."""
    lines = [
        f"Market Summary: {s.get('company_name', profile.name)} ({s.get('ticker')})",
        f"Sector: {s.get('sector', '?')} | Industry: {s.get('industry', '?')}",
        f"Currency: {s.get('currency')} | Employees: {s.get('employees', '?')}",
        f"As of: {s.get('as_of', '?')}",
        "",
        "Price & Market:",
        f"  Last Close: {s.get('last_close')}",
        f"  Market Cap: {_fmt_large(s.get('market_cap'))} {s.get('currency', '')}",
        f"  Enterprise Value: {_fmt_large(s.get('enterprise_value'))} {s.get('currency', '')}",
        f"  52W High: {s.get('52w_high')} | 52W Low: {s.get('52w_low')}",
        f"  Beta: {s.get('beta')}",
        "",
        "Valuation:",
        f"  Forward P/E: {s.get('forward_pe')} | Trailing P/E: {s.get('trailing_pe')}",
        f"  P/B: {s.get('price_to_book')} | EV/EBITDA: {s.get('ev_to_ebitda')}",
        "",
        "Financials:",
        f"  Revenue: {_fmt_large(s.get('total_revenue'))}",
        f"  EBITDA: {_fmt_large(s.get('ebitda'))} (margin: {_pct(s.get('ebitda_margin'))})",
        f"  FCF: {_fmt_large(s.get('free_cashflow'))}",
        f"  Cash: {_fmt_large(s.get('total_cash'))} | Debt: {_fmt_large(s.get('total_debt'))}",
        f"  D/E: {s.get('debt_to_equity')} | Current Ratio: {s.get('current_ratio')}",
        f"  ROE: {_pct(s.get('return_on_equity'))}",
        "",
        "Analyst Consensus:",
        f"  Rating: {s.get('analyst_rating')} ({s.get('analyst_count', '?')} analysts)",
        f"  Target: {s.get('target_low')} – {s.get('target_mean')} – {s.get('target_high')}",
        "",
        "Short Interest:",
        f"  Short % Float: {_pct(s.get('short_pct_float'))} | Short Ratio: {s.get('short_ratio')} days",
        f"  Shares Short: {_fmt_int(s.get('shares_short'))}",
        "",
        "Ownership:",
        f"  Insiders: {_pct(s.get('insider_pct'))} | Institutions: {_pct(s.get('institutional_pct'))}",
        "",
        "Risk Scores (1-10, higher = more risk):",
        f"  Overall: {s.get('overall_risk')} | Audit: {s.get('audit_risk')} | Board: {s.get('board_risk')}",
        "",
        "Performance:",
    ]
    for label, key in [("1M", "return_1m"), ("3M", "return_3m"), ("6M", "return_6m"), ("1Y", "return_1y")]:
        if key in s:
            lines.append(f"  {label}: {s[key]:+.2f}%")

    return "\n".join(lines)


# ── Holders ──────────────────────────────────────────────────────────

def _extract_holders_doc(ticker, profile: CompanyConfig, as_of: str, url: str) -> Document | None:
    """Extract institutional holders as a CSV document."""
    try:
        holders = ticker.institutional_holders
        if holders is None or holders.empty:
            return None
    except Exception:
        return None

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ticker", "holder", "shares", "date_reported", "pct_out", "value"])

    for _, row in holders.iterrows():
        writer.writerow([
            profile.yfinance_ticker,
            row.get("Holder", ""),
            int(row["Shares"]) if "Shares" in row else "",
            str(row.get("Date Reported", "")),
            round(row["pctHeld"] * 100, 2) if "pctHeld" in row and row["pctHeld"] else "",
            int(row["Value"]) if "Value" in row and row["Value"] else "",
        ])

    csv_text = output.getvalue()

    return Document.create(
        ticker=profile.ticker,
        source_key="yfinance",
        source_name="Yahoo Finance",
        source_url=url,
        doc_type="holders",
        title=f"{profile.name} institutional holders",
        raw_text=csv_text,
        as_of_date=as_of,
        metadata={"finance_ticker": profile.yfinance_ticker, "format": "csv"},
    )


# ── Formatting helpers ───────────────────────────────────────────────

def _fmt_large(v) -> str:
    if v is None:
        return "N/A"
    if abs(v) >= 1e9:
        return f"{v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.1f}M"
    return f"{v:,.0f}"


def _fmt_int(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:,}"


def _pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{v*100:.1f}%" if abs(v) < 1 else f"{v:.1f}%"
