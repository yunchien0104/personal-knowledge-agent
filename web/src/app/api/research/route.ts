import { authenticateApi, jsonError, jsonSuccess, validationError, uuidSchema } from "@/lib/api";
import { PDF_BUCKET, pdfObjectPath } from "@/lib/pdf";
import {
  getResearchRun,
  listResearchRuns,
  researchRequestSchema,
  startResearch,
} from "@/lib/research";
import { threadsCitationUrl } from "@/lib/threads";


export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "The request body must be valid JSON.");
  }
  const parsed = researchRequestSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);
  try {
    return jsonSuccess(await startResearch(auth.supabase, parsed.data), 202);
  } catch {
    return jsonError(409, "RESEARCH_NOT_STARTED", "Research could not be started.");
  }
}

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const runId = new URL(request.url).searchParams.get("id");
  try {
    if (!runId) return jsonSuccess(await listResearchRuns(auth.supabase));
    const parsed = uuidSchema.safeParse(runId);
    if (!parsed.success) return jsonError(400, "INVALID_ID", "Research ID is invalid.");
    const run = await getResearchRun(auth.supabase, parsed.data);
    if (!run) return jsonError(404, "NOT_FOUND", "Research run was not found.");
    const pdfUrls = new Map<string, string>();
    const citationUrls = new Map<string, string>();
    for (const citation of run.citations) {
      const sourceUrl = threadsCitationUrl(citation.locator);
      if (sourceUrl) citationUrls.set(citation.id, sourceUrl);
    }
    const verifiableAssetIds = run.citations
      .filter((item) => item.validityStatus === "valid" && !citationUrls.has(item.id))
      .map((item) => item.assetId);
    const uniqueAssetIds = [...new Set(verifiableAssetIds)];
    if (uniqueAssetIds.length) {
      const { data: assets, error: assetError } = await auth.supabase
        .from("assets")
        .select("id,source_type")
        .in("id", uniqueAssetIds);
      if (assetError) throw new Error("Research source URLs could not be loaded.");
      await Promise.all((assets ?? []).map(async (asset) => {
        if (asset.source_type === "pdf") {
          const signed = await auth.supabase.storage
            .from(PDF_BUCKET)
            .createSignedUrl(pdfObjectPath(auth.user.id, asset.id), 300);
          if (!signed.error) pdfUrls.set(asset.id, signed.data.signedUrl);
        }
      }));
    }
    return jsonSuccess({
      ...run,
      citations: run.citations.map((citation) => ({
        ...citation,
        sourceUrl: citationUrls.get(citation.id) ?? pdfUrls.get(citation.assetId),
      })),
    });
  } catch {
    return jsonError(500, "INTERNAL_ERROR", "Research could not be loaded.");
  }
}
