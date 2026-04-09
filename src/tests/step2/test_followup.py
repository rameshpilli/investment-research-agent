#!/usr/bin/env python3
"""Step 2 Test: Follow-up Q&A — grounded answers, refusals, citation quality."""

import asyncio
from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, populate_store
from src.models import get_company
from src.services.agent import ResearchAgent
from src.steps.dossier import answer_follow_up


def main():
    soc = get_company("SOC US")
    agent = ResearchAgent(use_llm=False)

    with TemporaryDirectory() as tmp:
        store, _ = populate_store(tmp, soc, build_soc_docs(soc))

        section("Grounded Answer — revenue")
        a1 = asyncio.run(answer_follow_up(soc, store, "What is the revenue?", agent))
        check("Not empty", len(a1) > 0)
        check("Not a refusal", "cannot be answered" not in a1.lower())
        check("Contains revenue evidence", "revenue" in a1.lower() or "$" in a1)
        show("Answer", a1)

        section("Grounded Answer — pipeline permits")
        a2 = asyncio.run(answer_follow_up(soc, store, "What is the pipeline permit status?", agent))
        check("Not empty", len(a2) > 0)
        check("References permits/pipeline", "permit" in a2.lower() or "pipeline" in a2.lower())
        show("Answer", a2)

        section("Grounded Answer — risk factors")
        a3 = asyncio.run(answer_follow_up(soc, store, "What are the main risk factors?", agent))
        check("Not empty", len(a3) > 0)
        check("References risk content", "risk" in a3.lower() or "environmental" in a3.lower())
        show("Answer", a3)

        section("Refusal — unanswerable")
        a4 = asyncio.run(answer_follow_up(soc, store, "What is the CEO's favorite color?", agent))
        check("Refuses unanswerable question", "cannot be answered" in a4.lower())
        show("Answer (unanswerable)", a4)

        section("Refusal — completely unrelated")
        a5 = asyncio.run(answer_follow_up(soc, store, "What did European regulators say about the merger remedy package?", agent))
        check("Refuses cleanly", "cannot be answered" in a5.lower())
        show("Answer (unrelated)", a5)

    report()

if __name__ == "__main__":
    main()
