"""
PDF Parser — Thin Wrapper Around LiteParse
============================================

Provides PDF text extraction using LiteParse (LlamaIndex's open-source
document parser) when available, with pypdf as fallback.

LiteParse advantages over pypdf:
- Preserves spatial layout (table columns stay aligned)
- Bounding box metadata per text element
- Better handling of multi-column layouts
- OCR support for scanned PDFs (optional, via Tesseract.js)

LiteParse is a Node.js CLI tool. This wrapper shells out to it via
subprocess and parses the JSON output.

Requirements:
    npm i -g @llamaindex/liteparse    (or: npx @llamaindex/liteparse)

Usage:
    from src.services.pdf_parser import parse_pdf_bytes

    text = parse_pdf_bytes(pdf_bytes)
    # Returns full text with spatial layout preserved.
    # Falls back to pypdf if liteparse is not installed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

LITEPARSE_VERSION_TIMEOUT_SECONDS = 15
LITEPARSE_PARSE_TIMEOUT_SECONDS = 120
LITEPARSE_ERROR_PREVIEW_CHARS = 500

# Check once at import time whether liteparse CLI is available
_LITEPARSE_AVAILABLE: bool | None = None


def _check_liteparse() -> bool:
    """Check if the liteparse CLI is available via npx or global install."""
    global _LITEPARSE_AVAILABLE
    if _LITEPARSE_AVAILABLE is not None:
        return _LITEPARSE_AVAILABLE

    # Check for global install first (faster)
    if shutil.which("lit"):
        _LITEPARSE_AVAILABLE = True
        return True

    # Check for npx availability
    if shutil.which("npx"):
        try:
            result = subprocess.run(
                ["npx", "@llamaindex/liteparse", "--version"],
                capture_output=True,
                text=True,
                timeout=LITEPARSE_VERSION_TIMEOUT_SECONDS,
            )
            _LITEPARSE_AVAILABLE = result.returncode == 0
        except Exception:
            _LITEPARSE_AVAILABLE = False
    else:
        _LITEPARSE_AVAILABLE = False

    return _LITEPARSE_AVAILABLE


def parse_pdf_bytes(
    data: bytes,
    max_pages: int | None = None,
    use_ocr: bool = False,
) -> str:
    """Extract text from PDF bytes. Uses LiteParse if available, falls back to pypdf.

    Args:
        data: Raw PDF file bytes.
        max_pages: Max pages to parse (None = all).
        use_ocr: Enable OCR for scanned PDFs (LiteParse only, requires Tesseract.js).

    Returns:
        Extracted text as a string. Spatial layout is preserved when using LiteParse.
    """
    if _check_liteparse():
        try:
            return _parse_with_liteparse(data, max_pages=max_pages, use_ocr=use_ocr)
        except Exception as exc:
            logger.warning("LiteParse failed, falling back to pypdf: %s", exc)

    return _parse_with_pypdf(data, max_pages=max_pages)


def _parse_with_liteparse(
    data: bytes,
    max_pages: int | None = None,
    use_ocr: bool = False,
) -> str:
    """Parse PDF using LiteParse CLI (Node.js)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / "input.pdf"
        out_path = Path(tmp_dir) / "output.json"
        pdf_path.write_bytes(data)

        # Build command
        cmd: list[str] = []
        if shutil.which("lit"):
            cmd = ["lit"]
        else:
            cmd = ["npx", "@llamaindex/liteparse"]

        cmd.extend(["parse", str(pdf_path), "--format", "json", "-q", "-o", str(out_path)])

        if max_pages:
            cmd.extend(["--max-pages", str(max_pages)])
        if not use_ocr:
            cmd.append("--no-ocr")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=LITEPARSE_PARSE_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            error_preview = result.stderr[:LITEPARSE_ERROR_PREVIEW_CHARS]
            raise RuntimeError(f"LiteParse failed (exit {result.returncode}): {error_preview}")

        if not out_path.exists():
            raise RuntimeError("LiteParse produced no output file")

        output = json.loads(out_path.read_text(encoding="utf-8"))

        # Extract text from JSON pages
        pages = output.get("pages", [])
        page_texts = []
        for page in pages:
            text = page.get("text", "").strip()
            if text:
                page_texts.append(text)

        return "\n\n".join(page_texts)


def _parse_with_pypdf(data: bytes, max_pages: int | None = None) -> str:
    """Fallback: parse PDF using pypdf (basic text extraction, no table structure)."""
    try:
        from io import BytesIO
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        pages = reader.pages
        if max_pages:
            pages = pages[:max_pages]
        return "\n".join(page.extract_text() or "" for page in pages).strip()
    except Exception as exc:
        logger.warning("pypdf failed: %s", exc)
        return ""
