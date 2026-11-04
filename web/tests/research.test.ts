import assert from "node:assert/strict";
import test from "node:test";

import {
  isResearchTerminal,
  isResearchReady,
  researchPdfPage,
  researchRequestSchema,
  researchWorkflowProgress,
  safeResearchError,
  getResearchRun,
  startResearch,
  waitForResearchRun,
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
    requested_limits: { maxRounds: 3, maxQueries: 12, maxCostUsd: 1 },
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
  assert.equal(researchRequestSchema.safeParse({
    ...parsed,
    requested_limits: { maxCostUsd: 1.0001 },
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

function fakeResearchClient(
  mode: "legacy" | "deep",
  webValidity: { status: "valid" | "invalid" | "source_deleted" | "superseded"; eligible: boolean } = {
    status: "valid",
    eligible: true,
  },
) {
  const touched: string[] = [];
  const rows: Record<string, unknown> = {
    research_runs: {
      id: "98000000-0000-4000-8000-000000000001",
      question: "What changed?",
      research_mode: mode,
      plan_json: null,
      scope_asset_ids: null,
      retrieval_status: null,
      progress_json: {},
      status: mode === "deep" ? "partial_ready" : "draft_ready",
      job_id: null,
      output_note_id: null,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
    },
    research_citations: [],
    deep_research_results: mode === "deep" ? {
      result_status: "verified_partial",
      stop_reason: "max_rounds_reached",
      title: "Partial",
      body: "Verified partial body",
      findings_json: [{ text: "Finding" }],
      verification_json: { gate: "passed" },
      output_hash: "a".repeat(64),
      published_note_id: null,
      updated_at: "2026-11-01T00:00:00Z",
    } : null,
    deep_web_citations: mode === "deep" ? [{
      id: "98000000-0000-4000-8000-000000000002",
      finding_index: 0,
      citation_index: 0,
      relation_type: "supports",
      web_evidence_id: "98000000-0000-4000-8000-000000000003",
      web_evidence_id_snapshot: "98000000-0000-4000-8000-000000000003",
      canonical_url: "https://example.com/report",
      source_title: "Report",
      content_hash: "b".repeat(64),
      locator_json: { kind: "web_page", url: "https://example.com/report" },
      supporting_quote: "supported quote",
    }] : [],
    deep_web_evidence: mode === "deep" ? [{
      id: "98000000-0000-4000-8000-000000000003",
      validity_status: webValidity.status,
      citation_eligible: webValidity.eligible,
    }] : [],
  };

  function builder(table: string) {
    const result = () => ({ data: rows[table] ?? null, error: null });
    const chain = {
      select: () => chain,
      eq: () => chain,
      order: () => chain,
      in: () => chain,
      maybeSingle: async () => result(),
      then: (resolve: (value: unknown) => void) => resolve(result()),
    };
    return chain;
  }
  return {
    touched,
    client: {
      from(table: string) {
        touched.push(table);
        return builder(table);
      },
    },
  };
}

test("legacy research read path remains independent of Deep tables", async () => {
  const fake = fakeResearchClient("legacy");
  const run = await getResearchRun(fake.client as never, "98000000-0000-4000-8000-000000000001");
  assert.equal(run?.mode, "legacy");
  assert.equal(run?.deepResult, null);
  assert.equal(fake.touched.includes("deep_research_results"), false);
  assert.equal(fake.touched.includes("deep_web_citations"), false);
});

test("deep research read path returns verified partial and valid Web provenance", async () => {
  const fake = fakeResearchClient("deep");
  const run = await getResearchRun(fake.client as never, "98000000-0000-4000-8000-000000000001");
  assert.equal(run?.deepResult?.status, "verified_partial");
  assert.equal(run?.citations.length, 1);
  assert.equal(run?.citations[0]?.origin, "web");
  assert.equal(run?.citations[0]?.validityStatus, "valid");
  assert.equal(run?.citations[0]?.canonicalUrl, "https://example.com/report");
});

test("deep research read path does not present ineligible Web Evidence as valid", async () => {
  const fake = fakeResearchClient("deep", { status: "superseded", eligible: false });
  const run = await getResearchRun(fake.client as never, "98000000-0000-4000-8000-000000000001");
  assert.equal(run?.citations.length, 1);
  assert.equal(run?.citations[0]?.origin, "web");
  assert.equal(run?.citations[0]?.validityStatus, "invalid");
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
