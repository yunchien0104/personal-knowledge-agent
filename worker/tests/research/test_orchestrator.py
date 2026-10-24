
from personal_brain.research.orchestrator import ResearchOrchestrator
from personal_brain.research.contracts import VerificationResult


EVENTS = []


class FakeGenerator:
    def synthesize(self, question, plan, evidence):
        EVENTS.append("generate")

        return {
            "schemaVersion": "atomic-research-v1",
            "title": "A 公司營收研究",
            "summary": "A 公司預估營收成長率放緩。",
            "findings": [
                {
                    "findingId": "F001",
                    "topic": "營收成長",
                    "claims": [
                        {
                            "claimId": "C001",
                            "statement": "2025 年營收成長率為 20%。",
                            "claimType": "fact",
                            "citations": [
                                {
                                    "evidenceId": "E001",
                                    "quote": "2025 年營收成長率為 20%",
                                    "relationType": "supports",
                                }
                            ],
                        },
                        {
                            "claimId": "C002",
                            "statement": "2026 年預估營收成長率為 12%。",
                            "claimType": "fact",
                            "citations": [
                                {
                                    "evidenceId": "E002",
                                    "quote": "2026 年預估營收成長率為 12%",
                                    "relationType": "supports",
                                }
                            ],
                        },
                    ],
                }
            ],
        }


    def repair(
        self,
        question,
        plan,
        evidence,
        previous_draft,
        verification_feedback,
    ):
        EVENTS.append("repair")
        return previous_draft


class FakeJudge:
    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}
        self.checked = []

    def verify(self, claim, evidence):
        EVENTS.append(f"verify:{claim.claimId}")
        self.checked.append(claim.claimId)

        return VerificationResult(
            claimId=claim.claimId,
            verdict=self.verdicts.get(claim.claimId, "supported"),
            explanation="Test verdict",
        )


EVIDENCE = [
    {"id": "E001", "excerpt": "A 公司 2025 年營收成長率為 20%。"},
    {"id": "E002", "excerpt": "A 公司 2026 年預估營收成長率為 12%。"},
]

QUESTION = "A 公司營收成長是否放緩？"
PLAN = {"question": QUESTION}


def test_all_supported():
    EVENTS.clear()
    judge = FakeJudge()

    execution = ResearchOrchestrator(FakeGenerator(), judge).run(
        QUESTION, PLAN, EVIDENCE
    )

    
    assert execution.gate.can_publish is True

    # 確認通過 Gate 後，有產生正式報告
    assert execution.verified_report is not None

    # 確認每個已驗證的 Claim 都出現在報告
    for finding in execution.draft.findings:
        for claim in finding.claims:
            assert claim.statement in execution.verified_report

    # 確認未驗證的 Summary 沒有被直接發布
    assert execution.draft.summary not in execution.verified_report

    # 保留原本的流程測試
    assert judge.checked == ["C001", "C002"]
    assert EVENTS == ["generate", "verify:C001", "verify:C002"]

    print("PASS: Generator, Verifier, and Report Builder")



def test_insufficient_evidence():
    EVENTS.clear()
    judge = FakeJudge({"C002": "insufficient_evidence"})

    execution = ResearchOrchestrator(FakeGenerator(), judge).run(
        QUESTION, PLAN, EVIDENCE
    )

    assert execution.gate.can_publish is False
    assert execution.verified_report is None
    assert execution.gate.repair_claim_ids == ["C002"]
    assert execution.repair_attempts == 1
    assert judge.checked == ["C001", "C002", "C001", "C002"]
    assert EVENTS == [
    "generate",
    "verify:C001",
    "verify:C002",
    "repair",
    "verify:C001",
    "verify:C002",
]
    print("PASS: Insufficient evidence blocks publication")


