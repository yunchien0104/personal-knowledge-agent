import { authenticateApi, jsonError, jsonSuccess, uuidSchema } from "@/lib/api";

const ASSET_COLUMNS =
  "id,user_id,source_type,source_url,source_external_id,status,active_revision_id,created_at,updated_at";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) {
    return jsonError(400, "INVALID_ID", "Asset id must be a UUID.");
  }

  const { data, error } = await auth.supabase
    .from("assets")
    .select(ASSET_COLUMNS)
    .eq("id", id)
    .maybeSingle();

  if (error) return jsonError(500, "DATABASE_ERROR", "The asset could not be loaded.");
  if (!data) return jsonError(404, "NOT_FOUND", "Asset not found.");
  return jsonSuccess(data);
}
