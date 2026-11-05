import { authenticateApi, jsonError, jsonSuccess, uuidSchema } from "@/lib/api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ noteId: string }> }
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { noteId } = await context.params;
  if (!uuidSchema.safeParse(noteId).success) {
    return jsonError(400, "INVALID_ID", "Note id 必須為有效的 UUID");
  }

  const { data, error } = await auth.supabase
    .from("personal_note_versions")
    .select("id, note_id, body_revision, title, content_json, plain_text, reason, created_at")
    .eq("note_id", noteId)
    .order("body_revision", { ascending: false });

  if (error) {
    return jsonError(500, "DATABASE_ERROR", "無法讀取版本歷史", error.message);
  }

  return jsonSuccess({ versions: data || [] });
}
