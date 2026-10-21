import type { SupabaseClient } from "@supabase/supabase-js";

import { PDF_BUCKET, pdfObjectPath } from "./pdf.ts";

export type SourceNoteCitation = {
  evidenceId: string;
  page: number | null;
  quote: string;
  validityStatus: string;
};

export type SourceNoteClaim = {
  id: string;
  text: string;
  claimType: string;
  verificationStatus: string;
  needsReview: boolean;
  citations: SourceNoteCitation[];
};

export type SourceNoteWorkspace = {
  asset: { id: string; status: string; activeRevisionId: string | null };
  job: { id: string; status: string; errorCode: string | null } | null;
  note: {
    id: string;
    title: string;
    body: string;
    bodyRevision: number;
    updatedAt: string;
    modelProvider: string;
    modelName: string;
    promptVersion: string;
    revisionId: string;
    pdfUrl: string | null;
    claims: SourceNoteClaim[];
  } | null;
};

export function pdfPageFromLocator(locator: unknown): number | null {
  if (!locator || typeof locator !== "object") return null;
  const page = (locator as Record<string, unknown>).page_number;
  return Number.isInteger(page) && Number(page) > 0 ? Number(page) : null;
}

export function safeSourceNoteError(code: string | null) {
  const messages: Record<string, string> = {
    invalid_model_output: "模型輸出格式不正確，未建立筆記。",
    unsupported_claim: "模型引用無法由原文支持，未建立筆記。",
    source_mismatch: "來源版本已變更，請重新產生筆記。",
    source_too_large: "文件超出目前的理解處理上限。",
  };
  return code ? messages[code] ?? "Source Note 產生失敗，請稍後再試。" : null;
}

export async function loadSourceNoteWorkspace(
  supabase: SupabaseClient,
  userId: string,
  assetId: string,
): Promise<SourceNoteWorkspace | null> {
  const { data: asset, error: assetError } = await supabase
    .from("assets")
    .select("id,status,active_revision_id")
    .eq("id", assetId)
    .eq("source_type", "pdf")
    .maybeSingle();
  if (assetError) throw new Error("Source Note Asset could not be loaded.");
  if (!asset) return null;

  const { data: jobs, error: jobError } = await supabase
    .from("jobs")
    .select("id,status,last_error_code,created_at")
    .eq("asset_id", assetId)
    .eq("job_type", "generate_source_note")
    .order("created_at", { ascending: false })
    .limit(1);
  if (jobError) throw new Error("Source Note Job could not be loaded.");
  const job = jobs?.[0] ?? null;

  if (!asset.active_revision_id) {
    return {
      asset: { id: asset.id, status: asset.status, activeRevisionId: null },
      job: job ? { id: job.id, status: job.status, errorCode: job.last_error_code } : null,
      note: null,
    };
  }

  const { data: generations, error: generationError } = await supabase
    .from("source_note_generations")
    .select("note_id,source_revision_id_snapshot,model_provider,model_name,prompt_version,created_at")
    .eq("asset_id_snapshot", assetId)
    .eq("source_revision_id_snapshot", asset.active_revision_id)
    .order("created_at", { ascending: false })
    .limit(1);
  if (generationError) throw new Error("Source Note provenance could not be loaded.");
  const generation = generations?.[0] ?? null;
  if (!generation) {
    return {
      asset: { id: asset.id, status: asset.status, activeRevisionId: asset.active_revision_id },
      job: job ? { id: job.id, status: job.status, errorCode: job.last_error_code } : null,
      note: null,
    };
  }

  const { data: note, error: noteError } = await supabase
    .from("notes")
    .select("id,title,body,body_revision,updated_at")
    .eq("id", generation.note_id)
    .eq("note_type", "source_note")
    .maybeSingle();
  if (noteError || !note) throw new Error("Source Note could not be loaded.");
  const { data: claimRows, error: claimsError } = await supabase
    .from("claims")
    .select("id,text,claim_type,verification_status,needs_review,created_at")
    .eq("note_id", note.id)
    .order("created_at", { ascending: true });
  if (claimsError) throw new Error("Source Note Claims could not be loaded.");
  const claimIds = (claimRows ?? []).map((claim) => claim.id);
  const { data: citationRows, error: citationsError } = claimIds.length
    ? await supabase
      .from("claim_evidence")
      .select("claim_id,evidence_id,evidence_id_snapshot,locator_json,supporting_quote,validity_status")
      .in("claim_id", claimIds)
      .order("created_at", { ascending: true })
    : { data: [], error: null };
  if (citationsError) throw new Error("Source Note citations could not be loaded.");

  const citationsByClaim = new Map<string, SourceNoteCitation[]>();
  for (const citation of citationRows ?? []) {
    const values = citationsByClaim.get(citation.claim_id) ?? [];
    values.push({
      evidenceId: citation.evidence_id_snapshot,
      page: pdfPageFromLocator(citation.locator_json),
      quote: citation.supporting_quote,
      validityStatus: citation.evidence_id ? citation.validity_status : "source_deleted",
    });
    citationsByClaim.set(citation.claim_id, values);
  }
  const signed = await supabase.storage
    .from(PDF_BUCKET)
    .createSignedUrl(pdfObjectPath(userId, assetId), 300);

  return {
    asset: { id: asset.id, status: asset.status, activeRevisionId: asset.active_revision_id },
    job: job ? { id: job.id, status: job.status, errorCode: job.last_error_code } : null,
    note: {
      id: note.id,
      title: note.title,
      body: note.body,
      bodyRevision: note.body_revision,
      updatedAt: note.updated_at,
      modelProvider: generation.model_provider,
      modelName: generation.model_name,
      promptVersion: generation.prompt_version,
      revisionId: generation.source_revision_id_snapshot,
      pdfUrl: signed.error ? null : signed.data.signedUrl,
      claims: (claimRows ?? []).map((claim) => ({
        id: claim.id,
        text: claim.text,
        claimType: claim.claim_type,
        verificationStatus: claim.verification_status,
        needsReview: claim.needs_review,
        citations: citationsByClaim.get(claim.id) ?? [],
      })),
    },
  };
}
