export type WorkflowJob = {
  id: string;
  asset_id: string | null;
  job_type: string;
  status: string;
  progress_percent: number;
  progress_stage: string;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at: string;
};

export type WorkflowProgress = {
  percent: number;
  stage: string;
  state: "active" | "completed" | "failed";
};

const INGESTION_TYPES = new Set([
  "ingest_pdf",
  "ingest_threads",
  "ingest_instagram_post",
  "ingest_instagram_reel",
]);

const STAGE_LABELS: Record<string, string> = {
  queued: "等待開始",
  starting: "啟動處理",
  acquiring_source: "取得來源資料",
  downloading_media: "下載媒體",
  preparing_media: "準備影音",
  transcribing_audio: "轉錄影片人聲",
  extracting_keyframes: "擷取關鍵畫面",
  analyzing_frames: "理解畫面內容",
  assembling_contract: "整理內容",
  loading_contract: "讀取匯入內容",
  validating_contract: "驗證內容",
  uploading_artifacts: "儲存處理結果",
  publishing_source: "發布知識來源",
  retry_wait: "等待重試",
};

function latestByType(jobs: WorkflowJob[]) {
  const result = new Map<string, WorkflowJob>();
  for (const job of jobs) {
    if (!result.has(job.job_type)) result.set(job.job_type, job);
  }
  return result;
}

function bounded(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function effectiveJobPercent(job: WorkflowJob) {
  if (job.status === "completed") return 100;
  if (job.status === "running") return Math.max(1, job.progress_percent);
  return job.progress_percent;
}

function failed(job: WorkflowJob | undefined, percent: number): WorkflowProgress | null {
  if (!job || !["failed", "cancelled"].includes(job.status)) return null;
  return { percent: bounded(percent), stage: "處理失敗", state: "failed" };
}

export function buildWorkflowProgress(
  asset: { source_type: string; status: string },
  jobs: WorkflowJob[],
): WorkflowProgress {
  const byType = latestByType(jobs);
  const ingestion = jobs.find((job) => INGESTION_TYPES.has(job.job_type));
  const ingestionPercent = ingestion ? effectiveJobPercent(ingestion) : 0;
  const ingestionFailure = failed(ingestion, ingestionPercent * 0.7);
  if (ingestionFailure) return ingestionFailure;

  if (asset.status === "failed" || asset.status === "unsupported") {
    return { percent: bounded(ingestionPercent * 0.7), stage: "處理失敗", state: "failed" };
  }
  if (ingestion && ingestion.status !== "completed") {
    return {
      percent: bounded(ingestionPercent * 0.7),
      stage: STAGE_LABELS[ingestion.progress_stage] ?? "處理來源資料",
      state: "active",
    };
  }
  if (asset.status !== "ready" && asset.status !== "completed") {
    return { percent: 0, stage: "等待開始", state: "active" };
  }

  const requiresKnowledgeWorkflow = [
    "threads",
    "instagram_post",
    "instagram_reel",
  ].includes(asset.source_type);
  if (!requiresKnowledgeWorkflow) {
    return { percent: 100, stage: "已完成", state: "completed" };
  }

  const sourceNote = byType.get("generate_source_note");
  const sourceNotePercent = sourceNote ? effectiveJobPercent(sourceNote) : 0;
  const sourceNoteFailure = failed(sourceNote, 70 + sourceNotePercent * 0.2);
  if (sourceNoteFailure) return sourceNoteFailure;
  if (!sourceNote || sourceNote.status !== "completed") {
    return {
      percent: bounded(70 + sourceNotePercent * 0.2),
      stage: sourceNote?.status === "running" ? "生成知識筆記" : "準備生成知識筆記",
      state: "active",
    };
  }

  const retrieval = byType.get("index_retrieval");
  const retrievalPercent = retrieval ? effectiveJobPercent(retrieval) : 0;
  const retrievalFailure = failed(retrieval, 90 + retrievalPercent * 0.1);
  if (retrievalFailure) return retrievalFailure;
  if (!retrieval || retrieval.status !== "completed") {
    return {
      percent: bounded(90 + retrievalPercent * 0.1),
      stage: retrieval?.status === "running" ? "建立搜尋索引" : "準備建立搜尋索引",
      state: "active",
    };
  }
  return { percent: 100, stage: "已完成", state: "completed" };
}
