import {
  authenticateApi,
  createAssetSchema,
  jsonError,
  jsonSuccess,
  parsePagination,
  validationError,
} from "@/lib/api";

const ASSET_COLUMNS =
  "id,user_id,source_type,source_url,source_external_id,status,active_revision_id,created_at,updated_at";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const pagination = parsePagination(request.url);
  if ("error" in pagination) return pagination.error;

  const { data, error, count } = await auth.supabase
    .from("assets")
    .select(ASSET_COLUMNS, { count: "exact" })
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .range(pagination.offset, pagination.offset + pagination.limit - 1);

  if (error) {
    return jsonError(500, "DATABASE_ERROR", "Assets could not be loaded.");
  }
  return jsonSuccess(data, 200, { ...pagination, total: count ?? 0 });
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

  const parsed = createAssetSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  const { data, error } = await auth.supabase
    .from("assets")
    .insert({ ...parsed.data, user_id: auth.user.id })
    .select(ASSET_COLUMNS)
    .single();

  if (error?.code === "23505") {
    return jsonError(409, "ASSET_CONFLICT", "This asset already exists.");
  }
  if (error) {
    return jsonError(500, "DATABASE_ERROR", "The asset could not be created.");
  }
  return jsonSuccess(data, 201);
}
