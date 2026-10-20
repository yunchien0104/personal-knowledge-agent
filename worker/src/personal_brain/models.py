from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import UUID

JOB_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ALLOWED_KEYS = {
    "schemaVersion",
    "jobId",
    "userId",
    "jobType",
    "idempotencyKey",
    "input",
}


def _require_string(value: dict[str, Any], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str):
        raise ValueError(f"{key} must be a string")
    return candidate


def _require_uuid(value: str, key: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{key} must be a UUID") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{key} must use canonical UUID form")
    return value


@dataclass(frozen=True, slots=True)
class JobPayloadV1:
    job_id: str
    user_id: str
    job_type: str
    idempotency_key: str
    input: dict[str, Any]
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: Any) -> JobPayloadV1:
        if not isinstance(value, dict):
            raise ValueError("job payload must be an object")
        unknown_keys = set(value) - ALLOWED_KEYS
        if unknown_keys:
            fields = ", ".join(sorted(unknown_keys))
            raise ValueError(f"unexpected job payload fields: {fields}")
        if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
            raise ValueError("schemaVersion must be 1")

        job_id = _require_uuid(_require_string(value, "jobId"), "jobId")
        user_id = _require_uuid(_require_string(value, "userId"), "userId")
        job_type = _require_string(value, "jobType")
        idempotency_key = _require_string(value, "idempotencyKey")
        input_value = value.get("input")

        if not JOB_TYPE_PATTERN.fullmatch(job_type):
            raise ValueError("jobType has an invalid format")
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotencyKey length must be between 1 and 128")
        if not isinstance(input_value, dict):
            raise ValueError("input must be an object")

        return cls(
            job_id=job_id,
            user_id=user_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
            input=input_value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "userId": self.user_id,
            "jobType": self.job_type,
            "idempotencyKey": self.idempotency_key,
            "input": self.input,
        }