def test_invalid_citation():
    class BadGenerator(FakeGenerator):
        def synthesize(self, question, plan, evidence):
            result = super().synthesize(question, plan, evidence)
            result["findings"][0]["claims"][0]["citations"][0]["evidenceId"] = "E999"
            return result

    EVENTS.clear()
    judge = FakeJudge()

    try:
        ResearchOrchestrator(BadGenerator(), judge).run(
            QUESTION, PLAN, EVIDENCE
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid citation was accepted")

    assert judge.checked == []
    print("PASS: Invalid citation rejected before LLM verification")



def test_repair_success():
    EVENTS.clear()

    class RepairGenerator(FakeGenerator):
        def repair(
            self,
            question,
            plan,
            evidence,
            previous_draft,
            verification_feedback,
        ):
            EVENTS.append("repair")

            # 模擬 LLM 修正 C002，但不修改 C001
            from copy import deepcopy

            repaired = deepcopy(previous_draft)
            repaired["findings"][0]["claims"][1]["statement"] = (
                "2026 年預估營收成長率為 12%。"
            )
            return repaired

    class RepairAwareJudge(FakeJudge):
        def verify(self, claim, evidence):
            EVENTS.append(f"verify:{claim.claimId}")
            self.checked.append(claim.claimId)

            if claim.claimId == "C002":
                verdict = (
                    "supported"
                    if "2026 年預估營收成長率為 12%" in claim.statement
                    else "insufficient_evidence"
                )
            else:
                verdict = "supported"

            return VerificationResult(
                claimId=claim.claimId,
                verdict=verdict,
                explanation="Fake verification",
            )

    # 初始 C002 先被判斷為證據不足；
    # 修復之後才被判斷為 supported
    class InitiallyWrongGenerator(RepairGenerator):
        def synthesize(self, question, plan, evidence):
            draft = super().synthesize(question, plan, evidence)
            draft["findings"][0]["claims"][1]["statement"] = (
                "2026 年成長率將超過 50%。"
            )
            return draft

    judge = RepairAwareJudge()

    execution = ResearchOrchestrator(
        InitiallyWrongGenerator(), judge
    ).run(QUESTION, PLAN, EVIDENCE)

    assert execution.repair_attempts == 1
    assert execution.gate.can_publish is True
    assert execution.verified_report is not None

    assert (
        "2026 年預估營收成長率為 12%"
        in execution.verified_report
    )
    assert "超過 50%" not in execution.verified_report

    assert judge.checked == [
        "C001", "C002", "C001", "C002"
    ]

    print("PASS: Repair succeeds and verified report is generated")


def test_contradicted_blocks_repair():
    EVENTS.clear()

    # C002 被證據反駁
    judge = FakeJudge({"C002": "contradicted"})

    execution = ResearchOrchestrator(
        FakeGenerator(), judge
    ).run(QUESTION, PLAN, EVIDENCE)

    # Gate 必須阻止發布
    assert execution.gate.can_publish is False
    assert execution.verified_report is None

    # 不得進入 Repair
    assert execution.repair_attempts == 0
    assert "repair" not in EVENTS

    # 只能驗證第一輪
    assert judge.checked == ["C001", "C002"]
    assert execution.gate.rejected_claim_ids == ["C002"]

    print("PASS: Contradicted claim blocks repair and publication")


def test_repair_api_failure():
    EVENTS.clear()

    class FailingRepairGenerator(FakeGenerator):
        def repair(
            self,
            question,
            plan,
            evidence,
            previous_draft,
            verification_feedback,
        ):
            EVENTS.append("repair")
            raise TimeoutError("Simulated LLM timeout")

    judge = FakeJudge({
        "C002": "insufficient_evidence"
    })

    try:
        ResearchOrchestrator(
            FailingRepairGenerator(), judge
        ).run(QUESTION, PLAN, EVIDENCE)
    except TimeoutError:
        pass
    else:
        raise AssertionError("Repair timeout was not propagated")

    assert EVENTS.count("repair") == 1
    assert judge.checked == ["C001", "C002"]

    print("PASS: Repair timeout stops the pipeline")


if __name__ == "__main__":
    test_all_supported()
    test_insufficient_evidence()
    test_invalid_citation()
    test_repair_success()
    test_contradicted_blocks_repair()
    test_repair_api_failure()