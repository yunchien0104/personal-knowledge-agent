import type { SupabaseClient } from "@supabase/supabase-js";

import { parsePdfMetadata, PDF_BUCKET, pdfObjectPath } from "@/lib/pdf";
import { deriveIngestionStatus, safeIngestionError } from "@/lib/pdf-inbox-status";

const INBOX_LIMIT = 50;

export type PdfInboxItem = {
  id: string;
  fileName: string;
  fileSize: number | null;
  ingestionStatus: "queued" | "processing" | "completed" | "failed";
  errorMessage: string | null;
  createdAt: string;
  downloadUrl: string | null;
  stored: boolean;
  sourceStatus: string;
  deletion: {
    id: string;
    asset_id: string | null;
    asset_id_snapshot: string;
    mode: "remove_original" | "purge_all";
    status: "pending" | "deleting" | "completed" | "failed";
    error_code: string | null;
    error_message: string | null;
    requested_at: string;
    started_at: string | null;
    completed_at: string | null;
    updated_at: string;
  } | null;
};

export async function loadPdfInbox(supabase: SupabaseClient, userId: string) {
  const { data, error } = await supabase
    .from("assets")
    .select("id,source_external_id,status,created_at")
    .eq("source_type", "pdf")
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(INBOX_LIMIT);
  if (error) throw new Error("PDF inbox could not be loaded.");

  const assetIds = (data ?? []).map((asset) => asset.id);
  const jobsByAsset = new Map<string, { status: string; last_error_code: string | null }>();
  const deletionsByAsset = new Map<string, PdfInboxItem["deletion"]>();
  if (assetIds.length) {
    const [{ data: jobs, error: jobsError }, { data: deletions, error: deletionsError }] = await Promise.all([
      supabase.from("jobs").select("asset_id,status,last_error_code,created_at").in("asset_id", assetIds).order("created_at", { ascending: false }),
      supabase.from("asset_deletions").select("id,asset_id,asset_id_snapshot,mode,status,error_code,error_message,requested_at,started_at,completed_at,updated_at").in("asset_id_snapshot", assetIds).order("updated_at", { ascending: false }),
    ]);
    if (jobsError) throw new Error("PDF ingestion status could not be loaded.");
    if (deletionsError) throw new Error("PDF deletion status could not be loaded.");
    for (const job of jobs ?? []) {
      if (!jobsByAsset.has(job.asset_id)) jobsByAsset.set(job.asset_id, job);
    }
    for (const deletion of deletions ?? []) {
      if (!deletionsByAsset.has(deletion.asset_id_snapshot)) {
        deletionsByAsset.set(deletion.asset_id_snapshot, deletion as PdfInboxItem["deletion"]);
      }
    }
  }

  return Promise.all((data ?? []).map(async (asset): Promise<PdfInboxItem> => {
    const metadata = parsePdfMetadata(asset.source_external_id);
    const path = pdfObjectPath(userId, asset.id);
    const prefix = path.slice(0, path.lastIndexOf("/"));
    const sourceAvailable = !["deleting", "deletion_failed", "source_removed"].includes(asset.status);
    const listed = sourceAvailable
      ? await supabase.storage.from(PDF_BUCKET).list(prefix, { limit: 2, search: "source.pdf" })
      : { data: [], error: null };
    const stored = !listed.error && (listed.data ?? []).some((object) => object.name === "source.pdf");

    let downloadUrl: string | null = null;
    if (stored) {
      const signed = await supabase.storage
        .from(PDF_BUCKET)
        .createSignedUrl(path, 300, { download: metadata?.fileName ?? "source.pdf" });
      downloadUrl = signed.error ? null : signed.data.signedUrl;
    }

    const job = jobsByAsset.get(asset.id) ?? null;
    return {
      id: asset.id,
      fileName: metadata?.fileName ?? `PDF ${asset.id.slice(0, 8)}`,
      fileSize: metadata?.fileSize ?? null,
      ingestionStatus: deriveIngestionStatus(asset.status, job?.status ?? null),
      errorMessage: safeIngestionError(job?.last_error_code ?? null),
      createdAt: asset.created_at,
      downloadUrl,
      stored,
      sourceStatus: asset.status,
      deletion: deletionsByAsset.get(asset.id) ?? null,
    };
  }));
}
