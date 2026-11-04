import { authenticateApi, jsonError, jsonSuccess, validationError } from "@/lib/api";
import { referenceSearchSchema, referenceSourceSchema } from "@/lib/reference-sources";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const params = new URL(request.url).searchParams;
  const parsed = referenceSearchSchema.safeParse({
    category: params.get("category") ?? undefined,
    sourceType: params.get("sourceType") ?? undefined,
    query: params.get("query") ?? undefined,
    currentNoteId: params.get("currentNoteId") || undefined,
    limit: params.get("limit") ?? undefined,
    offset: params.get("offset") ?? undefined,
  });
  if (!parsed.success) return validationError(parsed.error);

  const input = parsed.data;
  const { data, error } = await auth.supabase.rpc("list_reference_sources", {
    p_category: input.category,
    p_source_type: input.sourceType === "all" ? null : input.sourceType,
    p_search: input.query || null,
    p_current_note_id: input.currentNoteId ?? null,
    p_limit: input.limit,
    p_offset: input.offset,
  });

  if (error) {
    return jsonError(500, "REFERENCE_SEARCH_FAILED", "無法載入引用來源", error.message);
  }

  const rows = referenceSourceSchema.array().safeParse(data ?? []);
  if (!rows.success) {
    return jsonError(500, "INVALID_REFERENCE_DATA", "引用來源資料格式不正確");
  }

  const total = rows.data[0]?.total_count ?? 0;
  return jsonSuccess(rows.data, 200, {
    category: input.category,
    sourceType: input.sourceType,
    limit: input.limit,
    offset: input.offset,
    total,
    hasMore: input.offset + rows.data.length < total,
    ordering: "created_at.desc,id.desc",
  });
}
