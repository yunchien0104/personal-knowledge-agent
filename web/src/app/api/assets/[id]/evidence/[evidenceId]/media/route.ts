import { authenticateApi, jsonError, uuidSchema } from "@/lib/api";
import { instagramCitation } from "@/lib/instagram";
import { instagramReelCitation } from "@/lib/instagram-reel";
import { PDF_BUCKET } from "@/lib/pdf";


export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string; evidenceId: string }> },
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const values = await params;
  if (!uuidSchema.safeParse(values.id).success || !uuidSchema.safeParse(values.evidenceId).success) {
    return jsonError(400, "INVALID_ID", "Asset 與 Evidence id 必須是 UUID。");
  }
  const { data: evidence, error } = await auth.supabase
    .from("asset_evidence")
    .select("revision_id,locator_json,content_object_key")
    .eq("id", values.evidenceId)
    .eq("user_id", auth.user.id)
    .maybeSingle();
  if (error) return jsonError(500, "DATABASE_ERROR", "Instagram Evidence 無法讀取。");
  const { data: asset } = await auth.supabase.from("assets")
    .select("source_type,source_url").eq("id", values.id).eq("user_id", auth.user.id).maybeSingle();
  const postCitation = instagramCitation(evidence?.locator_json);
  const reelCitation = instagramReelCitation(evidence?.locator_json, asset?.source_url);
  const citation = postCitation ?? reelCitation;
  if (!evidence || !asset || !citation?.mediaId || !evidence.content_object_key) {
    return jsonError(404, "NOT_FOUND", "Instagram 圖片 Evidence 不存在。");
  }
  const { data: revision, error: revisionError } = await auth.supabase
    .from("asset_revisions")
    .select("id")
    .eq("id", evidence.revision_id)
    .eq("asset_id", values.id)
    .eq("user_id", auth.user.id)
    .maybeSingle();
  const prefix = `${auth.user.id}/${values.id}/revisions/`;
  const suffix = asset.source_type === "instagram_reel"
    ? `/keyframes/${citation.mediaId}`
    : `/images/${citation.mediaId}`;
  if (
    revisionError || !revision
    || !evidence.content_object_key.startsWith(prefix)
    || !evidence.content_object_key.endsWith(suffix)
    || evidence.content_object_key.split("/").includes("..")
  ) return jsonError(404, "NOT_FOUND", "Instagram 圖片 Evidence 不存在。");

  const { data: signed, error: signedError } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .createSignedUrl(evidence.content_object_key, 300);
  if (signedError || !signed?.signedUrl) {
    return jsonError(500, "STORAGE_ERROR", "Instagram 圖片預覽無法建立。");
  }
  return new Response(null, {
    status: 302,
    headers: { Location: signed.signedUrl, "Cache-Control": "private, no-store" },
  });
}
