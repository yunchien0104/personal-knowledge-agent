from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from personal_brain.handlers import HealthcheckHandler, JobHandler
from personal_brain.models import JobPayloadV1


def build_registry() -> dict[str, JobHandler]:
    return {"healthcheck": HealthcheckHandler()}


def load_payload(path: Path) -> JobPayloadV1:
    with path.open(encoding="utf-8") as payload_file:
        return JobPayloadV1.from_dict(json.load(payload_file))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal Brain worker baseline runner")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-handlers", action="store_true")
    action.add_argument("--validate-payload", type=Path)
    action.add_argument("--payload", type=Path)
    action.add_argument("--once", action="store_true")
    action.add_argument("--poll", action="store_true")
    return parser


def build_ingestion_worker():
    from personal_brain.ingestion import PdfIngestionWorker
    from personal_brain.supabase_gateway import SupabaseGateway

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "120"))
    return PdfIngestionWorker(
        SupabaseGateway.from_environment(), worker_id, lease_seconds=lease_seconds
    )


def build_threads_worker():
    from personal_brain.supabase_gateway import SupabaseGateway
    from personal_brain.threads_ingestion import ThreadsIngestionWorker

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "120"))
    return ThreadsIngestionWorker(
        SupabaseGateway.from_environment(), worker_id, lease_seconds=lease_seconds
    )


def build_source_note_worker():
    from personal_brain.source_notes import (
        LocalExtractiveStructuredNoteModel,
        OpenAICompatibleStructuredNoteModel,
        SourceNoteWorker,
    )
    from personal_brain.supabase_gateway import SupabaseGateway

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "120"))
    model_mode = os.environ.get("SOURCE_NOTE_MODEL_MODE", "openai-compatible").strip()
    if model_mode == "local-extractive":
        model = LocalExtractiveStructuredNoteModel()
    elif model_mode == "openai-compatible":
        model = OpenAICompatibleStructuredNoteModel.from_environment()
    else:
        raise ValueError(
            "SOURCE_NOTE_MODEL_MODE must be local-extractive or openai-compatible"
        )
    return SourceNoteWorker(
        SupabaseGateway.from_environment(),
        model,
        worker_id,
        lease_seconds=lease_seconds,
    )



def build_research_worker():
    from personal_brain.research_agent import (
        LocalExtractiveResearchModel,
        ResearchAgentWorker,
    )
    from personal_brain.supabase_gateway import SupabaseGateway
    from personal_brain.research.llm import OpenAICompatibleResearchModel
    from personal_brain.research.verifier import LLMClaimJudge
    from personal_brain.research.orchestrator import ResearchOrchestrator

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")

    lease_seconds = int(
        os.environ.get("JOB_LEASE_SECONDS", "120")
    )

    # R2: 真正的 Research Generator
    generator = OpenAICompatibleResearchModel()

    # R2: 逐條驗證 Atomic Claims
    judge = LLMClaimJudge(generator.client)

    # R2: 串接 Generator、Verifier、Gate、Repair
    orchestrator = ResearchOrchestrator(generator, judge)

    # 沿用原本的 Planning Model
    planning_model = LocalExtractiveResearchModel()

    return ResearchAgentWorker(
        SupabaseGateway.from_environment(),
        planning_model,
        worker_id,
        orchestrator=orchestrator,
        lease_seconds=lease_seconds,
    )



def research_suggestions_enabled() -> bool:
    return os.environ.get("RESEARCH_SUGGESTIONS_ENABLED", "false").strip().lower() == "true"


def build_opportunity_worker():
    if not research_suggestions_enabled():
        raise ValueError("RESEARCH_SUGGESTIONS_ENABLED must be true for opportunity workers")
    from personal_brain.opportunities import OpportunityWorker
    from personal_brain.supabase_gateway import SupabaseGateway

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "120"))
    return OpportunityWorker(
        SupabaseGateway.from_environment(), worker_id, lease_seconds=lease_seconds
    )


def build_deletion_worker():
    from personal_brain.deletion import AssetDeletionWorker
    from personal_brain.supabase_gateway import SupabaseGateway

    worker_id = os.environ.get("WORKER_ID", "").strip()
    if not worker_id:
        raise ValueError("WORKER_ID is required")
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "120"))
    return AssetDeletionWorker(
        SupabaseGateway.from_environment(), worker_id, lease_seconds=lease_seconds
    )


class WorkerDispatcher:
    def __init__(self, workers):
        self.workers = workers

    def process_one(self):
        result = None
        for worker in self.workers:
            result = worker.process_one()
            if result.status != "idle":
                return result
        return result


def build_queue_worker():
    capability = os.environ.get("WORKER_CAPABILITY", "pdf").strip()
    if capability == "pdf":
        return build_ingestion_worker()
    if capability == "threads":
        return build_threads_worker()
    if capability == "source_note":
        return build_source_note_worker()
    if capability == "research":
        return build_research_worker()
    if capability == "opportunity":
        return build_opportunity_worker()
    if capability == "deletion":
        return build_deletion_worker()
    if capability == "all":
        workers = [
            build_deletion_worker(),
            build_ingestion_worker(),
            build_source_note_worker(),
            build_research_worker(),
        ]
        if research_suggestions_enabled():
            workers.append(build_opportunity_worker())
        return WorkerDispatcher(workers)
    raise ValueError(
        "WORKER_CAPABILITY must be pdf, threads, source_note, research, opportunity, deletion, or all"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list_handlers:
            print(json.dumps(sorted([
                *build_registry(), "ingest_pdf", "ingest_threads", "generate_source_note", "execute_research",
                "rank_opportunities", "delete_asset"
            ])))
            return 0

        if args.once:
            print(json.dumps(asdict(build_queue_worker().process_one()), separators=(",", ":")))
            return 0
        if args.poll:
            interval = float(os.environ.get("JOB_POLL_INTERVAL_SECONDS", "2"))
            if interval <= 0 or interval > 60:
                raise ValueError("JOB_POLL_INTERVAL_SECONDS must be between 0 and 60")
            worker = build_queue_worker()
            while True:
                result = worker.process_one()
                print(json.dumps(asdict(result), separators=(",", ":")), flush=True)
                if result.status == "idle":
                    time.sleep(interval)

        payload_path = args.validate_payload or args.payload
        payload = load_payload(payload_path)
        if args.validate_payload:
            print(json.dumps(payload.to_dict(), separators=(",", ":")))
            return 0

        handler = build_registry().get(payload.job_type)
        if handler is None:
            raise ValueError(f"no handler registered for {payload.job_type}")
        result = {
            "status": "completed",
            "jobId": payload.job_id,
            "result": handler(payload),
        }
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"worker error: {error}", file=sys.stderr)
        return 2


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
