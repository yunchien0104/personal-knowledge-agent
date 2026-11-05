"""Safe, structured diagnostics for Deep Research model validation attempts."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Literal

from pydantic import Field, field_validator

from personal_brain.research.deep_contracts import DeepStrictModel


AttemptStage = Literal["initial", "repair", "fallback"]
ValidationStage = Literal[
    "provider_terminal",
    "provider_dto",
    "backend_contract",
    "quality_gate",
    "accepted",
    "fallback",
]


class DeepValidationDiagnosticFields(DeepStrictModel):
    """Privacy-bounded metadata shared by R2B and R2C attempts."""

    attemptNumber: int = Field(ge=1, le=3)
    attemptStage: AttemptStage
    requestId: str | None = Field(default=None, min_length=1, max_length=200)
    finishReason: str | None = Field(default=None, min_length=1, max_length=64)
    inputTokens: int = Field(default=0, ge=0)
    outputTokens: int = Field(default=0, ge=0)
    reasoningTokens: int = Field(default=0, ge=0)
    costUsd: Decimal = Field(default=Decimal("0"), ge=0, max_digits=8, decimal_places=4)
    latencyMs: int = Field(default=0, ge=0)
    validationStage: ValidationStage
    errorCode: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    fieldPath: str | None = Field(default=None, min_length=1, max_length=300)
    ruleId: str | None = Field(default=None, pattern=r"^[a-z0-9_.-]{1,128}$")
    diagnosticSummary: str = Field(min_length=1, max_length=500)

    @field_validator("requestId", "finishReason", "fieldPath", "diagnosticSummary")
    @classmethod
    def printable_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(character) < 32 or ord(character) > 126 for character in value):
            raise ValueError("diagnostic text must be printable ASCII")
        return value

    def repair_message(self) -> str:
        parts = [
            f"stage={self.validationStage}",
            f"code={self.errorCode or 'unknown'}",
        ]
        if self.fieldPath:
            parts.append(f"field={self.fieldPath}")
        if self.ruleId:
            parts.append(f"rule={self.ruleId}")
        parts.append(self.diagnosticSummary)
        return " | ".join(parts)[:1000]


class R2BValidationDiagnostic(DeepValidationDiagnosticFields):
    schemaVersion: Literal["r2b-validation-diagnostic-v1"] = (
        "r2b-validation-diagnostic-v1"
    )


class R2CValidationDiagnostic(DeepValidationDiagnosticFields):
    schemaVersion: Literal["r2c-validation-diagnostic-v1"] = (
        "r2c-validation-diagnostic-v1"
    )


R2BDiagnosticSink = Callable[[R2BValidationDiagnostic], None]
R2CDiagnosticSink = Callable[[R2CValidationDiagnostic], None]
