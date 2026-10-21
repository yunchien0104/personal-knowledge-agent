import { authenticateApi, jsonError, jsonSuccess, uuidSchema } from "@/lib/api";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) {
    return jsonError(400, "INVALID_ID", "Asset id must be a UUID.");
  }
  const { data, error } = await auth.supabase.rpc("enqueue_source_note_generation", {
    p_asset_id: id,
  });
  if (error || !data) {
    return jsonError(409, "SOURCE_NOT_READY", "A validated PDF Revision is required.");
  }
  return jsonSuccess(data, 202);
}
