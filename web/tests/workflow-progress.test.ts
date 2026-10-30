import assert from "node:assert/strict";
import test from "node:test";

import { buildWorkflowProgress, type WorkflowJob } from "../src/lib/workflow-progress.ts";

function job(overrides: Partial<WorkflowJob> = {}): WorkflowJob {
  return {
    id: crypto.randomUUID(),
    asset_id: crypto.randomUUID(),
    job_type: "ingest_instagram_reel",
    status: "running",
    progress_percent: 40,
    progress_stage: "transcribing_audio",
    last_error_code: null,
    last_error_message: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

test("maps real Reel ingestion progress into the first 70 percent", () => {
  assert.deepEqual(
    buildWorkflowProgress(
      { source_type: "instagram_reel", status: "processing" },
      [job()],
    ),
    { percent: 28, stage: "轉錄影片人聲", state: "active" },
  );
});

test("tracks source note and retrieval as the final workflow stages", () => {
  const ingestion = job({ status: "completed", progress_percent: 100, progress_stage: "completed" });
  const note = job({ job_type: "generate_source_note", status: "completed", progress_percent: 100 });
  const retrieval = job({ job_type: "index_retrieval", status: "running", progress_percent: 50 });
  assert.deepEqual(
    buildWorkflowProgress(
      { source_type: "instagram_reel", status: "ready" },
      [ingestion, note, retrieval],
    ),
    { percent: 95, stage: "建立搜尋索引", state: "active" },
  );
});

test("reports completion only after the social knowledge workflow completes", () => {
  const rows = [
    job({ status: "completed", progress_percent: 100 }),
    job({ job_type: "generate_source_note", status: "completed", progress_percent: 100 }),
    job({ job_type: "index_retrieval", status: "completed", progress_percent: 100 }),
  ];
  assert.deepEqual(
    buildWorkflowProgress({ source_type: "instagram_reel", status: "ready" }, rows),
    { percent: 100, stage: "已完成", state: "completed" },
  );
});

test("does not hide a failed downstream job behind an active progress bar", () => {
  const rows = [
    job({ status: "completed", progress_percent: 100 }),
    job({ job_type: "generate_source_note", status: "failed", progress_percent: 35 }),
  ];
  assert.deepEqual(
    buildWorkflowProgress({ source_type: "instagram_reel", status: "ready" }, rows),
    { percent: 77, stage: "處理失敗", state: "failed" },
  );
});
