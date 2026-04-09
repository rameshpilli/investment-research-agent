# Project Guidance

This project uses the Claude Agent SDK as the AI runtime for a bounded investment-research workflow.

Core rules:

- Treat the ingested corpus as the primary source of truth for Phase 1, Phase 2, Phase 3, and follow-up answers.
- Use the in-process research tools to inspect the corpus. Do not rely on unstated outside knowledge in generated reports.
- Every material claim should have a citation. If the corpus does not support a claim, mark it as uncertain or explicitly missing.
- Phase 2 should be adversarial. Prefer identifying contradictions, missing evidence, and downside risks over management-style summaries.
- Follow-up answers must remain grounded in the stored materials for the selected company.

Output expectations:

- Phase 1: coverage, freshness, and missing-context analysis.
- Phase 2: verdict, rationale, core risks, contradictions, and information gaps.
- Phase 3: concise analyst brief built from the earlier phases and corpus evidence.
- Follow-up: direct answer if supported; otherwise state that the question cannot be answered from the current materials.