from typing import Any, Protocol

from personal_brain.models import JobPayloadV1


class JobHandler(Protocol):
    """Interface implemented by each versioned job handler."""

    def __call__(self, payload: JobPayloadV1) -> dict[str, Any]: ...
