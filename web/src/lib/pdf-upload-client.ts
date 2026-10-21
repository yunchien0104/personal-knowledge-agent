import {
  type PdfUploadResult,
  validatePdfFile,
} from "@/lib/pdf";

type PrepareResult = {
  asset: { id: string };
  upload: { signed_url: string };
};

type ApiEnvelope<T> = {
  data: T | null;
  error?: { code: string; message: string };
};

async function apiRequest<T>(init: RequestInit) {
  const response = await fetch("/api/assets/upload", init);
  const payload = await response.json() as ApiEnvelope<T>;
  if (!response.ok || !payload.data) {
    throw new Error(payload.error?.message ?? "Upload request failed.");
  }
  return payload.data;
}

async function cleanupPreparedAsset(assetId: string) {
  const response = await fetch(`/api/assets/upload?asset_id=${encodeURIComponent(assetId)}`, {
    method: "DELETE",
  });
  return response.ok;
}

function validateSignedUploadUrl(value: string) {
  const url = new URL(value);
  const local = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  if ((!local && url.protocol !== "https:") || (local && url.protocol !== "http:")) {
    throw new Error("Storage upload URL is not trusted.");
  }
  if (!url.pathname.startsWith("/storage/v1/object/upload/sign/asset-private/")) {
    throw new Error("Storage upload URL is not trusted.");
  }
  return url;
}

async function uploadToSignedUrl(value: string, file: File) {
  const url = validateSignedUploadUrl(value);
  const body = new FormData();
  body.append("cacheControl", "3600");
  body.append("", file);
  const response = await fetch(url, {
    method: "PUT",
    headers: { "x-upsert": "false" },
    body,
    redirect: "error",
  });
  if (!response.ok) throw new Error("PDF 上傳失敗。請稍後再試。");
}

export async function uploadPdf(file: File) {
  const validationMessage = await validatePdfFile(file);
  if (validationMessage) throw new Error(validationMessage);

  const prepared = await apiRequest<PrepareResult>({
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "prepare",
      file_name: file.name,
      file_size: file.size,
      mime_type: file.type,
    }),
  });

  try {
    await uploadToSignedUrl(prepared.upload.signed_url, file);

    return await apiRequest<PdfUploadResult>({
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "complete", asset_id: prepared.asset.id }),
    });
  } catch (error) {
    const cleaned = await cleanupPreparedAsset(prepared.asset.id);
    if (!cleaned) {
      throw new Error("上傳失敗，且暫存資料未能自動清理。請稍後再試。");
    }
    throw error;
  }
}
