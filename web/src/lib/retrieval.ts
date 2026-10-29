import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";


const retrievalSchema = z.object({
  q: z.string().trim().min(2).max(200),
  limit: z.coerce.number().int().min(1).max(50).default(20),
  offset: z.coerce.number().int().min(0).max(1000).default(0),
}).strict();

export type RetrievalInput = z.infer<typeof retrievalSchema>;

export type KnowledgeCitation = {
  assetId: string;
  revisionId: string;
  evidenceId: string;
  locator: Record<string, unknown>;
  validityStatus: string;
};

export type KnowledgeResult = {
  resultType: "source_note" | "claim" | "evidence";
  contentOrigin: "generated_note" | "generated_claim" | "original_source";
  resultId: string;
  title: string;
  snippet: string;
  relevance: number;
  assetId: string;
  revisionId: string;
  noteId: string | null;
  claimId: string | null;
  citations: KnowledgeCitation[];
  createdAt: string;
};

type RetrievalRow = {
  result_type: KnowledgeResult["resultType"];
  content_origin: KnowledgeResult["contentOrigin"];
  result_id: string;
  title: string;
  snippet: string;
  relevance: number;
  asset_id: string;
  revision_id: string;
  note_id: string | null;
  claim_id: string | null;
  citations: Array<{
    assetId: string;
    revisionId: string;
    evidenceId: string;
    locator: Record<string, unknown>;
    validityStatus: string;
  }>;
  created_at: string;
  total_count: number;
};

export function parseRetrievalInput(input: Record<string, unknown>) {
  return retrievalSchema.safeParse(input);
}

export function citationPdfPage(citation: KnowledgeCitation): number | null {
  const page = citation.locator?.page_number;
  return Number.isInteger(page) && Number(page) > 0 ? Number(page) : null;
}

export function originLabel(origin: KnowledgeResult["contentOrigin"]) {
  const labels = {
    original_source: "原始來源",
    generated_note: "AI 產生筆記",
    generated_claim: "AI 產生 Claim",
  } as const;
  return labels[origin];
}

export type KnowledgeSourceKind = "PDF" | "IG" | "TH" | "NOTE";

function citationKinds(result: Pick<KnowledgeResult, "citations">) {
  return new Set(result.citations.flatMap((citation) => {
    const kind = citation.locator?.kind;
    return typeof kind === "string" ? [kind] : [];
  }));
}

export function knowledgeSourceKind(
  result: Pick<KnowledgeResult, "resultType" | "citations">,
): KnowledgeSourceKind {
  if (result.resultType === "source_note") return "NOTE";
  const kinds = citationKinds(result);
  if (kinds.has("threads_post")) return "TH";
  if ([...kinds].some((kind) => kind.startsWith("instagram_") || kind === "video_segment")) {
    return "IG";
  }
  return "PDF";
}

export function knowledgeResultTitle(
  result: Pick<KnowledgeResult, "resultType" | "title" | "citations">,
) {
  if (!/^PDF source page\b/.test(result.title)) return result.title;
  const sourceKind = knowledgeSourceKind(result);
  if (sourceKind === "TH") return "Threads 來源片段";
  if (sourceKind === "IG") {
    const kinds = citationKinds(result);
    return kinds.has("instagram_caption") || kinds.has("video_segment")
      ? "Instagram Reel 來源片段"
      : "Instagram 貼文來源片段";
  }
  return result.title;
}

export async function retrieveKnowledge(
  supabase: SupabaseClient,
  input: RetrievalInput,
): Promise<{ results: KnowledgeResult[]; total: number }> {
  const { data, error } = await supabase.rpc("search_knowledge", {
    p_query: input.q,
    p_limit: input.limit,
    p_offset: input.offset,
  });
  if (error) throw new Error("Knowledge retrieval failed.");
  const rows = (data ?? []) as RetrievalRow[];
  return {
    total: rows[0]?.total_count ?? 0,
    results: rows.map((row) => ({
      resultType: row.result_type,
      contentOrigin: row.content_origin,
      resultId: row.result_id,
      title: row.title,
      snippet: row.snippet,
      relevance: row.relevance,
      assetId: row.asset_id,
      revisionId: row.revision_id,
      noteId: row.note_id,
      claimId: row.claim_id,
      citations: row.citations ?? [],
      createdAt: row.created_at,
    })),
  };
}
