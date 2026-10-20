from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list_handlers:
            print(json.dumps(sorted(build_registry())))
            return 0

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
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"worker error: {error}", file=sys.stderr)
        return 2


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
