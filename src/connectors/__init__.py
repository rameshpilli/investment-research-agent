"""External data connectors."""

from src.connectors.base import BaseResearchConnector
from src.connectors.company_ir import CompanyIRConnector
from src.connectors.edgar import EdgarConnector
from src.connectors.local_files import LocalFilesConnector
from src.connectors.newsweb import NewswebConnector
from src.connectors.web_search import WebSearchConnector
from src.connectors.yfinance_client import YFinanceConnector

__all__ = [
    "BaseResearchConnector",
    "CompanyIRConnector",
    "EdgarConnector",
    "LocalFilesConnector",
    "NewswebConnector",
    "WebSearchConnector",
    "YFinanceConnector",
]
