#!/usr/bin/env python3
"""Step 1 Test: Corpus Store — SQLite + Qdrant lifecycle, search, manifest, refresh."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, populate_store
from src.models import get_company
from src.services.corpus_store import CorpusStore


def main():
    soc = get_company("SOC US")
    docs = build_soc_docs(soc)

    with TemporaryDirectory() as tmp:
        section("Create & Populate")
        store, all_chunks = populate_store(tmp, soc, docs)
        check("corpus_exists", store.corpus_exists(soc))
        check(f"{len(docs)} documents stored", len(store.get_all_documents(soc)) == len(docs))
        check(f"{len(all_chunks)} chunks stored", len(store.get_all_chunks(soc)) == len(all_chunks))

        section("Manifest")
        manifest = store.load_manifest(soc)
        check("Manifest loaded", manifest is not None)
        check(f"ticker={manifest.ticker}", manifest.ticker == "SOC US")
        source_map = manifest.source_map()
        check("edgar tracked", "edgar" in source_map)
        show("Sources", json.dumps({k: v.model_dump() for k, v in source_map.items()}, indent=2, default=str))

        section("Snapshot")
        snap = store.load_corpus(soc)
        check("Snapshot docs match", len(snap.documents) == len(docs))
        check("Snapshot chunks match", len(snap.chunks) == len(all_chunks))

        section("Semantic Search")
        results = store.search_chunks(soc, "revenue production barrels", limit=5)
        check(f"Search returned {len(results)} results", len(results) > 0)
        show("Top hit", results[0].text[:150] if results else "NONE")
        check("Empty query → empty", len(store.search_chunks(soc, "")) == 0)

        section("Topic Search")
        by_topic = store.search_chunks_by_topic(soc, ["revenue cash flow", "risk permits"], per_topic=3)
        check(f"2 topics returned", len(by_topic) == 2)

        section("Document Lookup")
        doc = store.get_document(soc, docs[0].doc_id)
        check("get_document works", doc is not None and doc.title == docs[0].title)
        by_source = store.get_documents_for_source(soc, "edgar")
        check(f"edgar has {len(by_source)} docs", len(by_source) == 3)

        section("Delete Chunks")
        before = store.get_chunks_for_doc(soc, docs[0].doc_id)
        store.delete_chunks_for_doc(soc, docs[0].doc_id)
        after = store.get_chunks_for_doc(soc, docs[0].doc_id)
        check(f"Before={len(before)}, After={len(after)}", len(before) > 0 and len(after) == 0)

        section("Empty Corpus")
        empty = CorpusStore(root_dir=Path(tmp) / "empty", output_dir=Path(tmp) / "eout")
        check("Empty → corpus_exists=False", not empty.corpus_exists(soc))

    report()

if __name__ == "__main__":
    main()
