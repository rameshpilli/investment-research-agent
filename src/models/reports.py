"""Structured report schemas and markdown renderers for Claude Agent SDK output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Citation model ───────────────────────────────────────────────────

class ReportCitation(BaseModel):
    """Source reference with both human-readable fields and corpus traceability."""

    # Human-readable (for footnotes and markdown rendering)
    source_name: str = Field(description="Human-readable source name.")
    doc_type: str | None = Field(default=None, description="Document type such as 10-K, 10-Q, annual_report, or news.")
    filing_date: str | None = Field(default=None, description="Document filing or publication date, if known.")
    title: str | None = Field(default=None, description="Document title, if known.")
    excerpt: str | None = Field(default=None, description="Short supporting excerpt or evidence snippet from the source.")

    # Corpus traceability (for programmatic verification)
    doc_id: str | None = Field(default=None, description="Internal document identifier from the corpus store.")
    chunk_id: str | None = Field(default=None, description="Internal chunk identifier within the corpus.")
    section: str | None = Field(default=None, description="Logical section, e.g. 'Risk Factors', 'MD&A'.")
    page: int | None = Field(default=None, description="Page number if known.")
    url: str | None = Field(default=None, description="Link to source document or viewer.")


class CitedBlock(BaseModel):
    """A text field with zero or more supporting citations."""

    text: str = Field(description="Content for this section in plain text.")
    citations: list[ReportCitation] = Field(default_factory=list, description="Source citations supporting the text.")


# ── Phase 1: Ingestion & Gap Analysis ────────────────────────────────

class Phase1SourceCoverageItem(BaseModel):
    source: str
    document: str
    what_to_expect: CitedBlock


class CorpusFreshnessItem(BaseModel):
    connector: str
    status: str
    last_fetched: str | None = None
    document_count: int = 0
    refresh_reason: str | None = None


class MissingContextItem(BaseModel):
    expected_material: str
    criticality: str
    severity: Literal["Blocking", "Important", "Minor"] = Field(
        default="Important",
        description="Impact on the investment decision if this material remains missing.",
    )
    why_it_matters: str
    status: Literal["Present", "Missing"]
    citations: list[ReportCitation] = Field(default_factory=list)


class Phase1StructuredReport(BaseModel):
    source_coverage: list[Phase1SourceCoverageItem] = Field(default_factory=list)
    corpus_freshness: list[CorpusFreshnessItem] = Field(default_factory=list)
    quality_assessment: CitedBlock
    missing_context: list[MissingContextItem] = Field(default_factory=list)


# ── Phase 2: Adversarial Deep Research Dossier ───────────────────────

class ReportFinding(BaseModel):
    title: str
    analysis: CitedBlock


class Phase2RiskItem(BaseModel):
    title: str
    direction: Literal["Downside", "Upside", "Mixed"] = "Downside"
    analysis: CitedBlock


class Phase2StructuredReport(BaseModel):
    executive_verdict: Literal["Proceed", "Stop", "Needs More Info"]
    verdict_rationale: CitedBlock
    thesis_summary: CitedBlock = Field(
        default_factory=lambda: CitedBlock(text=""),
        description="2-4 sentence investment thesis: what the company is, what the bet is, and why.",
    )
    company_overview: CitedBlock
    valuation_context: CitedBlock | None = Field(
        default=None,
        description="How the market is pricing this — multiples, rich/fair/cheap vs history or peers.",
    )
    top_nonconsensus_risks: list[Phase2RiskItem] = Field(
        default_factory=list,
        description="3-5 risks (upside AND downside) that the market may be missing.",
    )
    contradictions_tone_shifts: list[ReportFinding] = Field(default_factory=list)
    what_would_change_verdict: list[ReportFinding] = Field(default_factory=list)
    information_gaps: list[ReportFinding] = Field(default_factory=list)

    # Backward compatibility — old schema used top_bear_risks
    @property
    def top_bear_risks(self) -> list[Phase2RiskItem]:
        return self.top_nonconsensus_risks


# ── Phase 3: Analyst Brief ───────────────────────────────────────────

class Phase3StructuredReport(BaseModel):
    thesis_summary: CitedBlock
    key_findings: list[ReportFinding] = Field(default_factory=list)
    top_risks: list[ReportFinding] = Field(default_factory=list)
    critical_gaps: list[ReportFinding] = Field(default_factory=list)
    recommended_next_steps: list[ReportFinding] = Field(default_factory=list)


# ── Follow-up Q&A ────────────────────────────────────────────────────

class FollowUpStructuredAnswer(BaseModel):
    can_answer: bool = Field(description="Whether the question can be answered from the ingested materials.")
    answer: CitedBlock = Field(description="Grounded answer content and supporting citations.")
    missing_sources: list[str] = Field(default_factory=list, description="Sources that would be needed if the question cannot be answered.")


# ── Citation registry and rendering ──────────────────────────────────

# ── Validation ────────────────────────────────────────────────────────

def validate_phase2_report(report: Phase2StructuredReport) -> list[str]:
    """Check structural completeness of a Phase 2 dossier.

    Returns a list of issues found.  Empty list = report is valid.
    """
    issues: list[str] = []

    if not report.verdict_rationale.text.strip():
        issues.append("verdict_rationale is empty")

    if not report.thesis_summary or not report.thesis_summary.text.strip():
        issues.append("thesis_summary is empty — no investment thesis stated")

    if not report.company_overview.text.strip():
        issues.append("company_overview is empty")

    if len(report.top_nonconsensus_risks) < 3:
        issues.append(f"only {len(report.top_nonconsensus_risks)} risks — expected at least 3")

    # Check that risks have citations
    risks_without_citations = [
        r.title for r in report.top_nonconsensus_risks
        if not r.analysis.citations
    ]
    if risks_without_citations:
        issues.append(f"risks without citations: {', '.join(risks_without_citations)}")

    if not report.information_gaps:
        issues.append("no information_gaps listed")

    # Overall citation count
    total = _count_citations(report.model_dump())
    if total < 3:
        issues.append(f"very low citation count ({total}) — dossier may lack grounding")

    return issues


@dataclass
class CitationRegistry:
    citations: list[ReportCitation] = field(default_factory=list)
    _index: dict[tuple[Any, ...], int] = field(default_factory=dict)

    def markers(self, citations: list[ReportCitation]) -> str:
        markers: list[str] = []
        for citation in citations:
            key = (
                citation.source_name,
                citation.doc_type,
                citation.filing_date,
                citation.title,
                citation.doc_id,
            )
            index = self._index.get(key)
            if index is None:
                index = len(self.citations) + 1
                self._index[key] = index
                self.citations.append(citation)
            markers.append(f"[{index}]")
        return " ".join(markers)

    def footnotes(self) -> str:
        lines: list[str] = []
        for index, citation in enumerate(self.citations, start=1):
            parts = [f"[{index}] {citation.source_name}"]
            if citation.doc_type:
                parts.append(f"({citation.doc_type})")
            if citation.filing_date:
                parts.append(f"dated {citation.filing_date}")
            if citation.title:
                parts.append(f"- {citation.title}")
            if citation.url:
                parts.append(f"<{citation.url}>")
            if citation.excerpt:
                parts.append(f':: "{citation.excerpt}"')
            lines.append(" ".join(parts))
        return "\n".join(lines)


def citation_summary_for_model(model: BaseModel) -> dict[str, int | float]:
    """Summarize citation counts with heuristic verification.

    Use ``verify_citations_against_corpus()`` for true source validation.
    """
    all_citations = _collect_citations(model.model_dump())
    total = len(all_citations)
    verified = sum(1 for c in all_citations if _has_traceable_fields(c))
    return {
        "total": total,
        "verified": verified,
        "verification_rate": verified / total if total > 0 else 0.0,
    }


def verify_citations_against_corpus(
    model: BaseModel,
    corpus_store: Any,
    profile: Any,
) -> dict[str, Any]:
    """True citation verification — checks each excerpt against actual corpus text.

    For each citation that has a doc_id or chunk_id, looks up the source
    text in SQLite and checks whether the excerpt actually appears in it.

    Returns:
        {
            "total": int,
            "verified": int,              # excerpt found in corpus text
            "unverified": int,            # has fields but doc/excerpt not in corpus
            "unverified_traceable": int,  # doc exists but excerpt didn't match (likely paraphrased)
            "ungrounded": int,            # no traceable fields at all
            "verification_rate": float,
            "details": [
                {"citation": {...}, "status": "verified"|"unverified"|"unverified_traceable"|"ungrounded", "reason": str}
            ]
        }
    """
    all_citations = _collect_citations(model.model_dump())
    total = len(all_citations)
    verified = 0
    unverified = 0
    unverified_traceable = 0
    ungrounded = 0
    details: list[dict[str, Any]] = []

    for citation in all_citations:
        doc_id = citation.get("doc_id")
        chunk_id = citation.get("chunk_id")
        excerpt = (citation.get("excerpt") or "").strip()

        # No traceable fields at all
        if not doc_id and not chunk_id and not excerpt:
            ungrounded += 1
            details.append({
                "citation": _citation_label(citation),
                "status": "ungrounded",
                "reason": "No doc_id, chunk_id, or excerpt provided",
            })
            continue

        # Has fields but no corpus store to check against
        if corpus_store is None or profile is None:
            # Can't verify without corpus — count as heuristically traceable
            if excerpt or doc_id:
                verified += 1
                details.append({
                    "citation": _citation_label(citation),
                    "status": "heuristically_verified",
                    "reason": "Has traceable fields (corpus not available for deep check)",
                })
            else:
                unverified += 1
                details.append({
                    "citation": _citation_label(citation),
                    "status": "unverified",
                    "reason": "No excerpt to verify",
                })
            continue

        # Deep verification: look up candidate texts from corpus
        source_texts = _get_source_texts(corpus_store, profile, doc_id, chunk_id)

        if not source_texts:
            unverified += 1
            details.append({
                "citation": _citation_label(citation),
                "status": "unverified",
                "reason": "doc_id/chunk_id not found in corpus",
            })
            continue

        if not excerpt:
            # Has a valid doc_id but no excerpt — document exists, claim not checkable
            verified += 1
            details.append({
                "citation": _citation_label(citation),
                "status": "verified",
                "reason": "Document exists in corpus (no excerpt to match)",
            })
            continue

        # Try matching the excerpt against each candidate text (primary chunk,
        # sibling chunks, full document) until we find a hit.
        matched = False
        match_reason = "Excerpt found in corpus text"
        normalized_excerpt = " ".join(excerpt.lower().split())
        # Tier 2: also prepare a numeric-normalized variant
        numeric_excerpt = _normalize_numeric(normalized_excerpt)

        for source_text in source_texts:
            normalized_source = " ".join(source_text.lower().split())

            # --- Pass 1: prefix windows (whitespace-normalized) ---
            for length in (80, 55, 40, 28, 22):
                prefix = normalized_excerpt[:length].strip()
                if len(prefix) >= 12 and prefix in normalized_source:
                    matched = True
                    break
            if matched:
                break

            # --- Pass 2: signature phrase (first 4 substantive tokens) ---
            tokens = [t for t in normalized_excerpt.split() if len(t) > 2][:4]
            if len(tokens) >= 2:
                sig = " ".join(tokens)
                if len(sig) >= 10 and sig in normalized_source:
                    matched = True
                    match_reason = "Excerpt signature phrase found in corpus text"
                    break

            # --- Pass 3: numeric-normalized prefix windows ---
            numeric_source = _normalize_numeric(normalized_source)
            if numeric_excerpt != normalized_excerpt or numeric_source != normalized_source:
                for length in (80, 55, 40, 28, 22):
                    prefix = numeric_excerpt[:length].strip()
                    if len(prefix) >= 12 and prefix in numeric_source:
                        matched = True
                        match_reason = "Excerpt found after number normalization"
                        break
                if matched:
                    break

            # --- Pass 4: structured data token co-occurrence ---
            if len(normalized_excerpt) >= 8:
                looks_structured = bool(
                    re.search(r":\s*\d{4,}", normalized_excerpt)
                    or re.search(
                        r"\b(total_cash|total_debt|free_cash|market_cap|enterprise|ebitda|revenue)\b",
                        normalized_excerpt,
                    )
                )
                if looks_structured:
                    strong = re.findall(r"[a-z0-9]{5,}", normalized_excerpt)
                    stop = frozenset({
                        "company", "report", "annual", "current", "market", "summary",
                        "filing", "total", "million", "billion", "common", "stock",
                    })
                    strong = [t for t in strong if t not in stop]
                    if len(strong) >= 2 and all(t in normalized_source for t in strong[:8]):
                        matched = True
                        match_reason = "Excerpt key tokens found in corpus text (structured source)"
                        break

            # --- Pass 5: token overlap ratio ---
            # Handles paraphrased excerpts (e.g. from Norwegian filings) where
            # exact substrings won't match but most meaningful tokens appear.
            _token_stop = frozenset({
                "the", "and", "for", "are", "but", "not", "you", "all", "can",
                "has", "was", "one", "our", "out", "its", "may", "that", "this",
                "with", "have", "from", "been", "were", "what", "when", "where",
                "which", "about", "does", "they", "their", "will", "would",
                "could", "should", "also", "than", "into", "each", "other",
                "more", "some", "such", "these", "those",
            })
            excerpt_tokens = {
                t for t in re.findall(r"[a-z0-9]+", normalized_excerpt)
                if len(t) > 2 and t not in _token_stop
            }
            if len(excerpt_tokens) >= 3:
                source_tokens = set(re.findall(r"[a-z0-9]+", normalized_source))
                overlap = excerpt_tokens & source_tokens
                ratio = len(overlap) / len(excerpt_tokens)
                if ratio >= 0.65 and len(overlap) >= 3:
                    matched = True
                    match_reason = f"Excerpt token overlap {ratio:.0%} ({len(overlap)}/{len(excerpt_tokens)} tokens)"
                    break

            # --- Pass 6: fuzzy substring matching ---
            # Uses SequenceMatcher for near-matches (handles minor rephrasing,
            # spacing differences, and translation variance).
            if len(normalized_excerpt) >= 15:
                from difflib import SequenceMatcher
                # Slide a window of ~excerpt length across source, check similarity
                elen = len(normalized_excerpt)
                best_ratio = 0.0
                step = max(1, elen // 4)
                for i in range(0, max(1, len(normalized_source) - elen + 1), step):
                    window = normalized_source[i : i + elen + 20]
                    r = SequenceMatcher(None, normalized_excerpt, window).ratio()
                    if r > best_ratio:
                        best_ratio = r
                    if r >= 0.70:
                        matched = True
                        match_reason = f"Excerpt fuzzy-matched (similarity: {best_ratio:.0%})"
                        break
                if matched:
                    break

        if matched:
            verified += 1
            details.append({
                "citation": _citation_label(citation),
                "status": "verified",
                "reason": match_reason,
            })
        else:
            # Tier 3: distinguish traceable-but-unverified from fully unverified.
            # If we have a valid doc_id that resolved to text, the claim IS
            # traceable to a document — the excerpt just didn't substring-match.
            has_doc = bool(doc_id) and len(source_texts) > 0
            if has_doc:
                unverified_traceable += 1
                details.append({
                    "citation": _citation_label(citation),
                    "status": "unverified_traceable",
                    "reason": "Excerpt not found in corpus text (document exists — likely paraphrased)",
                })
            else:
                unverified += 1
                details.append({
                    "citation": _citation_label(citation),
                    "status": "unverified",
                    "reason": "Excerpt not found in corpus text",
                })

    return {
        "total": total,
        "verified": verified,
        "unverified": unverified,
        "unverified_traceable": unverified_traceable,
        "ungrounded": ungrounded,
        "verification_rate": verified / total if total > 0 else 0.0,
        "details": details,
    }


def _has_traceable_fields(citation: dict) -> bool:
    """Heuristic check — citation has fields that could be traced."""
    return bool(
        citation.get("doc_id")
        or citation.get("chunk_id")
        or citation.get("excerpt")
        or citation.get("url")
    )


def _citation_label(citation: dict) -> str:
    """Short label for a citation in verification details."""
    parts = [citation.get("source_name", "unknown")]
    if citation.get("doc_type"):
        parts.append(citation["doc_type"])
    if citation.get("filing_date"):
        parts.append(citation["filing_date"])
    return " | ".join(parts)


def _get_source_texts(corpus_store: Any, profile: Any, doc_id: str | None, chunk_id: str | None) -> list[str]:
    """Look up candidate texts from the corpus for verification.

    Returns a list of source texts to try, ordered by specificity:
    1. The exact chunk (if chunk_id provided and found)
    2. Sibling chunks for the same doc_id
    3. Full document raw_text as final fallback

    This fixes false negatives where chunk_id points at the wrong slice
    but the excerpt exists in a neighbouring chunk of the same document.
    """
    texts: list[str] = []
    seen: set[int] = set()          # avoid duplicate texts by id(str)
    try:
        primary_doc_id = doc_id
        if chunk_id:
            chunks = corpus_store.get_chunks_by_ids(profile, [chunk_id])
            if chunks:
                texts.append(chunks[0].text)
                seen.add(id(chunks[0].text))
                # remember doc_id from the chunk for sibling lookup
                if not primary_doc_id:
                    primary_doc_id = getattr(chunks[0], "doc_id", None)
        # Sibling chunks for the same document
        if primary_doc_id:
            try:
                siblings = corpus_store.get_chunks_for_doc(profile, primary_doc_id)
                for sib in siblings:
                    if id(sib.text) not in seen:
                        texts.append(sib.text)
                        seen.add(id(sib.text))
            except Exception:
                pass
            # Full document fallback
            try:
                doc = corpus_store.get_document(profile, primary_doc_id)
                if doc and id(doc.raw_text) not in seen:
                    texts.append(doc.raw_text)
            except Exception:
                pass
    except Exception:
        pass
    return texts


def _collect_citations(value: Any) -> list[dict]:
    """Recursively collect all citation dicts from a model dump."""
    results: list[dict] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "citations" and isinstance(item, list):
                results.extend(item)
            else:
                results.extend(_collect_citations(item))
    elif isinstance(value, list):
        for item in value:
            results.extend(_collect_citations(item))
    return results


# ── Markdown renderers ───────────────────────────────────────────────

def render_phase1_markdown(
    company_name: str,
    ticker: str,
    report: Phase1StructuredReport,
    last_refreshed_at: str,
    verification: dict[str, Any] | None = None,
) -> str:
    registry = CitationRegistry()
    lines = [
        f"# Phase 1 Ingestion Report — {company_name} ({ticker})",
        "",
        "## Source Coverage Matrix",
        "| Source | Document | What The Analyst Can Expect To Find |",
        "| --- | --- | --- |",
    ]
    for item in report.source_coverage:
        lines.append(f"| {item.source} | {item.document} | {_render_cited_text(item.what_to_expect, registry)} |")

    lines.extend([
        "",
        "## Corpus Freshness",
        f"Last refreshed: {last_refreshed_at}",
        "",
        "| Connector | Status | Last Fetched | Documents | Refresh Reason |",
        "| --- | --- | --- | --- | --- |",
    ])
    for item in report.corpus_freshness:
        lines.append(
            f"| {item.connector} | {item.status} | {item.last_fetched or 'n/a'} | {item.document_count} | {item.refresh_reason or 'n/a'} |"
        )

    lines.extend([
        "",
        "## Quality Assessment",
        _render_cited_text(report.quality_assessment, registry),
        "",
        "## Missing Context Report",
        "| Expected Material | Criticality | Severity | Why It Matters | Status |",
        "| --- | --- | --- | --- | --- |",
    ])
    for item in report.missing_context:
        why = item.why_it_matters
        if item.citations:
            why = f"{why} {registry.markers(item.citations)}"
        lines.append(f"| {item.expected_material} | {item.criticality} | {item.severity} | {why} | {item.status} |")

    return _finish_markdown(lines, registry, citation_summary_for_model(report), verification)


def render_phase2_markdown(
    company_name: str,
    ticker: str,
    report: Phase2StructuredReport,
    verification: dict[str, Any] | None = None,
) -> str:
    registry = CitationRegistry()
    lines = [
        f"# Phase 2 Deep Research Dossier — {company_name} ({ticker})",
        "",
        "## Executive Verdict",
        f"**{report.executive_verdict}**. {_render_cited_text(report.verdict_rationale, registry)}",
    ]

    # Thesis summary
    if report.thesis_summary and report.thesis_summary.text:
        lines.extend([
            "",
            "## Thesis Summary",
            _render_cited_text(report.thesis_summary, registry),
        ])

    lines.extend([
        "",
        "## Company Overview",
        _render_cited_text(report.company_overview, registry),
    ])

    # Valuation context
    if report.valuation_context and report.valuation_context.text:
        lines.extend([
            "",
            "## Valuation Context",
            _render_cited_text(report.valuation_context, registry),
        ])

    # Non-consensus risks (upside + downside)
    lines.extend(["", "## Top Non-Consensus Risks"])
    risks = report.top_nonconsensus_risks
    if risks:
        lines.extend(
            f"- **{item.title}** ({item.direction}): {_render_cited_text(item.analysis, registry)}"
            for item in risks
        )
    else:
        lines.append("- No grounded risks were identified. [UNVERIFIED]")

    lines.extend(["", "## Contradictions & Tone Shifts"])
    lines.extend(_render_findings(report.contradictions_tone_shifts, registry))

    lines.extend(["", "## What Would Change This Verdict"])
    lines.extend(_render_findings(report.what_would_change_verdict, registry))

    lines.extend(["", "## Information Gaps"])
    lines.extend(_render_findings(report.information_gaps, registry))

    return _finish_markdown(lines, registry, citation_summary_for_model(report), verification)


def render_phase3_markdown(
    company_name: str,
    ticker: str,
    verdict: str,
    last_refreshed_at: str,
    document_count: int,
    report: Phase3StructuredReport,
    verification: dict[str, Any] | None = None,
) -> str:
    registry = CitationRegistry()
    lines = [
        f"# Phase 3 Analyst Brief — {company_name} ({ticker})",
        "",
        f"Date: {last_refreshed_at} | Verdict: {verdict}",
        "",
        "## Thesis Summary",
        _render_cited_text(report.thesis_summary, registry),
        "",
        "## Key Findings",
    ]
    lines.extend(_render_findings(report.key_findings, registry))
    lines.extend(["", "## Top Risks"])
    lines.extend(_render_findings(report.top_risks, registry))
    lines.extend(["", "## Critical Gaps"])
    lines.extend(_render_findings(report.critical_gaps, registry))
    lines.extend(["", "## Recommended Next Steps"])
    lines.extend(_render_findings(report.recommended_next_steps, registry))
    lines.extend(["", f"Sources: {document_count} documents ingested | Full dossier: phase2_dossier.md"])
    return _finish_markdown(lines, registry, citation_summary_for_model(report), verification)


def render_followup_markdown(report: FollowUpStructuredAnswer) -> str:
    registry = CitationRegistry()
    if not report.can_answer:
        missing = ", ".join(report.missing_sources) if report.missing_sources else "newer filings, additional regulatory releases, or more recent news"
        return (
            "This question cannot be answered from the ingested materials. "
            f"The following sources would need to be added: {missing}."
        )

    lines = [_render_cited_text(report.answer, registry)]
    if registry.citations:
        lines.extend(["", "Citations:", registry.footnotes()])
    return "\n".join(lines).strip()


# ── Internal helpers ─────────────────────────────────────────────────

def _render_findings(findings: list[ReportFinding], registry: CitationRegistry) -> list[str]:
    if not findings:
        return ["- No grounded findings were identified. [UNVERIFIED]"]
    return [f"- **{item.title}**: {_render_cited_text(item.analysis, registry)}" for item in findings]


def _render_cited_text(block: CitedBlock, registry: CitationRegistry) -> str:
    text = (block.text or "").strip()
    if not text:
        return "[UNVERIFIED]"
    markers = registry.markers(block.citations)
    return f"{text} {markers}".strip() if markers else f"{text} [UNVERIFIED]"


def _finish_markdown(
    lines: list[str],
    registry: CitationRegistry,
    summary: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> str:
    # Use deep verification if available, otherwise heuristic summary
    v = verification or summary
    total = v.get("total", 0)
    verified = v.get("verified", 0)
    rate = v.get("verification_rate", 0.0)
    method = "corpus-checked" if verification else "heuristic"
    lines.extend(
        [
            "",
            "## Citation Verification",
            f"- Verified citations: {verified}/{total} ({method})",
            f"- Verification rate: {rate:.0%}",
        ]
    )
    if verification:
        unverified_traceable = verification.get("unverified_traceable", 0)
        if unverified_traceable > 0:
            lines.append(f"- Unverified but traceable (paraphrased): {unverified_traceable}")
        ungrounded = verification.get("ungrounded", 0)
        if ungrounded > 0:
            lines.append(f"- Ungrounded claims: {ungrounded}")
    footnotes = registry.footnotes()
    if footnotes:
        lines.extend(["", "## Citations", footnotes])
    return "\n".join(lines).strip()


def _count_citations(value: Any) -> int:
    if isinstance(value, dict):
        total = 0
        for key, item in value.items():
            if key == "citations" and isinstance(item, list):
                total += len(item)
            else:
                total += _count_citations(item)
        return total
    if isinstance(value, list):
        return sum(_count_citations(item) for item in value)
    return 0


def enrich_citations_against_corpus(
    model: BaseModel,
    corpus_store: Any,
    profile: Any,
) -> BaseModel:
    """Best-effort citation enrichment using corpus metadata.

    LLM outputs often include human-readable citation fields (title/doc_type/date)
    but omit internal ids. This pass resolves missing ``doc_id``/``chunk_id`` so
    verification can perform true corpus checks.
    """
    if corpus_store is None or profile is None:
        return model
    try:
        snapshot = corpus_store.load_corpus(profile)
    except Exception:
        return model

    documents = snapshot.documents
    chunks = snapshot.chunks
    by_doc_id = {document.doc_id: document for document in documents}
    chunks_by_doc: dict[str, list[Any]] = {}
    for chunk in chunks:
        chunks_by_doc.setdefault(chunk.doc_id, []).append(chunk)

    payload = model.model_dump()
    updated = _enrich_citation_payload(payload, documents, chunks_by_doc, by_doc_id)
    return model.__class__.model_validate(updated)


def _enrich_citation_payload(
    value: Any,
    documents: list[Any],
    chunks_by_doc: dict[str, list[Any]],
    by_doc_id: dict[str, Any],
) -> Any:
    if isinstance(value, dict):
        enriched: dict[str, Any] = {}
        for key, item in value.items():
            if key == "citations" and isinstance(item, list):
                enriched[key] = [
                    _enrich_single_citation(citation, documents, chunks_by_doc, by_doc_id)
                    if isinstance(citation, dict) else citation
                    for citation in item
                ]
            else:
                enriched[key] = _enrich_citation_payload(item, documents, chunks_by_doc, by_doc_id)
        return enriched
    if isinstance(value, list):
        return [_enrich_citation_payload(item, documents, chunks_by_doc, by_doc_id) for item in value]
    return value


def _infer_doc_type_from_title(title: str | None) -> str | None:
    if not title:
        return None
    upper = title.upper()
    for form in ("10-K", "10-Q", "8-K"):
        if form in upper or f"({form})" in upper:
            return form
    return None


def _enrich_single_citation(
    citation: dict[str, Any],
    documents: list[Any],
    chunks_by_doc: dict[str, list[Any]],
    by_doc_id: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(citation)
    if not updated.get("doc_type"):
        inferred = _infer_doc_type_from_title(updated.get("title"))
        if inferred:
            updated["doc_type"] = inferred

    doc_id = updated.get("doc_id")
    chunk_id = updated.get("chunk_id")
    excerpt = (updated.get("excerpt") or "").strip()

    doc = by_doc_id.get(doc_id) if doc_id else _resolve_document(updated, documents)
    if doc is None:
        doc = _resolve_document_by_unique_doc_type(updated, documents)
    if doc:
        updated.setdefault("doc_id", doc.doc_id)
        updated.setdefault("title", doc.title)
        updated.setdefault("doc_type", doc.doc_type)
        updated.setdefault("filing_date", doc.filing_date)
        updated.setdefault("source_name", doc.source_name)
        updated.setdefault("url", doc.source_url)
        if not chunk_id and excerpt:
            resolved_chunk_id = _resolve_chunk_id(excerpt, chunks_by_doc.get(doc.doc_id, []))
            if resolved_chunk_id:
                updated["chunk_id"] = resolved_chunk_id
    return updated


def _resolve_document_by_unique_doc_type(citation: dict[str, Any], documents: list[Any]) -> Any | None:
    """If exactly one corpus document matches doc_type, use it (common for 10-K)."""
    doc_type = _norm(citation.get("doc_type"))
    if not doc_type:
        return None
    typed = [d for d in documents if _norm(d.doc_type) == doc_type]
    if len(typed) != 1:
        return None
    return typed[0]


def _resolve_document(citation: dict[str, Any], documents: list[Any]) -> Any | None:
    title = _norm(citation.get("title"))
    source_name = _norm(citation.get("source_name"))
    doc_type = _norm(citation.get("doc_type"))
    filing_date = _norm(citation.get("filing_date"))

    def score(document: Any) -> int:
        s = 0
        doc_title_norm = _norm(document.title)
        doc_source_norm = _norm(document.source_name)

        if title and doc_title_norm == title:
            s += 6
        elif title and title in doc_title_norm:
            s += 3
        if source_name and doc_source_norm == source_name:
            s += 2
        if doc_type and _norm(document.doc_type) == doc_type:
            s += 2
        if filing_date and _norm(document.filing_date) == filing_date:
            s += 2

        # Cross-match: LLM often puts the document title in source_name
        # (e.g. source_name="Annual Report 2024" when corpus title is
        # "Annual Report 2024" and corpus source_name is "Company IR").
        if source_name and doc_title_norm == source_name:
            s += 5
        elif source_name and source_name in doc_title_norm:
            s += 3
        if title and doc_source_norm and title in doc_source_norm:
            s += 2

        # Bridge LLM source labels to connector keys (yfinance / web_search)
        sk = getattr(document, "source_key", "") or ""
        if source_name:
            sn = source_name.lower()
            if ("yahoo" in sn or "yfinance" in sn) and sk == "yfinance":
                s += 5
            if any(x in sn for x in ("duckduck", "web search", "search", "news")) and sk == "web_search":
                s += 4
        return s

    ranked = sorted(((score(document), document) for document in documents), key=lambda x: x[0], reverse=True)
    best_score, best_document = ranked[0] if ranked else (0, None)
    return best_document if best_score >= 3 else None


def _resolve_chunk_id(excerpt: str, chunks: list[Any]) -> str | None:
    needle = _norm(excerpt)[:120]
    if not needle:
        return None
    for chunk in chunks:
        if needle in _norm(chunk.text):
            return chunk.chunk_id
    return None


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).lower().split())


# ── Tier 2: Number / punctuation normalization for matching ──────────

_CURRENCY_RE = re.compile(r"[\$€£¥]")
_COMMA_IN_NUM_RE = re.compile(r"(?<=\d),(?=\d)")
_PERCENT_RE = re.compile(r"\s*%")


def _normalize_numeric(text: str) -> str:
    """Strip currency symbols, commas inside numbers, and whitespace before %.

    '$1,234.5M' → '1234.5m'  (after lowering by caller).
    This lets substring matching work when the model reformats numbers.
    """
    text = _CURRENCY_RE.sub("", text)
    text = _COMMA_IN_NUM_RE.sub("", text)
    text = _PERCENT_RE.sub("%", text)
    return text
