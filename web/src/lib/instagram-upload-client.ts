import {
  attachInstagramMedia,
  INSTAGRAM_CONTRACT_MAX_BYTES,
  normalizeInstagramPostUrl,
  type InstagramPostContract,
} from "@/lib/instagram";


type Envelope<T> = { data: T | null; error?: { message?: string } };

async function request<T>(body: unknown) {
  const response = await fetch("/api/assets/instagram", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json() as Envelope<T>;
  if (!response.ok || !payload.data) throw new Error(payload.error?.message ?? "Instagram 匯入失敗。");
  return payload.data;
}

function trustedUploadUrl(value: string) {
  const url = new URL(value);
  const local = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  if ((!local && url.protocol !== "https:") || (local && url.protocol !== "http:")) {
    throw new Error("Storage upload URL 不可信任。");
  }
  if (!url.pathname.startsWith("/storage/v1/object/upload/sign/asset-private/")) {
    throw new Error("Storage upload URL 不可信任。");
  }
  return url;
}

async function uploadFile(signedUrl: string, file: File) {
  const body = new FormData();
  body.append("cacheControl", "3600");
  body.append("", file);
  const response = await fetch(trustedUploadUrl(signedUrl), {
    method: "PUT",
    headers: { "x-upsert": "false" },
    body,
    redirect: "error",
  });
  if (!response.ok) throw new Error("Instagram 圖片上傳失敗。");
}

async function cleanup(assetId: string) {
  const response = await fetch(`/api/assets/instagram?asset_id=${encodeURIComponent(assetId)}`, {
    method: "DELETE",
  });
  return response.ok;
}

export async function uploadInstagramPost(sourceUrl: string, contractFile: File, images: File[]) {
  if (!contractFile.size || contractFile.size > INSTAGRAM_CONTRACT_MAX_BYTES) {
    throw new Error("Instagram Contract 必須小於 2 MB。");
  }
  let raw: unknown;
  try {
    raw = JSON.parse(await contractFile.text());
  } catch {
    throw new Error("Instagram Contract 必須是有效 JSON。");
  }
  const contract = await attachInstagramMedia(raw, images);
  const requested = normalizeInstagramPostUrl(sourceUrl);
  if (!requested || requested.canonicalUrl !== contract.source_url) {
    throw new Error("貼上的 Instagram URL 必須和 Contract 來源完全相同。");
  }
  const prepared = await request<{
    asset: { id: string };
    uploads: Array<{ media_id: string; signed_url: string }>;
  }>({ action: "prepare", contract });

  try {
    const byId = new Map<string, File>();
    contract.media.forEach((media, index) => byId.set(media.id, images[index]));
    for (const upload of prepared.uploads) {
      const file = byId.get(upload.media_id);
      if (!file) throw new Error("Instagram 圖片與 Contract 對應失敗。");
      await uploadFile(upload.signed_url, file);
    }
    return await request<{ asset: { id: string }; job: { id: string; status: string } }>({
      action: "complete",
      asset_id: prepared.asset.id,
    });
  } catch (error) {
    if (!await cleanup(prepared.asset.id)) {
      throw new Error("Instagram 上傳失敗，且暫存資料需要人工清理。");
    }
    throw error;
  }
}

export type { InstagramPostContract };
