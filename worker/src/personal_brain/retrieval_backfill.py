from __future__ import annotations

import argparse

from personal_brain.retrieval import (
    EmbeddingSettings,
    OpenAICompatibleEmbeddingProvider,
    RetrievalIndexer,
)
from personal_brain.retrieval_gateway import RetrievalSupabaseGateway


def run(*, page_size: int = 10, max_revisions: int = 100) -> int:
    settings = EmbeddingSettings.from_environment()
    gateway = RetrievalSupabaseGateway.from_environment()
    indexer = RetrievalIndexer(gateway, OpenAICompatibleEmbeddingProvider(settings))
    indexed = 0
    cursor: str | None = None
    while indexed < max_revisions:
        contexts = gateway.retrieval_backfill_contexts(
            settings.model,
            settings.version,
            after_revision_id=cursor,
            limit=min(page_size, max_revisions - indexed),
        )
        if not contexts:
            break
        for context in contexts:
            revision_id = context.get("revisionId")
            if not isinstance(revision_id, str):
                raise RuntimeError("Retrieval backfill context has no revision identity")
            indexer.index_context(context)
            indexed += 1
            cursor = revision_id
    return indexed


def cli() -> None:
    parser = argparse.ArgumentParser(description="Incrementally backfill active retrieval chunks")
    parser.add_argument("--page-size", type=int, default=10, choices=range(1, 51))
    parser.add_argument("--max-revisions", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.max_revisions <= 10_000:
        parser.error("--max-revisions must be between 1 and 10000")
    count = run(page_size=args.page_size, max_revisions=args.max_revisions)
    print(f"Indexed {count} active revision(s).")


if __name__ == "__main__":
    cli()
