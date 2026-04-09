"""Research pipeline steps."""

from src.steps.briefing import build_phase3_brief
from src.steps.dossier import answer_follow_up, build_phase2_dossier
from src.steps.gap_analysis import build_phase1_report
from src.steps.ingest import (
    ensure_corpus_ready,
    fetch_corpus,
    ingest_corpus,
)

__all__ = [
    "answer_follow_up",
    "build_phase1_report",
    "build_phase2_dossier",
    "build_phase3_brief",
    "ensure_corpus_ready",
    "fetch_corpus",
    "ingest_corpus",
]
