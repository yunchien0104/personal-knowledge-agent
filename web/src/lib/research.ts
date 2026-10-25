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
  note: { id: string; title: string; body: string; publicationState: string } | null;
  citations: ResearchCitation[];
};

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

  const [jobResult, noteResult, citationResult] = await Promise.all([
    run.job_id
      ? supabase.from("jobs").select("last_error_code").eq("id", run.job_id).maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    run.output_note_id
      ? supabase.from("notes").select("id,title,body,publication_state").eq("id", run.output_note_id).maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    supabase
      .from("research_citations")
      .select("id,finding_index,citation_index,relation_type,evidence_id,evidence_id_snapshot,asset_id,asset_id_snapshot,source_revision_id,source_revision_id_snapshot,locator_json,supporting_quote")
      .eq("research_run_id", run.id)
      .order("finding_index")
      .order("citation_index"),
  ]);
  if (jobResult.error || noteResult.error || citationResult.error) {
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
    note: noteResult.data ? {
      id: noteResult.data.id,
      title: noteResult.data.title,
      body: noteResult.data.body,
      publicationState: noteResult.data.publication_state,
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
