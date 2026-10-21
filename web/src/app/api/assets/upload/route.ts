import { z } from "zod";

import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  uuidSchema,
  validationError,
} from "@/lib/api";
import {
  encodePdfMetadata,
  hasPdfSignature,
  MAX_PDF_BYTES,
  PDF_BUCKET,
  PDF_MIME_TYPE,
  parsePdfMetadata,
  pdfObjectPath,
} from "@/lib/pdf";

const ASSET_COLUMNS = "id,user_id,source_external_id,status,created_at";
const UPLOAD_WINDOW_MS = 2 * 60 * 60 * 1000;

const uploadRequestSchema = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("prepare"),
    file_name: z.string().trim().min(1).max(180).refine(
      (name) => name.toLowerCase().endsWith(".pdf"),
      "File name must end with .pdf.",
    ),
    file_size: z.number().int().min(5).max(MAX_PDF_BYTES),
    mime_type: z.literal(PDF_MIME_TYPE),
  }).strict(),
  z.object({ action: z.literal("complete"), asset_id: uuidSchema }).strict(),
]);

type Authenticated = Exclude<Awaited<ReturnType<typeof authenticateApi>>, { response: Response }>;

async function ownedPdf(auth: Authenticated, assetId: string) {
  return auth.supabase
    .from("assets")
    .select(ASSET_COLUMNS)
    .eq("id", assetId)
    .eq("source_type", "pdf")
    .maybeSingle();
}

function insideUploadWindow(createdAt: string) {
  const age = Date.now() - new Date(createdAt).getTime();
  return Number.isFinite(age) && age >= 0 && age <= UPLOAD_WINDOW_MS;
}

async function deletePreparedAsset(auth: Authenticated, assetId: string, removeFile: boolean) {
  if (removeFile) {
    const path = pdfObjectPath(auth.user.id, assetId);
    const { error } = await auth.supabase.storage.from(PDF_BUCKET).remove([path]);
    if (error) return false;
  }
  const { error } = await auth.supabase.from("assets").delete().eq("id", assetId);
  return !error;
}

async function prepareUpload(
  auth: Authenticated,
  input: Extract<z.infer<typeof uploadRequestSchema>, { action: "prepare" }>,
) {
  const assetId = crypto.randomUUID();
  const metadata = encodePdfMetadata(assetId, input.file_size, input.file_name);
  const { data: asset, error: assetError } = await auth.supabase
    .from("assets")
    .insert({
      id: assetId,
      user_id: auth.user.id,
      source_type: "pdf",
      source_external_id: metadata,
    })
    .select(ASSET_COLUMNS)
    .single();
  if (assetError || !asset) {
    return jsonError(500, "DATABASE_ERROR", "The PDF upload could not be prepared.");
  }

  const path = pdfObjectPath(auth.user.id, assetId);
  const { data: signed, error: signedError } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .createSignedUploadUrl(path, { upsert: false });
  if (signedError || !signed?.token) {
    const cleaned = await deletePreparedAsset(auth, assetId, false);
    return cleaned
      ? jsonError(500, "STORAGE_ERROR", "The PDF upload could not be prepared.")
      : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Upload preparation cleanup failed.");
  }

  return jsonSuccess({ asset, upload: { signed_url: signed.signedUrl } }, 201);
}

async function completeUpload(auth: Authenticated, assetId: string) {
  const { data: asset, error } = await ownedPdf(auth, assetId);
  const metadata = parsePdfMetadata(asset?.source_external_id ?? null);
  if (error) return jsonError(500, "DATABASE_ERROR", "The PDF upload could not be verified.");
  if (!asset || !metadata || metadata.assetId !== assetId) {
    return jsonError(404, "NOT_FOUND", "PDF upload not found.");
  }

  const path = pdfObjectPath(auth.user.id, assetId);
  const { data: file, error: downloadError } = await auth.supabase.storage
    .from(PDF_BUCKET)
    .download(path);
  if (downloadError || !file) {
    const cleaned = await deletePreparedAsset(auth, assetId, true);
    return cleaned
      ? jsonError(400, "UPLOAD_NOT_FOUND", "The uploaded PDF was not found.")
      : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Incomplete upload cleanup failed.");
  }

  const header = new Uint8Array(await file.slice(0, 5).arrayBuffer());
  if (file.size !== metadata.fileSize || !hasPdfSignature(header)) {
    const cleaned = await deletePreparedAsset(auth, assetId, true);
    return cleaned
      ? jsonError(400, "INVALID_PDF", "The uploaded file is not a valid PDF.")
      : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Invalid upload cleanup failed.");
  }

  const { data: job, error: jobError } = await auth.supabase.rpc(
    "enqueue_pdf_ingestion",
    { p_asset_id: assetId },
  );
  if (jobError || !job) {
    return jsonError(
      500,
      "QUEUE_ERROR",
      "The PDF was saved, but ingestion could not be queued. Retry completion safely.",
    );
  }

  return jsonSuccess({ asset, stored: { size: file.size }, job });
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "The request body must be valid JSON.");
  }
  const parsed = uploadRequestSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  return parsed.data.action === "prepare"
    ? prepareUpload(auth, parsed.data)
    : completeUpload(auth, parsed.data.asset_id);
}

export async function DELETE(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const parsedId = uuidSchema.safeParse(new URL(request.url).searchParams.get("asset_id"));
  if (!parsedId.success) return jsonError(400, "INVALID_ID", "Asset id must be a UUID.");

  const { data: asset, error } = await ownedPdf(auth, parsedId.data);
  if (error) return jsonError(500, "DATABASE_ERROR", "Upload cleanup failed.");
  if (!asset) return jsonError(404, "NOT_FOUND", "PDF upload not found.");
  if (!insideUploadWindow(asset.created_at)) {
    return jsonError(409, "CLEANUP_WINDOW_EXPIRED", "Upload cleanup window expired.");
  }

  const cleaned = await deletePreparedAsset(auth, asset.id, true);
  return cleaned
    ? jsonSuccess({ asset_id: asset.id, cleaned: true })
    : jsonError(500, "UPLOAD_CLEANUP_REQUIRED", "Upload cleanup failed.");
}
