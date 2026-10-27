import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, uuidSchema, validationError } from "@/lib/api";
import {
  instagramContractObjectPath,
  hasInstagramImageSignature,
  instagramOriginalMediaPath,
  normalizeInstagramPostUrl,
  validateInstagramPostContract,
} from "@/lib/instagram";
import { PDF_BUCKET } from "@/lib/pdf";


const requestSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("prepare"), contract: z.unknown() }).strict(),
  z.object({ action: z.literal("complete"), asset_id: uuidSchema }).strict(),
]);

type Authenticated = Exclude<Awaited<ReturnType<typeof authenticateApi>>, { response: Response }>;
const UPLOAD_WINDOW_MS = 2 * 60 * 60 * 1000;

async function cleanup(auth: Authenticated, assetId: string) {
  const { data: asset, error: assetError } = await auth.supabase
    .from("assets")
    .select("id,status,active_revision_id,created_at")
    .eq("id", assetId)
    .eq("source_type", "instagram_post")
    .maybeSingle();
  const age = asset ? Date.now() - new Date(asset.created_at).getTime() : Number.NaN;
  if (
    assetError || !asset || asset.status !== "pending" || asset.active_revision_id
    || !Number.isFinite(age) || age < 0 || age > UPLOAD_WINDOW_MS
  ) return false;
  const { data: contractFile } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .download(instagramContractObjectPath(auth.user.id, assetId));
  const paths = [instagramContractObjectPath(auth.user.id, assetId)];
  if (contractFile) {
    try {
      const contract = await validateInstagramPostContract(JSON.parse(await contractFile.text()));
      paths.push(...contract.media.map((item) =>
        instagramOriginalMediaPath(auth.user.id, assetId, item.id)
      ));
    } catch {
      // The deterministic Contract object is still removed below.
    }
  }
  const { error: storageError } = await auth.supabase.storage.from(PDF_BUCKET).remove(paths);
  if (storageError) return false;
  const { error } = await auth.supabase.from("assets").delete().eq("id", assetId);
  return !error;
}

async function prepare(auth: Authenticated, rawContract: unknown) {
  let contract;
  try {
    contract = await validateInstagramPostContract(rawContract);
  } catch (error) {
    return jsonError(400, "INVALID_INSTAGRAM_CONTRACT", error instanceof Error ? error.message : "Instagram Contract 不正確。");
  }
  const identity = normalizeInstagramPostUrl(contract.source_url);
  if (!identity) return jsonError(400, "INVALID_INSTAGRAM_URL", "Instagram 貼文網址不正確。");

  const { data, error } = await auth.supabase.rpc("create_instagram_post_asset", {
    p_source_url: identity.canonicalUrl,
    p_external_id: identity.shortcode,
  });
  const result = data as { duplicate?: boolean; asset?: { id?: string } } | null;
  const assetId = result?.asset?.id;
  if (error || typeof assetId !== "string") {
    return jsonError(500, "DATABASE_ERROR", "Instagram 匯入無法準備。");
  }
  if (result?.duplicate) {
    return jsonError(409, "ASSET_CONFLICT", "這則 Instagram 貼文已存在。", { assetId });
  }

  const contractBytes = new TextEncoder().encode(JSON.stringify(contract));
  const { error: contractError } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .upload(instagramContractObjectPath(auth.user.id, assetId), contractBytes, {
      contentType: "application/json",
      upsert: false,
    });
  if (contractError) {
    await cleanup(auth, assetId);
    return jsonError(500, "STORAGE_ERROR", "Instagram Contract 無法安全保存。");
  }

  const uploads: Array<{ media_id: string; signed_url: string }> = [];
  for (const media of contract.media) {
    const path = instagramOriginalMediaPath(auth.user.id, assetId, media.id);
    const { data: signed, error: signedError } = await auth.supabase.storage
      .from(PDF_BUCKET)
      .createSignedUploadUrl(path, { upsert: false });
    if (signedError || !signed?.signedUrl) {
      await cleanup(auth, assetId);
      return jsonError(500, "STORAGE_ERROR", "Instagram 圖片上傳無法準備。");
    }
    uploads.push({ media_id: media.id, signed_url: signed.signedUrl });
  }
  return jsonSuccess({ asset: { id: assetId }, uploads }, 201);
}

async function complete(auth: Authenticated, assetId: string) {
  const { data: asset, error: assetError } = await auth.supabase
    .from("assets")
    .select("id,source_type,source_url,source_external_id,status")
    .eq("id", assetId)
    .eq("source_type", "instagram_post")
    .maybeSingle();
  if (assetError) return jsonError(500, "DATABASE_ERROR", "Instagram 上傳無法驗證。");
  if (!asset) return jsonError(404, "NOT_FOUND", "Instagram 上傳不存在。");

  const { data: contractFile, error: contractError } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .download(instagramContractObjectPath(auth.user.id, assetId));
  if (contractError || !contractFile) return jsonError(400, "UPLOAD_NOT_FOUND", "Instagram Contract 尚未上傳。");
  let contract;
  try {
    contract = await validateInstagramPostContract(JSON.parse(await contractFile.text()));
  } catch (error) {
    return jsonError(400, "INVALID_INSTAGRAM_CONTRACT", error instanceof Error ? error.message : "Instagram Contract 不正確。");
  }
  if (contract.source_url !== asset.source_url || contract.source_external_id !== asset.source_external_id) {
    return jsonError(400, "SOURCE_MISMATCH", "Instagram Contract 與 Asset 來源不一致。");
  }

  for (const media of contract.media) {
    const { data: file, error } = await auth.supabase.storage
      .from(PDF_BUCKET)
      .download(instagramOriginalMediaPath(auth.user.id, assetId, media.id));
    if (error || !file || file.size !== media.size_bytes) {
      return jsonError(400, "INVALID_INSTAGRAM_MEDIA", "Instagram 圖片不完整。");
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (hash !== media.sha256 || !hasInstagramImageSignature(bytes, media.mime)) {
      return jsonError(400, "INVALID_INSTAGRAM_MEDIA", "Instagram 圖片與 Contract 不一致。");
    }
  }

  const { data: job, error: jobError } = await auth.supabase.rpc(
    "enqueue_instagram_post_ingestion",
    { p_asset_id: assetId },
  );
  if (jobError || !job) return jsonError(500, "QUEUE_ERROR", "Instagram 已保存，但無法建立處理工作。");
  return jsonSuccess({ asset, job });
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "Request 必須是有效 JSON。");
  }
  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);
  return parsed.data.action === "prepare"
    ? prepare(auth, parsed.data.contract)
    : complete(auth, parsed.data.asset_id);
}

export async function DELETE(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const parsed = uuidSchema.safeParse(new URL(request.url).searchParams.get("asset_id"));
  if (!parsed.success) return jsonError(400, "INVALID_ID", "Asset id 必須是 UUID。");
  const cleaned = await cleanup(auth, parsed.data);
  return cleaned
    ? jsonSuccess({ asset_id: parsed.data, cleaned: true })
    : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Instagram 暫存資料需要人工清理。");
}
