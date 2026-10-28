import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, uuidSchema, validationError } from "@/lib/api";
import {
  hasInstagramImageSignature,
  instagramReelContractObjectPath,
  instagramReelKeyframePath,
  normalizeInstagramReelUrl,
  validateInstagramReelContract,
} from "@/lib/instagram-reel";
import { PDF_BUCKET } from "@/lib/pdf";

const requestSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("submit_url"), source_url: z.string().trim().min(1).max(2048) }).strict(),
  z.object({ action: z.literal("prepare"), contract: z.unknown() }).strict(),
  z.object({ action: z.literal("complete"), asset_id: uuidSchema }).strict(),
]);
type Authenticated = Exclude<Awaited<ReturnType<typeof authenticateApi>>, { response: Response }>;
const UPLOAD_WINDOW_MS = 2 * 60 * 60 * 1000;

async function cleanup(auth: Authenticated, assetId: string) {
  const { data: asset, error } = await auth.supabase.from("assets")
    .select("id,status,active_revision_id,created_at").eq("id", assetId)
    .eq("source_type", "instagram_reel").maybeSingle();
  const age = asset ? Date.now() - new Date(asset.created_at).getTime() : Number.NaN;
  if (error || !asset || asset.status !== "pending" || asset.active_revision_id
    || !Number.isFinite(age) || age < 0 || age > UPLOAD_WINDOW_MS) return false;
  const contractPath = instagramReelContractObjectPath(auth.user.id, assetId);
  const { data: blob } = await auth.supabase.storage.from(PDF_BUCKET).download(contractPath);
  const paths = [contractPath];
  if (blob) {
    try {
      const contract = await validateInstagramReelContract(JSON.parse(await blob.text()));
      paths.push(...contract.media.filter((item) => item.kind === "keyframe")
        .map((item) => instagramReelKeyframePath(auth.user.id, assetId, item.id)));
    } catch { /* deterministic Contract path is still removed */ }
  }
  if ((await auth.supabase.storage.from(PDF_BUCKET).remove(paths)).error) return false;
  return !(await auth.supabase.from("assets").delete().eq("id", assetId)).error;
}

async function prepare(auth: Authenticated, raw: unknown) {
  let contract;
  try { contract = await validateInstagramReelContract(raw); } catch (error) {
    return jsonError(400, "INVALID_INSTAGRAM_REEL_CONTRACT", error instanceof Error ? error.message : "Reel Contract 不正確。");
  }
  const identity = normalizeInstagramReelUrl(contract.source_url);
  if (!identity) return jsonError(400, "INVALID_INSTAGRAM_REEL_URL", "Instagram Reel 網址不正確。");
  const { data, error } = await auth.supabase.rpc("create_instagram_reel_asset", {
    p_source_url: identity.canonicalUrl, p_external_id: identity.shortcode,
  });
  const result = data as { duplicate?: boolean; asset?: { id?: string } } | null;
  const assetId = result?.asset?.id;
  if (error || typeof assetId !== "string") return jsonError(500, "DATABASE_ERROR", "Reel 匯入無法準備。");
  if (result?.duplicate) return jsonError(409, "ASSET_CONFLICT", "這支 Reel 已存在。", { assetId });
  const path = instagramReelContractObjectPath(auth.user.id, assetId);
  if ((await auth.supabase.storage.from(PDF_BUCKET).upload(path, new TextEncoder().encode(JSON.stringify(contract)), {
    contentType: "application/json", upsert: false,
  })).error) {
    await cleanup(auth, assetId);
    return jsonError(500, "STORAGE_ERROR", "Reel Contract 無法安全保存。");
  }
  const uploads: Array<{ media_id: string; signed_url: string }> = [];
  for (const frame of contract.media.filter((item) => item.kind === "keyframe")) {
    const { data: signed, error: signedError } = await auth.supabase.storage.from(PDF_BUCKET)
      .createSignedUploadUrl(instagramReelKeyframePath(auth.user.id, assetId, frame.id), { upsert: false });
    if (signedError || !signed?.signedUrl) {
      await cleanup(auth, assetId);
      return jsonError(500, "STORAGE_ERROR", "Reel keyframe 上傳無法準備。");
    }
    uploads.push({ media_id: frame.id, signed_url: signed.signedUrl });
  }
  return jsonSuccess({ asset: { id: assetId }, uploads }, 201);
}

