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
    .from("personal_note_citations")
    .select("id, note_id, import_id, source_label, source_url, supporting_quote, validity_status, locator_json, created_at")
    .eq("note_id", noteId)
    .order("created_at", { ascending: true });

  if (error) {
    return jsonError(500, "DATABASE_ERROR", "無法讀取引用資料", error.message);
  }

  return jsonSuccess({ citations: data || [] });
}
