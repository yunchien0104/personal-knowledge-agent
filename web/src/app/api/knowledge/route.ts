import { authenticateApi, jsonError, jsonSuccess, parsePagination, uuidSchema } from "@/lib/api";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const pagination = parsePagination(request.url);
  if ("error" in pagination) return pagination.error;

  const params = new URL(request.url).searchParams;
  const topicId = params.get("topicId");
  const unclassified = params.get("unclassified") === "true";
  if (topicId && !uuidSchema.safeParse(topicId).success) {
    return jsonError(400, "INVALID_TOPIC", "Topic id must be a UUID.");
  }
  if (topicId && unclassified) {
    return jsonError(400, "INVALID_FILTER", "Topic and unclassified filters cannot be combined.");
  }

  const { data, error } = await auth.supabase.rpc("list_knowledge_notes", {
    p_search: params.get("q")?.trim() || null,
    p_topic_id: topicId,
    p_unclassified: unclassified,
    p_limit: pagination.limit,
    p_offset: pagination.offset,
  });
  if (error) return jsonError(500, "DATABASE_ERROR", "Knowledge notes could not be loaded.");
  const rows = (data ?? []) as Array<Record<string, unknown> & { total_count: number | string }>;
  const total = rows.length ? Number(rows[0].total_count) : 0;
  const sourceAssetIds = [...new Set(rows.flatMap((row) => (
    typeof row.source_asset_id === "string" ? [row.source_asset_id] : []
  )))];
  const sourceTypes = new Map<string, string>();
  if (sourceAssetIds.length) {
    const { data: assets, error: assetsError } = await auth.supabase
      .from("assets")
      .select("id,source_type")
      .in("id", sourceAssetIds);
    if (assetsError) return jsonError(500, "DATABASE_ERROR", "Knowledge note sources could not be loaded.");
    for (const asset of assets ?? []) sourceTypes.set(asset.id, asset.source_type);
  }
  return jsonSuccess(rows.map((row) => {
    const sourceAssetId = typeof row.source_asset_id === "string" ? row.source_asset_id : null;
    const result: Record<string, unknown> = {
      ...row,
      source_type: sourceAssetId ? sourceTypes.get(sourceAssetId) ?? null : null,
    };
    delete result.total_count;
    return result;
  }), 200, {
    ...pagination,
    total,
  });
}
