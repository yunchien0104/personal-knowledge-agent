import assert from "node:assert/strict";
import test from "node:test";

import {
  researchPdfPage,
  researchRequestSchema,
  safeResearchError,
  startResearch,
} from "../src/lib/research.ts";


test("research request requires a bounded question and UUID idempotency key", () => {
  const valid = researchRequestSchema.safeParse({
    question: "  What changed in revenue?  ",
    request_key: "98000000-0000-4000-8000-000000000008",
  });
  assert.equal(valid.success, true);
  if (valid.success) {
    assert.equal(valid.data.question, "What changed in revenue?");
    assert.equal(valid.data.mode, "legacy");
  }
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

test("deep request is bounded and routes to a separate RPC", async () => {
  const parsed = researchRequestSchema.parse({
    question: "Compare the evidence and identify unresolved conflicts",
    request_key: "98000000-0000-4000-8000-000000000008",
    mode: "deep",
    web_policy: "disabled",
    requested_limits: { maxRounds: 3, maxQueries: 12, maxCostUsd: 0.5 },
  });
  let rpcName = "";
  let rpcArguments: Record<string, unknown> = {};
  const supabase = {
    rpc: async (name: string, args: Record<string, unknown>) => {
      rpcName = name;
      rpcArguments = args;
      return { data: { id: "run", jobId: "job", status: "queued" }, error: null };
    },
  };
  await startResearch(supabase as never, parsed);
  assert.equal(rpcName, "start_deep_research");
  assert.equal(rpcArguments.p_web_policy, "disabled");
  assert.equal("user_id" in rpcArguments, false);
  assert.equal(researchRequestSchema.safeParse({
    ...parsed,
    requested_limits: { maxRounds: 11 },
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