async function submitUrl(auth: Authenticated, sourceUrl: string) {
  const identity = normalizeInstagramReelUrl(sourceUrl);
  if (!identity) return jsonError(400, "INVALID_INSTAGRAM_REEL_URL", "請貼上公開 Instagram Reel 網址。");
  const { data, error } = await auth.supabase.rpc("create_instagram_reel_asset", {
    p_source_url: identity.canonicalUrl,
    p_external_id: identity.shortcode,
  });
  const result = data as {
    duplicate?: boolean;
    asset?: { id?: string; status?: string; active_revision_id?: string | null };
  } | null;
  const assetId = result?.asset?.id;
  if (error || typeof assetId !== "string") {
    return jsonError(500, "DATABASE_ERROR", "Reel 匯入無法建立。");
  }
  if (result?.duplicate && result.asset?.active_revision_id) {
    return jsonError(409, "ASSET_CONFLICT", "這支 Reel 已經在 Library 中。", { assetId });
  }
  const { data: job, error: jobError } = await auth.supabase.rpc(
    "enqueue_instagram_reel_cloud_ingestion",
    { p_asset_id: assetId },
  );
  if (jobError || !job) {
    return jsonError(500, "QUEUE_ERROR", "Reel 已建立，但無法排入雲端處理工作。", { assetId });
  }
  return jsonSuccess({ asset: result?.asset, job }, result?.duplicate ? 200 : 201);
}

async function complete(auth: Authenticated, assetId: string) {
  const { data: asset, error } = await auth.supabase.from("assets")
    .select("id,source_type,source_url,source_external_id,status").eq("id", assetId)
    .eq("source_type", "instagram_reel").maybeSingle();
  if (error) return jsonError(500, "DATABASE_ERROR", "Reel 上傳無法驗證。");
  if (!asset) return jsonError(404, "NOT_FOUND", "Reel 上傳不存在。");
  const { data: blob } = await auth.supabase.storage.from(PDF_BUCKET)
    .download(instagramReelContractObjectPath(auth.user.id, assetId));
  if (!blob) return jsonError(400, "UPLOAD_NOT_FOUND", "Reel Contract 尚未上傳。");
  let contract;
  try { contract = await validateInstagramReelContract(JSON.parse(await blob.text())); } catch (parseError) {
    return jsonError(400, "INVALID_INSTAGRAM_REEL_CONTRACT", parseError instanceof Error ? parseError.message : "Reel Contract 不正確。");
  }
  if (contract.source_url !== asset.source_url || contract.source_external_id !== asset.source_external_id) {
    return jsonError(400, "SOURCE_MISMATCH", "Reel Contract 與 Asset 來源不一致。");
  }
  for (const frame of contract.media.filter((item) => item.kind === "keyframe")) {
    const { data: file } = await auth.supabase.storage.from(PDF_BUCKET)
      .download(instagramReelKeyframePath(auth.user.id, assetId, frame.id));
    if (!file || file.size !== frame.size_bytes) return jsonError(400, "INVALID_REEL_KEYFRAME", "Reel keyframe 不完整。");
    const bytes = new Uint8Array(await file.arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (hash !== frame.sha256 || !hasInstagramImageSignature(bytes, frame.mime)) {
      return jsonError(400, "INVALID_REEL_KEYFRAME", "Reel keyframe 與 Contract 不一致。");
    }
  }
  const { data: job, error: jobError } = await auth.supabase.rpc("enqueue_instagram_reel_ingestion", { p_asset_id: assetId });
  if (jobError || !job) return jsonError(500, "QUEUE_ERROR", "Reel 已保存，但無法建立處理工作。");
  return jsonSuccess({ asset, job });
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  let body: unknown;
  try { body = await request.json(); } catch { return jsonError(400, "INVALID_JSON", "Request 必須是有效 JSON。"); }
  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);
  if (parsed.data.action === "submit_url") return submitUrl(auth, parsed.data.source_url);
  return parsed.data.action === "prepare"
    ? prepare(auth, parsed.data.contract)
    : complete(auth, parsed.data.asset_id);
}

export async function DELETE(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const parsed = uuidSchema.safeParse(new URL(request.url).searchParams.get("asset_id"));
  if (!parsed.success) return jsonError(400, "INVALID_ID", "Asset id 必須是 UUID。");
  return await cleanup(auth, parsed.data)
    ? jsonSuccess({ asset_id: parsed.data, cleaned: true })
    : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Reel 暫存資料需要人工清理。");
}
