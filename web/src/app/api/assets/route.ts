import {
  authenticateApi,
  createAssetSchema,
  jsonError,
  jsonSuccess,
  parsePagination,
  validationError,
} from "@/lib/api";
import { normalizeThreadsUrl, threadsEnqueueResultSchema } from "@/lib/threads";

const ASSET_COLUMNS =
  "id,user_id,source_type,source_url,source_external_id,status,active_revision_id,created_at,updated_at";

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const pagination = parsePagination(request.url);
  if ("error" in pagination) return pagination.error;

  const includeRemoved = new URL(request.url).searchParams.get("includeRemoved") === "true";
  let query = auth.supabase
    .from("assets")
    .select(ASSET_COLUMNS, { count: "exact" })
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .range(pagination.offset, pagination.offset + pagination.limit - 1);
  if (!includeRemoved) query = query.neq("status", "source_removed");
  const { data, error, count } = await query;

  if (error) {
    return jsonError(500, "DATABASE_ERROR", "Assets could not be loaded.");
  }
  const assetIds = (data ?? []).map((asset) => asset.id);
  if (!assetIds.length) {
    return jsonSuccess(data, 200, { ...pagination, total: count ?? 0 });
  }
  const { data: jobs, error: jobsError } = await auth.supabase
    .from("jobs")
    .select("id,asset_id,job_type,status,last_error_code,last_error_message,created_at")
    .in("asset_id", assetIds)
    .in("job_type", ["ingest_pdf", "ingest_threads"])
    .order("created_at", { ascending: false });
  if (jobsError) {
    return jsonError(500, "DATABASE_ERROR", "Asset processing status could not be loaded.");
  }
  const latestJobs = new Map<string, NonNullable<typeof jobs>[number]>();
  for (const job of jobs ?? []) {
    if (job.asset_id && !latestJobs.has(job.asset_id)) latestJobs.set(job.asset_id, job);
  }
  return jsonSuccess(
    data?.map((asset) => ({ ...asset, ingestion_job: latestJobs.get(asset.id) ?? null })),
    200,
    { ...pagination, total: count ?? 0 },
  );
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

  if (parsed.data.source_type === "threads") {
    const target = parsed.data.source_url
      ? normalizeThreadsUrl(parsed.data.source_url)
      : null;
    if (!target) {
      return jsonError(
        400,
        "INVALID_THREADS_URL",
        "請貼上公開的 Threads 單篇貼文網址。",
      );
    }
    const { data, error } = await auth.supabase.rpc(
      "create_threads_asset_and_enqueue",
      { p_source_url: target.canonicalUrl, p_external_id: target.postId },
    );
    if (error) {
      return jsonError(500, "DATABASE_ERROR", "Threads 匯入工作無法建立。");
    }
    const result = threadsEnqueueResultSchema.safeParse(data);
    if (!result.success) {
      return jsonError(500, "DATABASE_ERROR", "Threads 匯入工作回傳格式不正確。");
    }
    if (result.data.duplicate) {
      return jsonError(
        409,
        "ASSET_CONFLICT",
        "這則 Threads 貼文已在知識庫中。",
        { assetId: result.data.asset.id },
      );
    }
    return jsonSuccess(result.data, 202);
  }

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
