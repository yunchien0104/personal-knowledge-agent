from typing import Any

from personal_brain.models import JobPayloadV1


class HealthcheckHandler:
    """Dependency-free handler used only to prove the runner contract."""

    def __call__(self, payload: JobPayloadV1) -> dict[str, Any]:
        return {
            "message": payload.input.get("message", "ok"),
            "workerReady": True,
        }
