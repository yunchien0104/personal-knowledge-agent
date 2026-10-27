import { authenticateApi, jsonError, jsonSuccess, uuidSchema } from "@/lib/api";
import { loadSourceNoteWorkspace } from "@/lib/source-notes";

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
  try {
    const workspace = await loadSourceNoteWorkspace(auth.supabase, auth.user.id, id);
    return workspace
      ? jsonSuccess(workspace)
      : jsonError(404, "ASSET_NOT_FOUND", "Source Note Asset could not be found.");
  } catch {
    return jsonError(500, "DATABASE_ERROR", "Source Note could not be loaded.");
  }
}

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
  const { data: asset, error: assetError } = await auth.supabase
    .from("assets")
    .select("source_type")
    .eq("id", id)
    .maybeSingle();
  if (assetError || !asset) {
    return jsonError(404, "ASSET_NOT_FOUND", "Source Note Asset could not be found.");
  }
  const rpc = asset.source_type === "instagram_post"
    ? "enqueue_instagram_post_source_note_generation"
    : asset.source_type === "instagram_reel"
      ? "enqueue_instagram_reel_source_note_generation"
      : "enqueue_source_note_generation";
  const { data, error } = await auth.supabase.rpc(rpc, {
    p_asset_id: id,
  });
  if (error || !data) {
    return jsonError(409, "SOURCE_NOT_READY", "A validated source Revision is required.");
  }
  return jsonSuccess(data, 202);
}
