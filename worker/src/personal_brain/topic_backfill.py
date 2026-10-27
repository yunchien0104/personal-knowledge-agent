from __future__ import annotations

import argparse
import os
from typing import Any

from personal_brain.llm_client import StructuredLLMClient
from personal_brain.supabase_gateway import SupabaseGateway


TOPIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topicIds"],
    "properties": {
        "topicIds": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
    },
}


def classify(client: StructuredLLMClient, candidate: dict[str, Any]) -> list[str]:
    topics = candidate.get("availableTopics")
    if not isinstance(topics, list) or not topics:
        return []
    output = client.generate(
        "Choose zero or more topicIds only from availableTopics. Never invent a topic or ID. "
        "Return an empty list when none is a clear fit.",
        {
            "title": candidate.get("title"),
            "body": candidate.get("body"),
            "availableTopics": topics,
        },
        TOPIC_SCHEMA,
    )
    raw_ids = output.get("topicIds")
    allowed = {row.get("id") for row in topics if isinstance(row, dict)}
    if not isinstance(raw_ids, list) or len(raw_ids) > 20:
        raise ValueError("invalid_topic_ids")
    topic_ids: list[str] = []
    for topic_id in raw_ids:
        if not isinstance(topic_id, str) or topic_id not in allowed or topic_id in topic_ids:
            raise ValueError("invalid_topic_ids")
        topic_ids.append(topic_id)
    return topic_ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify only unclassified notes without a manual topic override."
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")

    gateway = SupabaseGateway.from_environment()
    candidates = gateway.topic_backfill_candidates(args.limit)
    if args.dry_run:
        for candidate in candidates:
            print(f"{candidate.get('noteId')}: pending")
        return 0
    client = StructuredLLMClient(
        os.environ.get("LLM_API_BASE_URL", ""),
        os.environ.get("LLM_API_KEY", ""),
        os.environ.get("SOURCE_NOTE_MODEL", ""),
    )
    for candidate in candidates:
        note_id = candidate.get("noteId")
        user_id = candidate.get("userId")
        if not isinstance(note_id, str) or not isinstance(user_id, str):
            continue
        if not candidate.get("availableTopics"):
            candidate["availableTopics"] = gateway.topic_classification_options(user_id)
        try:
            topic_ids = classify(client, candidate)
            status = "classified" if topic_ids else "unclassified"
            error = None
        except Exception as exc:  # one failed classification must not stop the bounded batch
            topic_ids, status, error = [], "failed", type(exc).__name__
        print(f"{note_id}: {status} ({len(topic_ids)} topics)")
        gateway.apply_ai_note_topics(note_id, topic_ids, status, error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
