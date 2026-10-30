import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";


export const researchRequestSchema = z.object({
  question: z.string().trim().min(3).max(500),
  request_key: z.uuid(),
  scope_asset_ids: z.array(z.uuid()).min(1).max(100).refine(
    (values) => new Set(values).size === values.length,
    "Asset scope must not contain duplicates.",
  ).optional(),
}).strict();

export function newResearchRequestKey() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16);
    return (token === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

export type ResearchCitation = {
  id: string;
  findingIndex: number;
  citationIndex: number;
  relationType: "supports" | "contradicts" | "contextualizes";
  evidenceId: string;
  assetId: string;
  revisionId: string;
  locator: Record<string, unknown>;
  quote: string;
  validityStatus: "valid" | "source_deleted";
  sourceUrl?: string;
};

export type ResearchRun = {
  id: string;
  question: string;
  plan: Record<string, unknown> | null;
  scopeAssetIds: string[] | null;
  retrievalStatus: "ready" | "degraded_embedding_unavailable" | "insufficient" | null;
  progress: Record<string, unknown>;
  status: string;
  jobId: string | null;
  outputNoteId: string | null;
  createdAt: string;
  updatedAt: string;
  errorCode: string | null;
  jobProgress: { percent: number; stage: string } | null;
  note: {
    id: string;
    title: string;
    body: string;
    publicationState: string;
    topics: Array<{ id: string; name: string }>;
  } | null;
  citations: ResearchCitation[];
};

export type ResearchWorkflowProgress = {
  percent: number;
  label: string;
  state: "active" | "completed" | "failed";
};

const researchStageLabels: Record<string, string> = {
  queued: "等待開始",
  starting: "準備研究資料",
  planning_research: "規劃研究方向",
  retrieving: "搜尋知識庫來源",
  searching_sources: "搜尋知識庫來源",
  generating: "整理研究證據",
  drafting_findings: "撰寫研究內容",
  verifying_claims: "核對主張與引用",
  publishing_research: "建立研究筆記",
  retry_wait: "等待重新處理",
};

export function researchWorkflowProgress(run: Pick<ResearchRun, "status" | "progress" | "jobProgress">): ResearchWorkflowProgress {
  if (isResearchReady(run.status)) return { percent: 100, label: "研究完成", state: "completed" };
  if (["failed", "unsupported", "cancelled"].includes(run.status)) {
    return {
      percent: Math.max(0, Math.min(99, run.jobProgress?.percent ?? 0)),
      label: run.status === "cancelled" ? "研究已取消" : "研究未完成",
      state: "failed",
    };
  }
  const legacyStage = typeof run.progress.stage === "string" ? run.progress.stage : null;
  const stage = run.jobProgress?.stage ?? legacyStage ?? run.status;
  const fallbackPercent = stage === "generating" ? 60 : stage === "retrieving" ? 30 : run.status === "running" ? 5 : 2;
  return {
    percent: Math.max(1, Math.min(99, run.jobProgress?.percent ?? fallbackPercent)),
    label: researchStageLabels[stage] ?? "處理研究資料",
    state: "active",
  };
}

export async function startResearch(
  supabase: SupabaseClient,
  input: z.infer<typeof researchRequestSchema>,
) {
  const { data, error } = await supabase.rpc("start_research", {
    p_question: input.question,
    p_request_key: input.request_key,
    p_scope_asset_ids: input.scope_asset_ids ?? null,
  });
  if (error || !data || typeof data !== "object") {
    throw new Error("Research could not be started.");
  }
  return data as { id: string; jobId: string; status: string };
}

export async function listResearchRuns(supabase: SupabaseClient, limit = 20) {
  const { data, error } = await supabase
    .from("research_runs")
    .select("id,question,status,job_id,output_note_id,created_at,updated_at")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw new Error("Research runs could not be loaded.");
  return data ?? [];
}

export async function getResearchRun(
  supabase: SupabaseClient,
  runId: string,
): Promise<ResearchRun | null> {
  const { data: run, error } = await supabase
    .from("research_runs")
    .select("id,question,plan_json,scope_asset_ids,retrieval_status,progress_json,status,job_id,output_note_id,created_at,updated_at")
    .eq("id", runId)
    .maybeSingle();
  if (error) throw new Error("Research run could not be loaded.");
  if (!run) return null;

  const [jobResult, noteResult, topicResult, citationResult] = await Promise.all([
    run.job_id
      ? supabase.from("jobs").select("last_error_code,progress_percent,progress_stage").eq("id", run.job_id).maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    run.output_note_id
      ? supabase.from("notes").select("id,title,body,publication_state").eq("id", run.output_note_id).maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    run.output_note_id
      ? supabase.from("note_topics").select("topic_id,topics!inner(id,name)").eq("note_id", run.output_note_id)
      : Promise.resolve({ data: [], error: null }),
    supabase
      .from("research_citations")
      .select("id,finding_index,citation_index,relation_type,evidence_id,evidence_id_snapshot,asset_id,asset_id_snapshot,source_revision_id,source_revision_id_snapshot,locator_json,supporting_quote")
      .eq("research_run_id", run.id)
      .order("finding_index")
      .order("citation_index"),
  ]);
  if (jobResult.error || noteResult.error || topicResult.error || citationResult.error) {
    throw new Error("Research draft could not be loaded.");
  }
  return {
    id: run.id,
    question: run.question,
    plan: run.plan_json,
    scopeAssetIds: run.scope_asset_ids,
    retrievalStatus: run.retrieval_status,
    progress: run.progress_json ?? {},
    status: run.status,
    jobId: run.job_id,
    outputNoteId: run.output_note_id,
    createdAt: run.created_at,
    updatedAt: run.updated_at,
    errorCode: jobResult.data?.last_error_code ?? null,
    jobProgress: jobResult.data ? {
      percent: jobResult.data.progress_percent,
      stage: jobResult.data.progress_stage,
    } : null,
    note: noteResult.data ? {
      id: noteResult.data.id,
      title: noteResult.data.title,
      body: noteResult.data.body,
      publicationState: noteResult.data.publication_state,
      topics: (topicResult.data ?? []).flatMap((row) => {
        const topic = Array.isArray(row.topics) ? row.topics[0] : row.topics;
        return topic && typeof topic.id === "string" && typeof topic.name === "string"
          ? [{ id: topic.id, name: topic.name }]
          : [];
      }),
    } : null,
    citations: (citationResult.data ?? []).map((row) => ({
      id: row.id,
      findingIndex: row.finding_index,
      citationIndex: row.citation_index,
      relationType: row.relation_type,
      evidenceId: row.evidence_id_snapshot,
      assetId: row.asset_id_snapshot,
      revisionId: row.source_revision_id_snapshot,
      locator: row.locator_json,
      quote: row.supporting_quote,
      validityStatus:
        row.evidence_id && row.asset_id && row.source_revision_id ? "valid" : "source_deleted",
    })),
  };
}

export function researchPdfPage(locator: unknown): number | null {
  if (!locator || typeof locator !== "object") return null;
  const page = (locator as Record<string, unknown>).page_number;
  return typeof page === "number" && Number.isInteger(page) && page > 0 ? page : null;
}

export function safeResearchError(code: string | null) {
  const messages: Record<string, string> = {
    insufficient_evidence: "內部知識庫找不到足夠的原始 Evidence；Research Draft 未建立。",
    unsupported_research_finding: "研究結果無法由逐字原始引用支持；Research Draft 未建立。",
    invalid_research_plan: "研究計畫無效，未執行研究。",
  };
  return code ? messages[code] ?? "Research Agent 執行失敗，請稍後重試。" : null;
}

const terminalResearchStatuses = new Set([
  "draft_ready", "completed", "failed", "unsupported", "cancelled",
]);

export function isResearchReady(status: string) {
  return status === "draft_ready" || status === "completed";
}

export function isResearchTerminal(status: string) {
  return terminalResearchStatuses.has(status);
}

export async function waitForResearchRun<T extends { status: string }>(
  runId: string,
  load: (id: string) => Promise<T>,
  options: { intervalMs?: number; maxAttempts?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? 2_000;
  const maxAttempts = options.maxAttempts ?? 60;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const run = await load(runId);
    if (isResearchTerminal(run.status)) return run;
    if (attempt < maxAttempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
  }
  throw new Error("Research answer timed out.");
}
