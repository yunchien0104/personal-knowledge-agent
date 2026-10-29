import assert from "node:assert/strict";
import test from "node:test";

import {
  isResearchTerminal,
  researchPdfPage,
  researchRequestSchema,
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

test("knowledge answers stop polling only at a terminal research status", async () => {
  assert.equal(isResearchTerminal("queued"), false);
  assert.equal(isResearchTerminal("running"), false);
  assert.equal(isResearchTerminal("completed"), true);
  assert.equal(isResearchTerminal("failed"), true);

  const statuses = ["queued", "running", "completed"];
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
    note: null,
    citations: [],
  }), { intervalMs: 0, maxAttempts: 4 });
  assert.equal(result.status, "completed");
  assert.equal(calls, 3);
});
