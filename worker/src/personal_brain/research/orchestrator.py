
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from personal_brain.research.contracts import (
    AtomicResearchDraft,
    VerificationResult,
)
from personal_brain.research.verifier import (
    ClaimJudge,
    verify_research_draft,
)
from personal_brain.research.gate import GateDecision, evaluate_gate
from personal_brain.research.repair import (
    evaluate_repair,
    validate_repaired_draft,
)
from personal_brain.research.report_builder import build_verified_report


@dataclass(frozen=True)
class ResearchExecution:
    draft: AtomicResearchDraft
    verification_results: list[VerificationResult]
    gate: GateDecision
    verified_report: str | None
    repair_attempts: int = 0


class ResearchOrchestrator:
    def __init__(self, generator: Any, judge: ClaimJudge):
        self.generator = generator
        self.judge = judge

    def _verify(
        self,
        draft: AtomicResearchDraft,
        evidence: list[Mapping[str, Any]],
    ) -> tuple[list[VerificationResult], GateDecision]:

        # Batch Verifier 內部會先檢查 Citation
        results = verify_research_draft(
            draft, evidence, self.judge
        )

        claim_ids = [
            claim.claimId
            for finding in draft.findings
            for claim in finding.claims
        ]

        decision = evaluate_gate(results, claim_ids)
        return results, decision

    def run(
        self,
        question: str,
        plan: Mapping[str, Any],
        evidence: list[Mapping[str, Any]],
    ) -> ResearchExecution:

        # 1. 初次生成
        output = self.generator.synthesize(
            question, plan, evidence
        )
        draft = AtomicResearchDraft.model_validate(output)

        # 2. 初次驗證
        results, gate = self._verify(draft, evidence)
        attempts = 0

        # 3. Gate 不通過，才考慮修復
        if not gate.can_publish:
            repair_decision = evaluate_repair(
                results,
                attempts_used=attempts,
                max_attempts=1,
            )

            if repair_decision.should_repair:

                feedback = [
                    result.model_dump()
                    for result in results
                ]

                # 4. 只允許修復一次
                repaired_output = self.generator.repair(
                    question=question,
                    plan=plan,
                    evidence=evidence,
                    previous_draft=draft.model_dump(),
                    verification_feedback=feedback,
                )
                attempts += 1

                repaired = AtomicResearchDraft.model_validate(
                    repaired_output
                )

                # 5. 檢查 LLM 是否違反修復規則
                validate_repaired_draft(
                    original=draft,
                    repaired=repaired,
                    results=results,
                )

                # 6. 修復後必須重新驗證
                draft = repaired
                results, gate = self._verify(draft, evidence)

        # 7. 只有通過 Gate 才產生報告
        verified_report = None

        if gate.can_publish:
            verified_report = build_verified_report(
                draft, gate
            )

        return ResearchExecution(
            draft=draft,
            verification_results=results,
            gate=gate,
            verified_report=verified_report,
            repair_attempts=attempts,
        )
