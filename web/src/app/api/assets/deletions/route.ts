import { z } from "zod";

import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  uuidSchema,
  validationError,
} from "@/lib/api";

const deletionRequestSchema = z.object({
  asset_id: uuidSchema,
  mode: z.enum(["remove_original", "purge_all"]),
  request_key: uuidSchema,
}).strict();

const DELETION_COLUMNS =
  "id,asset_id,asset_id_snapshot,mode,status,error_code,error_message,requested_at,started_at,completed_at,updated_at";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const assetId = new URL(request.url).searchParams.get("asset_id");
  if (assetId && !uuidSchema.safeParse(assetId).success) {
    return jsonError(400, "INVALID_ID", "Asset id must be a UUID.");
  }
  let query = auth.supabase
    .from("asset_deletions")
    .select(DELETION_COLUMNS)
    .order("updated_at", { ascending: false })
    .limit(100);
  if (assetId) query = query.eq("asset_id_snapshot", assetId);
  const { data, error } = await query;
  if (error) return jsonError(500, "DATABASE_ERROR", "Deletion status could not be loaded.");
  return jsonSuccess(data);
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
  const parsed = deletionRequestSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  const { data, error } = await auth.supabase.rpc("request_asset_deletion", {
    p_asset_id: parsed.data.asset_id,
    p_mode: parsed.data.mode,
    p_request_key: parsed.data.request_key,
  });
  if (error || !data) {
    return jsonError(
      409,
      "DELETION_NOT_STARTED",
      "Deletion could not be started for this PDF.",
    );
  }
  const deletionId = typeof data === "object" && data && "id" in data
    ? String(data.id)
    : null;
  if (!deletionId || !uuidSchema.safeParse(deletionId).success) {
    return jsonError(500, "INVALID_RESPONSE", "Deletion status could not be loaded.");
  }
  const { data: deletion, error: deletionError } = await auth.supabase
    .from("asset_deletions")
    .select(DELETION_COLUMNS)
    .eq("id", deletionId)
    .single();
  if (deletionError || !deletion) {
    return jsonError(500, "DATABASE_ERROR", "Deletion status could not be loaded.");
  }
  return jsonSuccess(deletion, 202);
}
