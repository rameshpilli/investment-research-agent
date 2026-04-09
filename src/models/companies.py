"""
Company Configuration Models
==============================

Pydantic models and helpers that define the universe of supported
companies. Each CompanyConfig holds identifiers, connector toggles,
and exchange metadata needed by every pipeline step.

Key class:
- CompanyConfig          -- immutable company profile used across the pipeline

Key functions:
- get_company            -- look up a CompanyConfig by ticker
- list_supported_tickers -- return all configured ticker strings
- expected_materials     -- list material types expected for a company
"""

from __future__ import annotations

from urllib.parse import quote_plus

from pydantic import BaseModel, Field


class CompanyConfig(BaseModel):
    ticker: str
    name: str
    slug: str
    yfinance_ticker: str
    edgar_cik: str | None = None
    newsweb_query: str | None = None
    ir_url: str | None = None
    enabled_connectors: list[str]
    exchange: str
    country: str
    currency: str
    sec_registered: bool
    aliases: list[str] = Field(default_factory=list)
    web_queries: list[str] = Field(default_factory=list)
    peer_tickers: list[str] = Field(
        default_factory=list,
        description="Peer company tickers for relative valuation context.",
    )

    @property
    def newsweb_url(self) -> str | None:
        if not self.newsweb_query:
            return None
        query = quote_plus(self.newsweb_query)
        return f"https://newsweb.oslobors.no/search?query={query}"


COMPANIES: dict[str, CompanyConfig] = {
    "SOC US": CompanyConfig(
        ticker="SOC US",
        name="Sable Offshore",
        slug="soc_us",
        yfinance_ticker="SOC",
        edgar_cik="0001831481",
        newsweb_query=None,
        ir_url=None,
        enabled_connectors=["edgar", "yfinance", "web_search"],
        exchange="NYSE",
        country="US",
        currency="USD",
        sec_registered=True,
        aliases=["SOC", "SOC US EQUITY"],
        web_queries=["Sable Offshore earnings transcript", "Sable Offshore oil and gas news"],
        peer_tickers=["FANG", "PR", "CRC"],
    ),
    "AKSO NO": CompanyConfig(
        ticker="AKSO NO",
        name="Aker Solutions",
        slug="akso_no",
        yfinance_ticker="AKSO.OL",
        edgar_cik=None,
        newsweb_query="AKER SOLUTIONS",
        ir_url="https://www.akersolutions.com/investors/annual-reports/",
        enabled_connectors=["newsweb", "company_ir", "yfinance", "web_search"],
        exchange="OSE",
        country="NO",
        currency="NOK",
        sec_registered=False,
        aliases=["AKSO", "AKSO.OL", "AKSO NO EQUITY"],
        web_queries=["Aker Solutions quarterly report", "Aker Solutions analyst commentary"],
        peer_tickers=["TGS.OL", "SUB.OL", "BORR"],
    ),
}


def _index_keys() -> dict[str, CompanyConfig]:
    indexed: dict[str, CompanyConfig] = {}
    for config in COMPANIES.values():
        indexed[config.ticker.upper()] = config
        indexed[config.yfinance_ticker.upper()] = config
        for alias in config.aliases:
            indexed[alias.upper()] = config
    return indexed


COMPANY_INDEX = _index_keys()


def get_company(ticker: str) -> CompanyConfig:
    normalized = ticker.strip().upper()
    config = COMPANY_INDEX.get(normalized)
    if config is None:
        supported = ", ".join(sorted(COMPANIES))
        raise ValueError(f"Unsupported ticker '{ticker}'. Supported tickers: {supported}")
    return config


def list_supported_tickers() -> list[str]:
    return sorted(COMPANIES)


class ExpectedMaterial(BaseModel):
    """What document types should exist for this company type."""

    material: str
    criticality: str
    severity: str = Field(
        default="Important",
        description="Impact if missing: Blocking, Important, or Minor.",
    )
    why: str
    min_count: int | None = Field(
        default=None,
        description="Minimum expected documents of this type.",
    )
    lookback_years: int | None = Field(
        default=None,
        description="How far back documents should cover.",
    )


def expected_materials(profile: CompanyConfig) -> list[tuple[str, str, str]]:
    """What document types should exist for this company type?

    Returns simple (material, criticality, why) tuples for backward
    compatibility.  Use ``expected_materials_rich()`` for the full model.
    """
    return [(m.material, m.criticality, m.why) for m in expected_materials_rich(profile)]


def expected_materials_rich(profile: CompanyConfig) -> list[ExpectedMaterial]:
    """Rich expected materials with counts, lookback, and severity."""
    if profile.sec_registered:
        return [
            ExpectedMaterial(
                material="10-K", criticality="Critical", severity="Blocking",
                why="Annual filings anchor the business and risk review.",
                min_count=2, lookback_years=3,
            ),
            ExpectedMaterial(
                material="10-Q", criticality="Important", severity="Important",
                why="Quarterly filings show trend shifts and updates.",
                min_count=3, lookback_years=2,
            ),
            ExpectedMaterial(
                material="8-K", criticality="Important", severity="Important",
                why="Material events can change the thesis quickly.",
                min_count=1, lookback_years=2,
            ),
            ExpectedMaterial(
                material="transcript", criticality="Nice-to-have", severity="Minor",
                why="Call transcripts expose tone shifts and management candor.",
                min_count=1, lookback_years=1,
            ),
            ExpectedMaterial(
                material="price_history", criticality="Important", severity="Important",
                why="Price history needed to assess what the market is pricing in.",
                min_count=1, lookback_years=3,
            ),
            ExpectedMaterial(
                material="competitor_filings", criticality="Nice-to-have", severity="Minor",
                why="Comparative context helps stress-test the thesis.",
            ),
        ]
    return [
        ExpectedMaterial(
            material="regulatory_announcement", criticality="Critical", severity="Blocking",
            why="Regulatory releases are the main disclosure channel.",
            min_count=2, lookback_years=2,
        ),
        ExpectedMaterial(
            material="annual_report", criticality="Critical", severity="Blocking",
            why="Annual reports anchor business description and risks.",
            min_count=2, lookback_years=3,
        ),
        ExpectedMaterial(
            material="investor_presentation", criticality="Important", severity="Important",
            why="Presentations reveal management emphasis and forward strategy.",
            min_count=1, lookback_years=2,
        ),
        ExpectedMaterial(
            material="price_history", criticality="Important", severity="Important",
            why="Price history needed to assess what the market is pricing in.",
            min_count=1, lookback_years=3,
        ),
        ExpectedMaterial(
            material="analyst_commentary", criticality="Nice-to-have", severity="Minor",
            why="Secondary views help surface non-consensus risks.",
        ),
        ExpectedMaterial(
            material="competitor_filings", criticality="Nice-to-have", severity="Minor",
            why="Comparative context helps stress-test the thesis.",
        ),
    ]
