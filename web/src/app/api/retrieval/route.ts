import { authenticateApi, jsonError, jsonSuccess, validationError } from "@/lib/api";
import { parseRetrievalInput, retrieveKnowledge } from "@/lib/retrieval";


export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const search = new URL(request.url).searchParams;
  if (["q", "limit", "offset"].some((name) => search.getAll(name).length > 1)) {
    return jsonError(400, "INVALID_QUERY", "Retrieval parameters are invalid.");
  }
  const parsed = parseRetrievalInput({
    q: search.get("q") ?? "",
    limit: search.get("limit") ?? undefined,
    offset: search.get("offset") ?? undefined,
  });
  if (!parsed.success) return validationError(parsed.error);
  let result;
  try {
    result = await retrieveKnowledge(auth.supabase, parsed.data);
  } catch {
    return jsonError(500, "RETRIEVAL_ERROR", "Knowledge retrieval could not be completed.");
  }
  return jsonSuccess(result.results, 200, {
    query: parsed.data.q,
    limit: parsed.data.limit,
    offset: parsed.data.offset,
    total: result.total,
  });
}
