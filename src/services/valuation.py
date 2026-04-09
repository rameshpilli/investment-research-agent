"""
Valuation Snapshot
===================

Reads the Yahoo Finance summary.json and price_history.csv from
``data/raw/`` and computes a compact valuation snapshot that the
Phase 2 agent can use to assess whether a stock is rich, fair, or
cheap relative to its fundamentals.

This is deterministic — no LLM, no tokens. It produces a dict and
a human-readable text block that can be injected into the dossier
prompt or used directly in the deterministic fallback.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.config import settings
from src.models.companies import CompanyConfig


def load_valuation_snapshot(profile: CompanyConfig) -> dict | None:
    """Load and compute a valuation snapshot from raw market data.

    Returns None if no market data is available.
    """
    raw_dir = settings.raw_data_dir / profile.slug / "market_data"
    summary = _load_summary(raw_dir / "summary.json")
    prices = _load_price_tail(raw_dir / "price_history.csv", tail=20)

    if not summary and not prices:
        return None

    data = summary.get("data", {}) if summary else {}

    snapshot: dict = {
        "ticker": data.get("ticker") or profile.yfinance_ticker,
        "company_name": data.get("company_name") or profile.name,
        "currency": data.get("currency") or profile.currency,
    }

    # Price context
    for key in ("last_close", "52w_high", "52w_low", "52w_change",
                "50d_avg", "200d_avg", "beta"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Position in 52W range
    if "52w_high" in snapshot and "52w_low" in snapshot and "last_close" in snapshot:
        range_size = snapshot["52w_high"] - snapshot["52w_low"]
        if range_size > 0:
            snapshot["pct_from_52w_high"] = round(
                (snapshot["52w_high"] - snapshot["last_close"]) / snapshot["52w_high"] * 100, 1
            )
            snapshot["position_in_range"] = round(
                (snapshot["last_close"] - snapshot["52w_low"]) / range_size * 100, 1
            )

    # Valuation multiples
    for key in ("forward_pe", "trailing_pe", "price_to_book",
                "ev_to_ebitda", "ev_to_revenue",
                "market_cap", "enterprise_value"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Financials
    for key in ("total_revenue", "ebitda", "ebitda_margin",
                "free_cashflow", "total_cash", "total_debt",
                "debt_to_equity", "current_ratio", "return_on_equity"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Analyst
    for key in ("analyst_rating", "analyst_count",
                "target_high", "target_low", "target_mean", "target_median"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Short interest
    for key in ("short_ratio", "short_pct_float", "shares_short"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Performance
    for key in ("return_1m", "return_3m", "return_6m", "return_1y"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Risk
    for key in ("overall_risk", "audit_risk", "board_risk"):
        if data.get(key) is not None:
            snapshot[key] = data[key]

    # Flags for the agent
    snapshot["flags"] = _compute_flags(snapshot)

    # Recent prices (last 5 trading days)
    if prices:
        snapshot["recent_prices"] = prices[-5:]

    return snapshot


def valuation_snapshot_text(snapshot: dict | None) -> str:
    """Render a valuation snapshot as text for the Phase 2 agent."""
    if not snapshot:
        return "No market data available for valuation context."

    lines = [
        f"VALUATION SNAPSHOT: {snapshot.get('company_name')} ({snapshot.get('ticker')})",
        f"Currency: {snapshot.get('currency', '?')}",
        "",
    ]

    # Price
    if "last_close" in snapshot:
        lines.append(f"Last Close: {snapshot['last_close']}")
    if "52w_high" in snapshot:
        lines.append(f"52W Range: {snapshot.get('52w_low')} — {snapshot.get('52w_high')}")
    if "pct_from_52w_high" in snapshot:
        lines.append(f"  {snapshot['pct_from_52w_high']}% below 52W high, position in range: {snapshot.get('position_in_range')}%")

    # Multiples
    multiples = []
    if "forward_pe" in snapshot:
        multiples.append(f"Fwd P/E: {snapshot['forward_pe']}")
    if "trailing_pe" in snapshot:
        multiples.append(f"Trail P/E: {snapshot['trailing_pe']}")
    if "price_to_book" in snapshot:
        multiples.append(f"P/B: {snapshot['price_to_book']}")
    if "ev_to_ebitda" in snapshot:
        multiples.append(f"EV/EBITDA: {snapshot['ev_to_ebitda']}")
    if multiples:
        lines.extend(["", "Multiples: " + " | ".join(multiples)])

    # Market cap
    if "market_cap" in snapshot:
        mc = snapshot["market_cap"]
        mc_str = f"{mc/1e9:.2f}B" if mc >= 1e9 else f"{mc/1e6:.0f}M"
        lines.append(f"Market Cap: {mc_str} {snapshot.get('currency', '')}")
    if "enterprise_value" in snapshot:
        ev = snapshot["enterprise_value"]
        ev_str = f"{ev/1e9:.2f}B" if ev >= 1e9 else f"{ev/1e6:.0f}M"
        lines.append(f"Enterprise Value: {ev_str} {snapshot.get('currency', '')}")

    # Analyst
    if "analyst_rating" in snapshot:
        lines.extend([
            "",
            f"Analyst: {snapshot['analyst_rating']} ({snapshot.get('analyst_count', '?')} analysts)",
            f"Targets: {snapshot.get('target_low')} — {snapshot.get('target_mean')} — {snapshot.get('target_high')}",
        ])

    # Short interest
    if "short_pct_float" in snapshot:
        pct = snapshot["short_pct_float"]
        pct_str = f"{pct*100:.1f}%" if pct < 1 else f"{pct:.1f}%"
        lines.extend(["", f"Short Interest: {pct_str} of float, ratio {snapshot.get('short_ratio', '?')} days"])

    # Performance
    import math
    perfs = []
    for label, key in [("1M", "return_1m"), ("3M", "return_3m"), ("6M", "return_6m"), ("1Y", "return_1y")]:
        val = snapshot.get(key)
        if val is not None and isinstance(val, (int, float)) and not math.isnan(val):
            perfs.append(f"{label}: {val:+.1f}%")
    if perfs:
        lines.extend(["", "Performance: " + " | ".join(perfs)])

    # Flags
    flags = snapshot.get("flags", [])
    if flags:
        lines.extend(["", "Flags:"] + [f"  - {f}" for f in flags])

    return "\n".join(lines)


def _compute_flags(s: dict) -> list[str]:
    """Derive analyst-relevant flags from the snapshot."""
    flags: list[str] = []

    if s.get("pct_from_52w_high") and s["pct_from_52w_high"] > 40:
        flags.append("Trading >40% below 52W high")
    if s.get("position_in_range") and s["position_in_range"] > 90:
        flags.append("Near 52W high")
    if s.get("position_in_range") and s["position_in_range"] < 10:
        flags.append("Near 52W low")

    if s.get("short_pct_float"):
        spf = s["short_pct_float"]
        pct = spf * 100 if spf < 1 else spf
        if pct > 20:
            flags.append(f"High short interest ({pct:.1f}% of float)")

    if s.get("debt_to_equity") and s["debt_to_equity"] > 150:
        flags.append(f"High leverage (D/E: {s['debt_to_equity']})")

    if s.get("current_ratio") and s["current_ratio"] < 0.5:
        flags.append(f"Low current ratio ({s['current_ratio']})")

    if s.get("overall_risk") and s["overall_risk"] >= 8:
        flags.append(f"High governance risk score ({s['overall_risk']}/10)")

    if s.get("beta") is not None and abs(s["beta"]) < 0.2:
        flags.append(f"Very low beta ({s['beta']}) — limited price data or illiquid")

    target_mean = s.get("target_mean")
    last = s.get("last_close")
    if target_mean and last and last > 0:
        upside = (target_mean - last) / last * 100
        if upside > 50:
            flags.append(f"Analyst target implies {upside:.0f}% upside")
        elif upside < -10:
            flags.append(f"Trading above mean analyst target")

    return flags


def _load_summary(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_price_tail(path: Path, tail: int = 20) -> list[dict]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
        rows = list(csv.DictReader(text.strip().splitlines()))
        return rows[-tail:] if rows else []
    except Exception:
        return []
