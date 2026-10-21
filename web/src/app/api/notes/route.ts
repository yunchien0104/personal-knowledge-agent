import { authenticateApi, jsonError, jsonSuccess, parsePagination } from "@/lib/api";

const NOTE_COLUMNS =
  "id,user_id,note_type,title,body,body_revision,publication_state,created_at,updated_at";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const pagination = parsePagination(request.url);
  if ("error" in pagination) return pagination.error;

  const { data, error, count } = await auth.supabase
    .from("notes")
    .select(NOTE_COLUMNS, { count: "exact" })
    .order("updated_at", { ascending: false })
    .order("id", { ascending: false })
    .range(pagination.offset, pagination.offset + pagination.limit - 1);

  if (error) return jsonError(500, "DATABASE_ERROR", "Notes could not be loaded.");
  return jsonSuccess(data, 200, { ...pagination, total: count ?? 0 });
}
