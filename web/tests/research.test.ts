import assert from "node:assert/strict";
import test from "node:test";

import {
  isResearchTerminal,
  isResearchReady,
  researchPdfPage,
  researchRequestSchema,
  researchWorkflowProgress,
  safeResearchError,
  waitForResearchRun,
} from "../src/lib/research.ts";


test("research request requires a bounded question and UUID idempotency key", () => {
  const valid = researchRequestSchema.safeParse({
    question: "  What changed in revenue?  ",
    request_key: "98000000-0000-4000-8000-000000000008",
  });
  assert.equal(valid.success, true);
  if (valid.success) assert.equal(valid.data.question, "What changed in revenue?");
  assert.equal(researchRequestSchema.safeParse({ question: "x", request_key: "bad" }).success, false);
  assert.equal(researchRequestSchema.safeParse({
    question: "Valid question", request_key: "98000000-0000-4000-8000-000000000008", user_id: "forged",
  }).success, false);
  assert.equal(researchRequestSchema.safeParse({
    question: "Valid question",
    request_key: "98000000-0000-4000-8000-000000000008",
    scope_asset_ids: ["94000000-0000-4000-8000-000000000004"],
  }).success, true);
  assert.equal(researchRequestSchema.safeParse({
    question: "Valid question",
    request_key: "98000000-0000-4000-8000-000000000008",
    scope_asset_ids: [
      "94000000-0000-4000-8000-000000000004",
      "94000000-0000-4000-8000-000000000004",
    ],
  }).success, false);
});

test("research citations expose only valid positive PDF pages", () => {
  assert.equal(researchPdfPage({ kind: "pdf_page", page_number: 4 }), 4);
  assert.equal(researchPdfPage({ kind: "pdf_page", page_number: 0 }), null);
  assert.equal(researchPdfPage({ kind: "video", start_seconds: 4 }), null);
});

test("research errors reveal only allowlisted safe messages", () => {
  assert.match(safeResearchError("insufficient_evidence") ?? "", /Evidence/);
  assert.equal(safeResearchError("provider-secret"), "Research Agent 執行失敗，請稍後重試。");
});

test("research workflow exposes a human-readable stage and bounded progress", () => {
  assert.deepEqual(researchWorkflowProgress({
    status: "running", progress: {}, jobProgress: { percent: 60, stage: "drafting_findings" },
  }), { percent: 60, label: "撰寫研究內容", state: "active" });
  assert.deepEqual(researchWorkflowProgress({
    status: "draft_ready", progress: {}, jobProgress: { percent: 94, stage: "publishing_research" },
  }), { percent: 100, label: "研究完成", state: "completed" });
  assert.deepEqual(researchWorkflowProgress({
    status: "failed", progress: {}, jobProgress: { percent: 82, stage: "verifying_claims" },
  }), { percent: 82, label: "研究未完成", state: "failed" });
});

test("knowledge answers stop polling only at a terminal research status", async () => {
  assert.equal(isResearchTerminal("queued"), false);
  assert.equal(isResearchTerminal("running"), false);
  assert.equal(isResearchTerminal("draft_ready"), true);
  assert.equal(isResearchTerminal("completed"), true);
  assert.equal(isResearchTerminal("failed"), true);
  assert.equal(isResearchReady("draft_ready"), true);
  assert.equal(isResearchReady("running"), false);

  const statuses = ["queued", "running", "draft_ready"];
  let calls = 0;
  const result = await waitForResearchRun("run-id", async () => ({
    id: "run-id",
    question: "What do my sources say?",
    plan: null,
    scopeAssetIds: null,
    retrievalStatus: "ready",
    progress: {},
    status: statuses[calls++] ?? "completed",
    jobId: null,
    outputNoteId: null,
    createdAt: "2026-10-30T00:00:00Z",
    updatedAt: "2026-10-30T00:00:00Z",
    errorCode: null,
    jobProgress: null,
    note: null,
    citations: [],
  }), { intervalMs: 0, maxAttempts: 4 });
  assert.equal(result.status, "draft_ready");
  assert.equal(calls, 3);
});
