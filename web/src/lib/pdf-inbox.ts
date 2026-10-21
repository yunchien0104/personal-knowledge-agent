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
  if (assetIds.length) {
    const { data: jobs, error: jobsError } = await supabase
      .from("jobs")
      .select("asset_id,status,last_error_code,created_at")
      .in("asset_id", assetIds)
      .order("created_at", { ascending: false });
    if (jobsError) throw new Error("PDF ingestion status could not be loaded.");
    for (const job of jobs ?? []) {
      if (!jobsByAsset.has(job.asset_id)) jobsByAsset.set(job.asset_id, job);
    }
  }

  return Promise.all((data ?? []).map(async (asset): Promise<PdfInboxItem> => {
    const metadata = parsePdfMetadata(asset.source_external_id);
    const path = pdfObjectPath(userId, asset.id);
    const prefix = path.slice(0, path.lastIndexOf("/"));
    const { data: objects, error: listError } = await supabase.storage
      .from(PDF_BUCKET)
      .list(prefix, { limit: 2, search: "source.pdf" });
    const stored = !listError && (objects ?? []).some((object) => object.name === "source.pdf");

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
    };
  }));
}
