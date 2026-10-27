import { authenticateApi, jsonError, jsonSuccess, uuidSchema } from "@/lib/api";
import { PDF_BUCKET, pdfObjectPath } from "@/lib/pdf";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) return jsonError(400, "INVALID_ID", "Asset id must be a UUID.");
  const { data: asset, error } = await auth.supabase.from("assets")
    .select("id,source_type,source_url,status").eq("id", id).eq("user_id", auth.user.id).maybeSingle();
  if (error) return jsonError(500, "DATABASE_ERROR", "Source could not be loaded.");
  if (!asset) return jsonError(404, "NOT_FOUND", "Source not found.");
  if (["source_removed", "deleting", "deletion_failed"].includes(asset.status)) {
    return jsonError(410, "SOURCE_REMOVED", "原始來源已移除或不可用。");
  }
  if (asset.source_type !== "pdf") {
    if (!asset.source_url) return jsonError(404, "NOT_FOUND", "Source URL is unavailable.");
    return jsonSuccess({ url: asset.source_url });
  }
  const signed = await auth.supabase.storage.from(PDF_BUCKET)
    .createSignedUrl(pdfObjectPath(auth.user.id, id), 300);
  if (signed.error) return jsonError(404, "NOT_FOUND", "Original PDF is unavailable.");
  return jsonSuccess({ url: signed.data.signedUrl });
}
